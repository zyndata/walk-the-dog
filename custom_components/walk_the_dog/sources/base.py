"""Common source-adapter interface and the normalized structures it returns.

Every adapter turns a provider-specific response into `SourceSeries` — a sorted
series of (slot start UTC, intensity mm/h) pairs on the common scale from
docs/DATA_SOURCES.md — plus a `SourceStatus` per source id it speaks for. The
engine consumes only these two structures and never learns which provider a
series came from beyond its `source_id`.

Adapters do I/O; they never import `engine` (docs/ARCHITECTURE.md § Module layout).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from ..const import (
    SOURCE_CHMI,
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
    SOURCE_METNO,
)

if TYPE_CHECKING:
    from aiohttp import ClientSession

# --- Source metadata (docs/ARCHITECTURE.md § Consensus scoring, docs/DATA_SOURCES.md) ---

#: Static per-source reliability weight used by the consensus vote.
RELIABILITY: dict[str, float] = {
    SOURCE_LIBREWXR: 1.00,
    SOURCE_KNMI: 0.90,
    #: Radar extrapolation like LibreWXR. Its level->dBZ calibration is CHMI's own
    #: published scale, verified live (docs/DATA_SOURCES.md § CHMI), so the only
    #: remaining discount is quantisation: CHMI publishes 15 steps of 4 dBZ where
    #: LibreWXR's grey ramp carries 1 dBZ, and at the light end one step is the
    #: difference between 0.06 and 0.12 mm/h - i.e. between voting dry and wet.
    SOURCE_CHMI: 0.95,
    SOURCE_ICON_EU: 0.80,
    SOURCE_METNO: 0.70,
}

#: Nominal publication interval in seconds. Freshness decays against this, and a
#: series older than 3x it is stale and dropped for the cycle.
UPDATE_INTERVAL_S: dict[str, int] = {
    SOURCE_LIBREWXR: 10 * 60,
    SOURCE_CHMI: 5 * 60,
    SOURCE_KNMI: 60 * 60,
    SOURCE_ICON_EU: 3 * 60 * 60,
    SOURCE_METNO: 2 * 60 * 60,
}

STALE_FACTOR: int = 3

#: Effective cell size in km at 52 N (docs/DATA_SOURCES.md § Effective resolution).
CELL_KM: dict[str, float] = {
    SOURCE_LIBREWXR: 2.0,
    #: CHMI publishes the CZRAD composite at a stated 1x1 km resolution, which the
    #: frame's own extent confirms: 1.005 km per pixel east-west at 49.75 N.
    SOURCE_CHMI: 1.0,
    SOURCE_KNMI: 5.5,
    SOURCE_ICON_EU: 6.95,
    SOURCE_METNO: 10.0,
}

#: Attribution required by each provider's licence; surfaced in sensor attributes.
ATTRIBUTION: dict[str, str] = {
    SOURCE_LIBREWXR: (
        "Weather data via LibreWXR (librewxr.net), based on EUMETNET OPERA radar "
        "(CC BY 4.0, modified)"
    ),
    SOURCE_ICON_EU: "Weather data by Open-Meteo.com and DWD ICON-EU (CC BY 4.0, modified)",
    SOURCE_KNMI: ("Weather data by Open-Meteo.com and KNMI HARMONIE AROME (CC BY 4.0, modified)"),
    SOURCE_METNO: "Weather data from MET Norway (CC BY 4.0 / NLOD, modified)",
    SOURCE_CHMI: (
        "Radar data from the Czech Hydrometeorological Institute (CHMI), CZRAD composite "
        "via opendata.chmi.cz (CC BY 4.0, modified)"
    ),
}

# Status values (docs/ARCHITECTURE.md § Data flow). `out_of_range` is per-slot and
# assigned by the engine; adapters use the others.
STATE_OK = "ok"
STATE_STALE = "stale"
STATE_FAILED = "failed"
STATE_OUT_OF_RANGE = "out_of_range"
STATE_DISABLED = "disabled"

#: The source cannot serve this location at all — a permanent property of where the
#: user lives, not of this cycle. Only regional sources report it, and they never
#: make a request while they do (docs/DATA_SOURCES.md § CHMI). Distinct from
#: `out_of_range`, which is about a *slot* a fetched source does not reach, and from
#: `disabled`, which is a dormancy this cycle could end.
STATE_NOT_APPLICABLE = "not_applicable"

EARTH_RADIUS_KM = 6371.0088

#: Marshall-Palmer `Z = 200 * R^1.6`, inverted (docs/DATA_SOURCES.md § Intensity
#: mapping). Shared: both radar sources decode a reflectivity scale, and they must
#: land on the same mm/h or the consensus would be comparing two different scales.
MP_A = 200.0
MP_B = 1.6


def dbz_to_mm_per_h(dbz: float) -> float:
    """Reflectivity in dBZ to rain rate in mm/h."""
    return float((10.0 ** (dbz / 10.0) / MP_A) ** (1.0 / MP_B))


def utc_now() -> datetime:
    """Current UTC time. Adapters take `now` as a parameter; this is the fallback."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class SampleGeometry:
    """Where to sample: a disc of `radius_km` around the configured location.

    Point sources sample the centre plus four points at the disc edge; the tile
    source samples every pixel inside the disc (docs/ARCHITECTURE.md § Frame sampling).
    """

    latitude: float
    longitude: float
    radius_km: float

    @property
    def key(self) -> str:
        """Identity used to invalidate caches when the geometry changes."""
        return f"{self.latitude:.5f},{self.longitude:.5f},{self.radius_km:.2f}"

    def offset(self, distance_km: float, bearing_deg: float) -> tuple[float, float]:
        """Great-circle destination point, rounded to the precision the APIs accept."""
        angular = distance_km / EARTH_RADIUS_KM
        bearing = math.radians(bearing_deg)
        lat1 = math.radians(self.latitude)
        lon1 = math.radians(self.longitude)
        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular)
            + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular) * math.cos(lat1),
            math.cos(angular) - math.sin(lat1) * math.sin(lat2),
        )
        return round(math.degrees(lat2), 4), round(math.degrees(lon2), 4)

    def sample_points(self) -> tuple[tuple[float, float], ...]:
        """Centre plus N/E/S/W points at the disc edge — the 5 coordinates NWP sources use."""
        return (
            (self.latitude, self.longitude),
            *(self.offset(self.radius_km, bearing) for bearing in (0.0, 90.0, 180.0, 270.0)),
        )


