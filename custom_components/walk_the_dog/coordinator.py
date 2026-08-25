"""WalkCoordinator: polling windows, orchestration, and the published result.

Implements docs/ARCHITECTURE.md § Coordinator scheduling & polling windows. One
shared `DataUpdateCoordinator` per config entry runs a cycle every 10 minutes
while a walk window is near, and **nothing at all** the rest of the time: there
is no `update_interval`, only a single armed point-in-time timer, so an idle day
costs zero requests and zero wakeups.

The 10-minute cycle grid is anchored to the window start rather than to the wall
clock, and the window starts exactly `earlier_margin + lead_time` before the walk.
Because `lead_time` is a whole number of slots, a cycle therefore lands exactly on
`T - earlier_margin` — the moment the notification is promised for — whatever
minute the walk itself is scheduled at.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .cache import SampleCache
from .const import (
    CONF_AUTO_MUTE_ENTITY,
    CONF_EARLIER_MARGIN_MIN,
    CONF_FIRE_EVENT,
    CONF_INTENSITY_THRESHOLD,
    CONF_LATER_MARGIN_MIN,
    CONF_LOCATION,
    CONF_NOTIFY_SERVICE,
    CONF_RADIUS_KM,
    CONF_SCHEDULE,
    CONF_SCHEDULE_MODE,
    CONF_WALK_DURATION_MIN,
    DEFAULT_EARLIER_MARGIN_MIN,
    DEFAULT_INTENSITY_THRESHOLD,
    DEFAULT_LATER_MARGIN_MIN,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    LEAD_TIME_MIN,
    SCHEDULE_MODE_DAILY,
    SLOT_MINUTES,
)
from .engine import DIRECTION_UNKNOWN, build_consensus, evaluation_slots, recommend
from .notifier import WalkNotifier
from .schedule import ScheduleError, walks_from
from .sources import SourceRegistry, build_user_agent
from .sources.base import SampleGeometry

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .engine import Recommendation, SourceBreakdown
    from .sources.base import SourceStatus

_LOGGER = logging.getLogger(__name__)

#: One update cycle per grid slot — LibreWXR's frame cadence. Polling faster
#: cannot observe new data (docs/ARCHITECTURE.md § Coordinator scheduling).
CYCLE = timedelta(minutes=SLOT_MINUTES)

#: How long before `T - earlier_margin` polling starts, so the decision moment has
#: warm data. A whole number of slots, which is what keeps the cycle grid aligned.
LEAD_TIME = timedelta(minutes=LEAD_TIME_MIN)


@dataclass(frozen=True)
class WalkData:
    """What one cycle publishes: the answer, and everything needed to explain it."""

    enabled: bool
    active: bool
    walk_start: datetime | None = None
    recommendation: Recommendation | None = None
    statuses: tuple[SourceStatus, ...] = ()
    attributions: tuple[str, ...] = ()
    fetched_at: datetime | None = None
    failover: bool = False

    @property
    def direction(self) -> str:
        """The recommendation in one word, `unknown` while there is nothing to say."""
        if self.recommendation is None:
            return DIRECTION_UNKNOWN
        return self.recommendation.direction

    def payload(self) -> dict[str, Any]:
        """The serialized recommendation.

        One shape, used by both outputs: the sensor's attributes and the
        `walk_the_dog_alert` event payload documented in docs/CONFIG.md. Times are
        ISO-8601 UTC, so an automation never has to guess a timezone.
        """
        recommendation = self.recommendation
        payload: dict[str, Any] = {
            "direction": self.direction,
            "scheduled_start": _iso(self.walk_start),
            "recommended_start": None,
            "shift_min": None,
            "duration_min": None,
            "risk": None,
            "confidence": None,
            "expected_intensity": None,
            "degraded": False,
            "horizon_limited": False,
            "sources": [],
            "data_age_s": None,
        }
        if recommendation is None:
            return payload
        shift = recommendation.shift
        # A window no source reaches has no risk, rather than a risk of zero: the
        # scored fields stay null so "we do not know" cannot be read as "no rain".
        known = recommendation.scheduled.has_data
        payload.update(
            {
                "recommended_start": _iso(recommendation.recommended_start),
                "shift_min": None if shift is None else int(shift.total_seconds() // 60),
                "duration_min": recommendation.duration_s // 60,
                "risk": round(recommendation.risk, 3) if known else None,
                "confidence": round(recommendation.confidence, 3) if known else None,
                "expected_intensity": recommendation.peak_intensity if known else None,
                "degraded": recommendation.degraded,
                "horizon_limited": recommendation.horizon_limited,
                "sources": [_source_payload(source) for source in recommendation.sources],
                "data_age_s": _freshness_s(recommendation.sources),
            }
        )
        return payload


def _iso(moment: datetime | None) -> str | None:
    """ISO-8601 UTC, or None."""
    return None if moment is None else moment.isoformat()


def _source_payload(source: SourceBreakdown) -> dict[str, Any]:
    """One source's contribution, as an automation and the sensor both see it."""
    return {
        "source_id": source.source_id,
        "state": source.state,
        "verdict": source.verdict,
        "contributed": source.contributed,
        "weight": round(source.weight, 3),
        "age_s": source.age_s,
        "peak_mm_h": None if source.peak_mm_h is None else round(source.peak_mm_h, 2),
        "peak_intensity": source.peak_intensity,
    }


