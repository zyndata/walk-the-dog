"""MET Norway Locationforecast 2.0 adapter — provider-level failover only.

Polled only while Open-Meteo has failed (docs/DATA_SOURCES.md § Fallback strategy):
polling it alongside Open-Meteo would add a correlated vote (0.61 with KNMI, 0.71
with ECMWF-IFS) for no gain and spend a request budget its terms ask us to conserve.

Samples the **centre point only** — its ~10 km cell already covers any permitted
alert radius, so extra coordinates would return the same cell at four times the cost.

Terms obligations honoured here: a mandatory identifying `User-Agent` with contact
information, `If-Modified-Since` on every repeat request, no polling before the
`Expires` header, and never more often than every 10 minutes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from ..const import SOURCE_METNO
from .base import (
    CELL_KM,
    RELIABILITY,
    STATE_DISABLED,
    STATE_FAILED,
    STATE_OK,
    STATE_STALE,
    Backoff,
    FetchResult,
    RequestBudget,
    SampleGeometry,
    SourceSeries,
    SourceStatus,
    restate,
)

_LOGGER = logging.getLogger(__name__)

URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

STEP_S = 3600
FORECAST_HOURS = 12

#: The terms' hard floor between polls; `Expires` is typically 30 minutes ahead.
MIN_INTERVAL_S = 10 * 60

#: Ceiling from the docs/DATA_SOURCES.md request budget.
MAX_REQUESTS_PER_HOUR = 2

_TIMEOUT_S = 20
_HTTP_NOT_MODIFIED = 304


class MetNorwayAdapter:
    """Adapter for MET Norway, dormant until the registry enables it."""

    source_ids = (SOURCE_METNO,)

    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent
        self._budget = RequestBudget(limit=MAX_REQUESTS_PER_HOUR)
        self._backoff = Backoff()
        self._last: FetchResult | None = None
        self._last_fetch_at: datetime | None = None
        self._last_modified: str | None = None
        self._expires: datetime | None = None
        self.enabled = False

    # --- adapter protocol ---------------------------------------------------

    @property
    def budget(self) -> RequestBudget:
        """This adapter's rolling hourly request budget, for the registry to total."""
        return self._budget

    def should_fetch(self, now: datetime) -> bool:
        """Respect the enable flag, the backoff, `Expires`, and the 10-minute floor."""
        if not self.enabled or not self._backoff.ready(now):
            return False
        if self._last_fetch_at is not None:
            if now - self._last_fetch_at < timedelta(seconds=MIN_INTERVAL_S):
                return False
            if self._expires is not None and now < self._expires:
                return False
        return True

    def cached(self, now: datetime) -> FetchResult:
        """Re-present the last result, or report `disabled` while dormant."""
        if not self.enabled:
            return FetchResult(statuses=(SourceStatus(SOURCE_METNO, STATE_DISABLED),))
        if self._last is None:
            return FetchResult(
                statuses=(SourceStatus(SOURCE_METNO, STATE_FAILED, detail="no data yet"),)
            )
        return restate(self._last, now)

    async def fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        """Fetch the centre-point forecast, or reuse the cached one on a 304."""
        if not self.enabled:
            return FetchResult(statuses=(SourceStatus(SOURCE_METNO, STATE_DISABLED),))
        try:
            result = await self._fetch(session, geometry, now)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # provider-side failures must never break the cycle
            _LOGGER.debug("MET Norway fetch failed: %s", err)
            self._backoff.record_failure(now)
            return self._failed(now, str(err))
        self._backoff.record_success()
        self._last = result
        self._last_fetch_at = now
        return result

    # --- implementation -----------------------------------------------------

    def _failed(self, now: datetime, detail: str) -> FetchResult:
        if self._last is not None:
            return replace(restate(self._last, now), failed=True)
        return FetchResult(
            statuses=(SourceStatus(SOURCE_METNO, STATE_FAILED, detail=detail),), failed=True
        )

    async def _fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        if not self._budget.consume(now):
            raise RuntimeError("hourly request budget exhausted")
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip"}
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified

        async with session.get(
            URL,
            params={"lat": f"{geometry.latitude:.4f}", "lon": f"{geometry.longitude:.4f}"},
            headers=headers,
            timeout=ClientTimeout(total=_TIMEOUT_S),
        ) as response:
            self._expires = _http_date(response.headers.get("Expires"))
            if response.status == _HTTP_NOT_MODIFIED:
                # Unchanged upstream: no body was sent, and the cached series is
                # still the current one. Its age keeps growing from `issued_at`.
                if self._last is None:
                    raise RuntimeError("304 with no cached series")
                _LOGGER.debug("MET Norway unchanged (304)")
                return restate(self._last, now)
            response.raise_for_status()
            self._last_modified = response.headers.get("Last-Modified")
            payload = await response.json(content_type=None)

        series = parse_compact(payload, now)
        stale = series.is_stale(now)
        return FetchResult(
            series=(series,),
            statuses=(
                SourceStatus(
                    SOURCE_METNO,
                    STATE_STALE if stale else STATE_OK,
                    age_s=series.age_s(now),
                    contributed=not stale,
                ),
            ),
        )


