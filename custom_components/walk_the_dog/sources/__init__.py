"""Source adapter registry: builds the adapter set and owns provider failover.

Correlated sources must never vote together (docs/DATA_SOURCES.md § Ranked
recommendation, measured correlations). MET Norway correlates 0.61 with KNMI and
0.71 with ECMWF-IFS, so it stays dormant while Open-Meteo is healthy and is woken
only when Open-Meteo has failed twice in a row — the registry is the single place
that decides this.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .base import (
    ATTRIBUTION,
    FetchResult,
    SampleGeometry,
    SourceSeries,
    SourceStatus,
)
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
        self._open_meteo_failures = 0
        self._open_meteo_successes = 0

    @property
    def adapters(self) -> tuple[LibreWxrAdapter, OpenMeteoAdapter, MetNorwayAdapter]:
        """Every adapter, in the order their statuses are reported."""
        return (self.librewxr, self.open_meteo, self.met_norway)

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

        # LibreWXR and Open-Meteo are independent providers: fetch concurrently.
        primary = [self.librewxr, self.open_meteo]
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
        adapter: LibreWxrAdapter | OpenMeteoAdapter | MetNorwayAdapter,
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

    def attributions(self, statuses: list[SourceStatus]) -> list[str]:
        """Attribution strings for the sources that actually contributed."""
        return [
            ATTRIBUTION[status.source_id]
            for status in statuses
            if status.contributed and status.source_id in ATTRIBUTION
        ]


__all__ = [
    "FAILOVER_THRESHOLD",
    "LibreWxrAdapter",
    "MetNorwayAdapter",
    "OpenMeteoAdapter",
    "SourceRegistry",
    "build_user_agent",
]