@dataclass(frozen=True)
class SourceSeries:
    """One source's normalized forecast for the sampled disc."""

    source_id: str
    issued_at: datetime
    fetched_at: datetime
    step_s: int
    slots: tuple[tuple[datetime, float], ...]
    cell_km: float
    reliability: float

    def age_s(self, now: datetime) -> int:
        """Seconds since the data was issued upstream."""
        return max(0, int((now - self.issued_at).total_seconds()))

    def is_stale(self, now: datetime) -> bool:
        """True once the series is older than 3x its source's publication interval."""
        return self.age_s(now) > STALE_FACTOR * UPDATE_INTERVAL_S[self.source_id]

    @property
    def horizon_end(self) -> datetime | None:
        """End of the last slot — beyond this the source is out of range, not stale."""
        if not self.slots:
            return None
        return self.slots[-1][0] + timedelta(seconds=self.step_s)


@dataclass(frozen=True)
class SourceStatus:
    """Why a source did or did not contribute — surfaced in the sensor attributes."""

    source_id: str
    state: str
    age_s: int | None = None
    contributed: bool = False
    detail: str | None = None


@dataclass(frozen=True)
class FetchResult:
    """What one adapter produced this cycle: series plus a status per source id."""

    series: tuple[SourceSeries, ...] = ()
    statuses: tuple[SourceStatus, ...] = ()

    @property
    def ok(self) -> bool:
        """True when every source this adapter speaks for produced usable data."""
        return bool(self.statuses) and all(s.state == STATE_OK for s in self.statuses)


class SourceAdapter(Protocol):
    """What the registry expects of every adapter."""

    #: Source ids this adapter produces series for (Open-Meteo speaks for two).
    source_ids: tuple[str, ...]

    @property
    def budget(self) -> RequestBudget:
        """The adapter's rolling hourly request budget, for the registry to total."""
        ...

    def should_fetch(self, now: datetime) -> bool:
        """False when the adapter's own cadence or backoff says to reuse cached data."""
        ...

    async def fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        """Fetch, sample and normalize. Never raises for provider-side failures."""
        ...

    def cached(self, now: datetime) -> FetchResult:
        """The last successful result, re-presented on a skipped or failed cycle."""
        ...


def restate(result: FetchResult, now: datetime) -> FetchResult:
    """Re-evaluate a cached result's freshness against the current time.

    A series kept from an earlier cycle is never re-presented as fresh: its age
    grows with the clock and it flips to `stale` on its own once it crosses 3x its
    publication interval (docs/DATA_SOURCES.md § Fallback strategy).
    """
    statuses = []
    for status in result.statuses:
        series = next((s for s in result.series if s.source_id == status.source_id), None)
        if series is None:
            statuses.append(status)
            continue
        stale = series.is_stale(now)
        statuses.append(
            SourceStatus(
                status.source_id,
                STATE_STALE if stale else STATE_OK,
                age_s=series.age_s(now),
                contributed=not stale,
                detail=status.detail,
            )
        )
    return FetchResult(series=result.series, statuses=tuple(statuses))


@dataclass
class RequestBudget:
    """Rolling cap on outgoing requests, enforcing the docs/DATA_SOURCES.md budget.

    The budget there is stated per active hour, so it is enforced over a rolling
    hour rather than per cycle: a cold start may legitimately spend several
    requests in one cycle as long as the hour stays inside its ceiling.
    """

    limit: int
    window_s: int = 3600
    _times: list[datetime] = field(default_factory=list)

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_s)
        self._times = [t for t in self._times if t > cutoff]

    def remaining(self, now: datetime) -> int:
        """How many more requests the budget allows right now."""
        self._prune(now)
        return max(0, self.limit - len(self._times))

    def consume(self, now: datetime) -> bool:
        """Record one request; False when the budget is exhausted (caller must not send)."""
        if self.remaining(now) <= 0:
            return False
        self._times.append(now)
        return True


@dataclass
class Backoff:
    """Cross-cycle retry spacing for a failing source.

    docs/DATA_SOURCES.md budgets "3 attempts with exponential backoff (1, 2, 4 min,
    capped at 15 min)". The delays are applied *between* update cycles rather than as
    sleeps inside one: a coordinator cycle must never block the event loop for minutes
    (deviation recorded in STATE.md, phase 3).
    """

    delays_s: tuple[int, ...] = (60, 120, 240)
    max_delay_s: int = 900
    failures: int = 0
    next_attempt_at: datetime | None = field(default=None)

    def ready(self, now: datetime) -> bool:
        """True when enough time has passed since the last failure to try again."""
        return self.next_attempt_at is None or now >= self.next_attempt_at

    def record_success(self) -> None:
        """Clear the backoff."""
        self.failures = 0
        self.next_attempt_at = None

    def record_failure(self, now: datetime) -> None:
        """Advance the backoff one step and arm the next attempt time."""
        index = min(self.failures, len(self.delays_s) - 1)
        delay = min(self.delays_s[index], self.max_delay_s)
        self.failures += 1
        self.next_attempt_at = now + timedelta(seconds=delay)