def parse_compact(payload: Any, fetched_at: datetime) -> SourceSeries:
    """Turn a Locationforecast compact response into a `SourceSeries`.

    `next_1_hours.details.precipitation_amount` is millimetres over the coming hour,
    so mm/h equals the value directly. Steps without a `next_1_hours` block are
    beyond the hourly part of the forecast and are skipped rather than interpolated.
    `issued_at` comes from `properties.meta.updated_at` — the real upstream run time.
    """
    if not isinstance(payload, dict):
        raise ValueError("locationforecast response is not an object")
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("locationforecast response has no properties")

    meta = properties.get("meta")
    issued_at = fetched_at
    if isinstance(meta, dict) and isinstance(meta.get("updated_at"), str):
        issued_at = _iso_utc(meta["updated_at"]) or fetched_at

    timeseries = properties.get("timeseries")
    if not isinstance(timeseries, list):
        raise ValueError("locationforecast response has no timeseries")

    slots: list[tuple[datetime, float]] = []
    liquid: list[tuple[datetime, float]] = []
    instants: dict[datetime, float] = {}
    for step in timeseries:
        parsed = _parse_step(step)
        if parsed is None:
            continue
        slot, amount, rain, temperature = parsed
        if temperature is not None:
            instants[slot] = temperature
        if len(slots) >= FORECAST_HOURS:
            # One instant past the last slot is still wanted: it is that slot's end.
            break
        if amount is None:
            continue
        slots.append((slot, amount))
        if rain is not None:
            liquid.append((slot, rain))

    if not slots:
        raise ValueError("locationforecast response has no hourly precipitation")

    return SourceSeries(
        source_id=SOURCE_METNO,
        issued_at=issued_at,
        fetched_at=fetched_at,
        step_s=STEP_S,
        slots=tuple(sorted(slots)),
        cell_km=CELL_KM[SOURCE_METNO],
        reliability=RELIABILITY[SOURCE_METNO],
        liquid=tuple(sorted(liquid)),
        temperature_c=_slot_temperatures(slots, instants),
    )


def _parse_step(
    step: Any,
) -> tuple[datetime, float | None, float | None, float | None] | None:
    """One timeseries step: its time, the hour's amount and rain, and the temperature.

    None for a step without a usable time. The rain is the amount unless the hour's
    symbol forecasts snow alone, and unknown when there is no symbol to go by.
    """
    if not isinstance(step, dict) or not isinstance(step.get("time"), str):
        return None
    slot = _iso_utc(step["time"])
    if slot is None:
        return None
    data = step.get("data")
    temperature = _dig(data, "instant", "details", "air_temperature")
    amount = _dig(data, "next_1_hours", "details", "precipitation_amount")
    symbol = _dig(data, "next_1_hours", "summary", "symbol_code")
    value = max(0.0, float(amount)) if isinstance(amount, int | float) else None
    rain = None
    if value is not None and isinstance(symbol, str):
        rain = 0.0 if _is_snow(symbol) else value
    return (
        slot,
        value,
        rain,
        float(temperature) if isinstance(temperature, int | float) else None,
    )


def _slot_temperatures(
    slots: list[tuple[datetime, float]], instants: dict[datetime, float]
) -> tuple[tuple[datetime, float], ...]:
    """The warmer of the two instants each hourly slot spans, where either is known."""
    step = timedelta(seconds=STEP_S)
    temperatures = []
    for slot, _ in slots:
        ends = [instants[at] for at in (slot, slot + step) if at in instants]
        if ends:
            temperatures.append((slot, max(ends)))
    return tuple(temperatures)


def _dig(data: Any, *keys: str) -> Any:
    """`data[k1][k2]...`, or None as soon as a level is missing or not an object."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _is_snow(symbol: str) -> bool:
    """Whether a weather symbol forecasts snow alone — `lightsnow`, `snowshowers_day`.

    Sleet is rain and snow together, and soaks a dog like rain, so it is not snow
    here; nor is anything that does not name snow at all.
    """
    return "snow" in symbol and "sleet" not in symbol


def _iso_utc(value: str) -> datetime | None:
    """Parse MET Norway's `2026-08-25T06:00:00Z` timestamps as aware UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def _http_date(value: str | None) -> datetime | None:
    """Parse an HTTP date header, ignoring anything malformed."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["MetNorwayAdapter", "parse_compact"]
