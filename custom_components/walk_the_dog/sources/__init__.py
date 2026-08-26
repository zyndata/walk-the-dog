"""Source adapter registry: builds the adapter set and owns provider failover.

Correlated sources must never vote together (docs/DATA_SOURCES.md § Ranked
recommendation, measured correlations). MET Norway correlates 0.61 with KNMI and
0.71 with ECMWF-IFS, so it stays dormant while Open-Meteo is healthy and is woken
only when Open-Meteo has failed twice in a row — the registry is the single place
that decides this.

It is also where a *regional* source is kept quiet. CHMI's CZRAD composite covers
only the south-west corner of Poland, so the registry asks it once whether the
configured location is inside the composite and, if it is not, never calls it again
— the source reports `not_applicable` and costs nothing
(docs/DATA_SOURCES.md § CHMI).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .base import (
    ATTRIBUTION,
    FetchResult,
    SampleGeometry,
    SourceAdapter,
    SourceSeries,
    SourceStatus,
)
from .chmi import ChmiAdapter
from .librewxr import LibreWxrAdapter
from .met_norway import MetNorwayAdapter
from .open_meteo import OpenMeteoAdapter

if TYPE_CHECKING:
    from aiohttp import ClientSession

    from ..cache import SampleCache

_LOGGER = logging.getLogger(__name__)

#: Consecutive Open-Meteo failures before MET Norway is woken, and consecutive
#: successes before it goes dormant again (docs/DATA_SOURCES.md § Fallback strategy).
FAILOVER_THRESHOLD = 2


def build_user_agent(version: str) -> str:
    """Identifying `User-Agent` with contact information.

    Mandatory for MET Norway (403 otherwise) and for LibreWXR, which rejects the
    default Python agent (measured in phase 0). It carries the project URL as the
    contact point — never the user's own details.
    """
    return f"walk_the_dog/{version} (+https://github.com/zyndata/walk-the-dog)"


class SourceRegistry:
    """Owns the adapters, the per-cycle fetch, and provider failover."""

    def __init__(self, user_agent: str, cache: SampleCache | None = None) -> None:
        self.librewxr = LibreWxrAdapter(user_agent, cache=cache)
        self.open_meteo = OpenMeteoAdapter(user_agent)
        self.met_norway = MetNorwayAdapter(user_agent)
        self.chmi = ChmiAdapter(user_agent, cache=cache)
        self._open_meteo_failures = 0
        self._open_meteo_successes = 0

    @property
    def adapters(
        self,
    ) -> tuple[LibreWxrAdapter, ChmiAdapter, OpenMeteoAdapter, MetNorwayAdapter]:
        """Every adapter, in the order their statuses are reported."""
        return (self.librewxr, self.chmi, self.open_meteo, self.met_norway)

    @property
    def failover_active(self) -> bool:
        """True while MET Norway is standing in for Open-Meteo."""
        return self.met_norway.enabled

    async def async_fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> tuple[list[SourceSeries], list[SourceStatus]]:
        """Run one update cycle across all adapters.

        Adapters that are inside their own cadence or backoff re-present cached data
        instead of making a request, so a skipped fetch still yields a full picture
        for the engine — with the freshness weights that its age now implies.
        """
        results: dict[str, FetchResult] = {}

        # Three independent providers: fetch concurrently. CHMI is skipped
        # entirely — not even a cached status lookup costs a request — when the
        # configured location lies outside its composite.
        primary: list[Any] = [self.librewxr, self.open_meteo]
        if self.chmi.applicable(geometry):
            primary.append(self.chmi)
        else:
            results[self.chmi.source_ids[0]] = self.chmi.not_applicable()
        gathered = await asyncio.gather(
            *(self._run(adapter, session, geometry, now) for adapter in primary)
        )
        for adapter, result in zip(primary, gathered, strict=True):
            results[adapter.source_ids[0]] = result

        self._update_failover(results[self.open_meteo.source_ids[0]])

        # MET Norway is decided by this cycle's Open-Meteo outcome, so it runs after.
        results["metno"] = await self._run(self.met_norway, session, geometry, now)

        series: list[SourceSeries] = []
        statuses: list[SourceStatus] = []
        for adapter in self.adapters:
            result = results[adapter.source_ids[0]]
            series.extend(result.series)
            statuses.extend(result.statuses)
        return series, statuses

    async def _run(
        self,
        adapter: SourceAdapter,
        session: ClientSession,
        geometry: SampleGeometry,
        now: datetime,
    ) -> FetchResult:
        if not adapter.should_fetch(now):
            return adapter.cached(now)
        return await adapter.fetch(session, geometry, now)

    def _update_failover(self, open_meteo: FetchResult) -> None:
        """Wake or retire MET Norway based on Open-Meteo's recent record."""
        if open_meteo.ok:
            self._open_meteo_successes += 1
            self._open_meteo_failures = 0
        else:
            self._open_meteo_failures += 1
            self._open_meteo_successes = 0

        if not self.met_norway.enabled and self._open_meteo_failures >= FAILOVER_THRESHOLD:
            _LOGGER.info("Open-Meteo unavailable; falling back to MET Norway")
            self.met_norway.enabled = True
        elif self.met_norway.enabled and self._open_meteo_successes >= FAILOVER_THRESHOLD:
            _LOGGER.info("Open-Meteo healthy again; standing MET Norway down")
            self.met_norway.enabled = False

    def fast_cadence(self, geometry: SampleGeometry) -> bool:
        """Whether any source here publishes faster than the engine's 10-minute grid.

        Only CHMI does (5 minutes), and only inside its composite. The coordinator
        asks before shortening its cycle: everywhere else the extra cycles would
        re-score bytes that have not changed, which costs CPU and buys nothing.
        """
        return self.chmi.applicable(geometry)

    def budget(self, now: datetime) -> tuple[int, int]:
        """Requests spent in the last rolling hour, and the ceiling they count against.

        Every adapter polices its own budget before it sends anything
        (`base.RequestBudget`), which is what keeps the integration inside the
        allowances in docs/DATA_SOURCES.md however long a walk window stays open.
        Totalling them here is what makes that promise visible on the sensor rather
        than merely true in the code.

        The cap is the sum of every adapter's own cap, including sources that are
        dormant or not applicable at this location and therefore spend nothing.
        """
        spent = 0
        cap = 0
        for adapter in self.adapters:
            budget = adapter.budget
            cap += budget.limit
            spent += budget.limit - budget.remaining(now)
        return spent, cap

    def attributions(self, statuses: list[SourceStatus]) -> list[str]:
        """Attribution strings for the sources that actually contributed."""
        return [
            ATTRIBUTION[status.source_id]
            for status in statuses
            if status.contributed and status.source_id in ATTRIBUTION
        ]


__all__ = [
    "FAILOVER_THRESHOLD",
    "ChmiAdapter",
    "LibreWxrAdapter",
    "MetNorwayAdapter",
    "OpenMeteoAdapter",
    "SourceRegistry",
    "build_user_agent",
]
