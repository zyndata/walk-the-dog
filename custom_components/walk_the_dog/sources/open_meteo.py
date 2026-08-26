"""Open-Meteo adapter: ICON-EU and KNMI HARMONIE AROME from ONE HTTP request.

Requests the five sample points and both models together and returns two
`SourceSeries` (docs/ARCHITECTURE.md § Frame sampling strategy). A live 5-point,
2-model, 12-hour request measures 419 bytes gzipped.

Response shape (measured 2026-08-25): with several coordinates the API returns a
JSON *list*, one object per coordinate; with several models each variable key is
suffixed with the model id (`precipitation_icon_eu`,
`precipitation_knmi_harmonie_arome_europe`). This adapter always requests both
models, so the keys are always suffixed; a model absent from the response yields
no series for it and its source is reported failed rather than silently dry.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from ..const import SOURCE_ICON_EU, SOURCE_KNMI
from .base import (
    CELL_KM,
    RELIABILITY,
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

URL = "https://api.open-meteo.com/v1/forecast"

#: Open-Meteo model id per source id.
MODEL_IDS: dict[str, str] = {
    SOURCE_ICON_EU: "icon_eu",
    SOURCE_KNMI: "knmi_harmonie_arome_europe",
}

STEP_S = 3600  # hourly series; the 15-minutely one is interpolated and lossy
FORECAST_HOURS = 12

#: Fetched every 3rd 10-minute cycle: the freshest model re-runs hourly, so a
#: 10-minute cadence would be at least two thirds identical responses
#: (docs/ARCHITECTURE.md § Coordinator scheduling).
MIN_INTERVAL_S = 30 * 60

#: Ceiling from the docs/DATA_SOURCES.md request budget (the cadence above uses 2).
MAX_REQUESTS_PER_HOUR = 6

_TIMEOUT_S = 20


class OpenMeteoAdapter:
    """Adapter producing the `icon_eu` and `knmi` series."""

    source_ids = (SOURCE_ICON_EU, SOURCE_KNMI)

    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent
        self._budget = RequestBudget(limit=MAX_REQUESTS_PER_HOUR)
        self._backoff = Backoff()
        self._last: FetchResult | None = None
        self._last_fetch_at: datetime | None = None

    # --- adapter protocol ---------------------------------------------------

    @property
    def budget(self) -> RequestBudget:
        """This adapter's rolling hourly request budget, for the registry to total."""
        return self._budget

    def should_fetch(self, now: datetime) -> bool:
        """True only every 30 minutes, and never while a backoff is armed."""
        if not self._backoff.ready(now):
            return False
        if self._last_fetch_at is None:
            return True
        return now - self._last_fetch_at >= timedelta(seconds=MIN_INTERVAL_S)

    def cached(self, now: datetime) -> FetchResult:
        """Re-present the last successful result for the two skipped cycles."""
        if self._last is None:
            return FetchResult(
                statuses=tuple(
                    SourceStatus(sid, STATE_FAILED, detail="no data yet") for sid in self.source_ids
                )
            )
        return restate(self._last, now)

    async def fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        """One request for both models across all five sample points."""
        try:
            result = await self._fetch(session, geometry, now)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # provider-side failures must never break the cycle
            _LOGGER.debug("Open-Meteo fetch failed: %s", err)
            self._backoff.record_failure(now)
            return self._failed(now, str(err))
        self._backoff.record_success()
        self._last = result
        self._last_fetch_at = now
        return result

    # --- implementation -----------------------------------------------------

    def _failed(self, now: datetime, detail: str) -> FetchResult:
        if self._last is not None:
            stated = restate(self._last, now)
            return FetchResult(
                series=stated.series,
                statuses=tuple(
                    SourceStatus(
                        s.source_id,
                        s.state,
                        age_s=s.age_s,
                        contributed=s.contributed,
                        detail=f"reusing cached series: {detail}",
                    )
                    for s in stated.statuses
                ),
            )
        return FetchResult(
            statuses=tuple(
                SourceStatus(sid, STATE_FAILED, detail=detail) for sid in self.source_ids
            )
        )

    async def _fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        if not self._budget.consume(now):
            raise RuntimeError("hourly request budget exhausted")
        points = geometry.sample_points()
        params = {
            "latitude": ",".join(f"{lat}" for lat, _ in points),
            "longitude": ",".join(f"{lon}" for _, lon in points),
            "hourly": "precipitation",
            "models": ",".join(MODEL_IDS[sid] for sid in self.source_ids),
            "forecast_hours": str(FORECAST_HOURS),
            "timeformat": "unixtime",
            "timezone": "UTC",
        }
        async with session.get(
            URL,
            params=params,
            headers={"User-Agent": self._user_agent, "Accept-Encoding": "gzip"},
            timeout=ClientTimeout(total=_TIMEOUT_S),
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

        series = parse_forecast(payload, now)
        if not series:
            raise ValueError("no usable model series in the response")

        by_id = {s.source_id: s for s in series}
        statuses = []
        for source_id in self.source_ids:
            found = by_id.get(source_id)
            if found is None:
                statuses.append(
                    SourceStatus(source_id, STATE_FAILED, detail="model missing from response")
                )
                continue
            stale = found.is_stale(now)
            statuses.append(
                SourceStatus(
                    source_id,
                    STATE_STALE if stale else STATE_OK,
                    age_s=found.age_s(now),
                    contributed=not stale,
                )
            )
        return FetchResult(series=tuple(series), statuses=tuple(statuses))


def parse_forecast(payload: Any, fetched_at: datetime) -> list[SourceSeries]:
    """Turn an Open-Meteo response into one `SourceSeries` per model.

    Each hourly value is millimetres accumulated over the step, so on an hourly
    series mm/h equals the value directly. Across the five sample points the
    intensity is the **max**: the fields are smooth, there is no speckle to reject,
    and the conservative choice is the right one for "will my walk get wet".

    `issued_at` is the fetch time: /v1/forecast carries no model-run timestamp
    (checked 2026-08-25), so upstream run age cannot be measured. Freshness then
    tracks our own fetch age, which is exactly what degrades when Open-Meteo stops
    answering — the failure this guards against. Recorded in STATE.md, phase 3.
    """
    entries = payload if isinstance(payload, list) else [payload]
    per_model: dict[str, dict[datetime, float]] = {sid: {} for sid in MODEL_IDS}

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hourly = entry.get("hourly")
        if not isinstance(hourly, dict):
            continue
        times = hourly.get("time")
        if not isinstance(times, list):
            continue
        for source_id, model_id in MODEL_IDS.items():
            values = hourly.get(f"precipitation_{model_id}")
            if not isinstance(values, list):
                continue
            slots = per_model[source_id]
            for raw_time, raw_value in zip(times, values, strict=False):
                if raw_value is None or not isinstance(raw_time, int | float):
                    continue
                slot = datetime.fromtimestamp(int(raw_time), tz=UTC)
                value = max(0.0, float(raw_value))
                # max across the five sample points
                if value > slots.get(slot, -1.0):
                    slots[slot] = value

    result = []
    for source_id, slots in per_model.items():
        if not slots:
            continue
        result.append(
            SourceSeries(
                source_id=source_id,
                issued_at=fetched_at,
                fetched_at=fetched_at,
                step_s=STEP_S,
                slots=tuple(sorted(slots.items())),
                cell_km=CELL_KM[source_id],
                reliability=RELIABILITY[source_id],
            )
        )
    return result


__all__ = ["MODEL_IDS", "OpenMeteoAdapter", "parse_forecast"]