def _freshness_s(sources: tuple[SourceBreakdown, ...]) -> int | None:
    """Age of the freshest source that actually voted — the result's data freshness."""
    ages = [s.age_s for s in sources if s.contributed and s.age_s is not None]
    return min(ages) if ages else None


class WalkCoordinator(DataUpdateCoordinator[WalkData]):
    """Wires sources to the engine and owns when — and whether — anything runs."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, version: str) -> None:
        """Read the entry's settings once; an option change reloads the entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            # Deliberately no update_interval: idle ticking is what this design avoids.
            update_interval=None,
        )
        options = entry.options
        location = entry.data.get(CONF_LOCATION, {})
        self._geometry = SampleGeometry(
            latitude=float(location.get("latitude", hass.config.latitude)),
            longitude=float(location.get("longitude", hass.config.longitude)),
            radius_km=float(options.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)),
        )
        self._mode = options.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_DAILY)
        self._schedule = options.get(CONF_SCHEDULE, {})
        self._threshold = options.get(CONF_INTENSITY_THRESHOLD, DEFAULT_INTENSITY_THRESHOLD)
        self._duration = timedelta(minutes=int(options.get(CONF_WALK_DURATION_MIN, 30)))
        self._earlier = timedelta(
            minutes=int(options.get(CONF_EARLIER_MARGIN_MIN, DEFAULT_EARLIER_MARGIN_MIN))
        )
        self._later = timedelta(
            minutes=int(options.get(CONF_LATER_MARGIN_MIN, DEFAULT_LATER_MARGIN_MIN))
        )

        self._cache = SampleCache(self._geometry.key)
        self._cache.attach_store(hass)
        self._registry = SourceRegistry(build_user_agent(version), cache=self._cache)
        self.notifier = WalkNotifier(
            hass,
            notify_service=options.get(CONF_NOTIFY_SERVICE),
            fire_event=bool(options.get(CONF_FIRE_EVENT, False)),
            mute_entity=options.get(CONF_AUTO_MUTE_ENTITY),
        )

        # Starts off: the switch restores the real state and turns it on, so a
        # restart with alerting disabled makes no request at all.
        self._enabled = False
        self._walk: datetime | None = None
        self._unsub_timer: Any = None

    # --- lifecycle ----------------------------------------------------------

    async def async_setup_cache(self) -> None:
        """Restore the persisted samples, so a restart inside a window is warm."""
        await self._cache.async_load()

    async def async_shutdown(self) -> None:
        """Cancel the armed timer along with everything the base class owns."""
        self._cancel_timer()
        await super().async_shutdown()

    @property
    def enabled(self) -> bool:
        """Whether alerting — and therefore polling — is switched on."""
        return self._enabled

    async def async_set_enabled(self, enabled: bool) -> None:
        """Switch alerting on or off.

        Off cancels the timer outright: no timers, no requests, no cycles. On
        recomputes the schedule immediately and runs a cycle at once if a window
        is already open.
        """
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self._cancel_timer()
            self.notifier.reset()
            self.async_set_updated_data(self._idle(active=False))
            return
        await self.async_refresh()

    # --- the cycle ----------------------------------------------------------

    async def _async_update_data(self) -> WalkData:
        """One update cycle: resolve the walk, fetch if due, score, recommend."""
        now = dt_util.utcnow()
        self._walk = self._resolve_walk(now)
        if not self._enabled or self._walk is None or not self._is_active(now):
            return self._idle(active=False)

        data = await self._cycle(now, self._walk)
        await self.notifier.async_process(data, now, arm_at=self._walk - self._earlier)
        return data

    async def _cycle(self, now: datetime, walk: datetime) -> WalkData:
        """Fetch what is due, score every slot the search can reach, recommend."""
        session = async_get_clientsession(self.hass)
        series, statuses = await self._registry.async_fetch(session, self._geometry, now)
        self._cache.evict_expired(now)
        self._cache.async_schedule_save()

        consensus = build_consensus(
            series,
            statuses,
            slots=evaluation_slots(walk, self._duration, self._earlier, self._later),
            threshold=self._threshold,
            now=now,
        )
        recommendation = recommend(
            consensus,
            scheduled_start=walk,
            duration=self._duration,
            earlier_margin=self._earlier,
            later_margin=self._later,
        )
        return WalkData(
            enabled=True,
            active=True,
            walk_start=walk,
            recommendation=recommendation,
            statuses=consensus.statuses,
            attributions=tuple(self._registry.attributions(list(consensus.statuses))),
            fetched_at=now,
            failover=self._registry.failover_active,
        )

    def _idle(self, *, active: bool) -> WalkData:
        """A cycle that made no request: the schedule is known, the forecast is not."""
        return WalkData(enabled=self._enabled, active=active, walk_start=self._walk)

    # --- scheduling ---------------------------------------------------------

    def _walks(self, now: datetime) -> tuple[datetime, ...]:
        """Upcoming walk starts, including one whose window may still be running."""
        try:
            return walks_from(
                self._mode,
                self._schedule,
                moment=now - (self._later + self._duration),
                tz=dt_util.get_default_time_zone(),
            )
        except ScheduleError:
            _LOGGER.warning("Stored walk schedule is unusable; no walk can be predicted")
            return ()

    def _window_start(self, walk: datetime) -> datetime:
        """When polling for this walk begins: `T - earlier_margin - lead_time`."""
        return walk - self._earlier - LEAD_TIME

    def _walk_end(self, walk: datetime) -> datetime:
        """End of the window being watched — a `later` recommendation extends it."""
        recommended = self._recommended_start(walk)
        return max(walk, recommended or walk) + self._duration

    def _recommended_start(self, walk: datetime) -> datetime | None:
        """The current recommendation's start, but only if it is about this walk."""
        data = self.data
        if data is None or data.walk_start != walk or data.recommendation is None:
            return None
        return data.recommendation.recommended_start

    def _resolve_walk(self, now: datetime) -> datetime | None:
        """The walk currently being watched: the first whose window has not ended."""
        for walk in self._walks(now):
            if now < self._walk_end(walk):
                return walk
        return None

    def _is_active(self, now: datetime) -> bool:
        """True inside `[T - E - lead_time, walk end]` — the only time we fetch."""
        walk = self._walk
        return walk is not None and self._window_start(walk) <= now < self._walk_end(walk)

    def _next_wake(self, now: datetime) -> datetime | None:
        """When to run the next cycle, or None when there is nothing to wait for."""
        walk = self._walk
        if walk is None:
            return None
        start = self._window_start(walk)
        if now < start:
            return start
        # Anchored to the window start, not the wall clock: because lead_time is a
        # whole number of slots, one cycle then lands exactly on `T - E`.
        elapsed = now - start
        slots = int(elapsed // CYCLE) + 1
        # Never sleep past the end of the window: waking exactly then is what moves
        # the coordinator on to the following walk without an idle cycle in between.
        return min(start + slots * CYCLE, self._walk_end(walk))

    @callback
    def _async_refresh_finished(self) -> None:
        """Arm the next wakeup — the only timer this integration ever holds."""
        self._cancel_timer()
        if not self._enabled:
            return
        wake = self._next_wake(dt_util.utcnow())
        if wake is None:
            return
        self._unsub_timer = async_track_point_in_time(self.hass, self._handle_wake, wake)

    @callback
    def _cancel_timer(self) -> None:
        """Drop the armed timer, if any."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _handle_wake(self, _now: datetime) -> None:
        """Run a cycle because the armed timer fired."""
        self._unsub_timer = None
        await self.async_refresh()


__all__ = ["CYCLE", "LEAD_TIME", "WalkCoordinator", "WalkData"]
