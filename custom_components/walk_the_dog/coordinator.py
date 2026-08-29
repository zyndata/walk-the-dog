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

The window's far end is `later_margin` past the walk, not the walk itself, and that
is deliberate: at the decision moment the radars can see one hour ahead, so a walk
moved further out than that is a model-only answer at first. Staying awake is what
lets the radar confirm or correct it as the suggested hour comes into its range.
Those extra cycles are why the engine is told what time it is (`recommend(now=...)`)
and why the notifier checks `is_actionable` — a window that is still worth watching
is not the same as advice that is still worth sending.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .cache import SampleCache
from .const import (
    CONF_CONFIRM_MARGIN_MIN,
    CONF_EARLIER_MARGIN_MIN,
    CONF_INTENSITY_THRESHOLD,
    CONF_LATER_MARGIN_MIN,
    CONF_LOCATION,
    CONF_RADIUS_KM,
    CONF_SCHEDULE,
    CONF_SCHEDULE_MODE,
    CONF_TARGET_AWAY_ENTITY,
    CONF_TARGET_MUTE,
    CONF_TARGET_SERVICES,
    CONF_WALK_DURATION_MIN,
    CONF_WALK_TARGETS,
    DEFAULT_CONFIRM_MARGIN_MIN,
    DEFAULT_EARLIER_MARGIN_MIN,
    DEFAULT_INTENSITY_THRESHOLD,
    DEFAULT_LATER_MARGIN_MIN,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    EVENT_MOBILE_APP_ACTION,
    LEAD_TIME_MIN,
    SCHEDULE_MODE_DAILY,
    SLOT_MINUTES,
    SPRINT_LEAD_MIN,
    SPRINT_MINUTES,
    publish_settle_s,
)
from .engine import DIRECTION_UNKNOWN, Search, build_consensus, evaluation_slots, recommend
from .notifier import WalkNotifier, WalkTarget, walk_start_from_action
from .schedule import ScheduleError, walks_from
from .sources import SourceRegistry, build_user_agent
from .sources.base import UPDATE_INTERVAL_S, SampleGeometry

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .engine import Recommendation, SourceBreakdown
    from .schedule import Walk
    from .sources.base import SourceStatus

_LOGGER = logging.getLogger(__name__)

#: One update cycle per grid slot — LibreWXR's frame cadence. Polling faster
#: cannot observe new data (docs/ARCHITECTURE.md § Coordinator scheduling).
CYCLE = timedelta(minutes=SLOT_MINUTES)

#: How long before `T - earlier_margin` polling starts, so the decision moment has
#: warm data. A whole number of slots, which is what keeps the cycle grid aligned.
LEAD_TIME = timedelta(minutes=LEAD_TIME_MIN)

#: The faster cadence used in the final approach to setting off, and how long before
#: it starts. A shower can build and arrive well inside one 10-minute slot, so the
#: last stretch before the door is worth watching at the fastest rate any source
#: actually publishes at. `SPRINT` divides `CYCLE`, so the anchored grid is unchanged
#: — it is subdivided, and the cycle that lands on `T - earlier_margin` still lands.
SPRINT = timedelta(minutes=SPRINT_MINUTES)
SPRINT_LEAD = timedelta(minutes=SPRINT_LEAD_MIN)


def publish_settle(source_id: str) -> timedelta:
    """Grace between a frame's stamp and asking for it, per source.

    Measured in phase 8: LibreWXR publishes up to 158 s after a frame's own
    timestamp and CHMI within 20 s, so one shared number would have been too early
    for one of them — and too early means the aligned cycle asks for a frame that is
    not there yet (`const.PUBLISH_SETTLE_S`).
    """
    return timedelta(seconds=publish_settle_s(source_id))


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
    #: Requests spent in the last rolling hour, and the ceiling they are counted
    #: against — the budget from docs/DATA_SOURCES.md, made visible.
    requests_last_hour: int = 0
    requests_hourly_cap: int = 0

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
            "recommended_end": None,
            "shift_min": None,
            "duration_min": None,
            "risk": None,
            "confidence": None,
            "expected_intensity": None,
            "degraded": False,
            "horizon_limited": False,
            "provisional": False,
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
                "recommended_end": _iso(recommendation.recommended_end),
                "shift_min": None if shift is None else int(shift.total_seconds() // 60),
                "duration_min": recommendation.duration_s // 60,
                "risk": round(recommendation.risk, 3) if known else None,
                "confidence": round(recommendation.confidence, 3) if known else None,
                "expected_intensity": recommendation.peak_intensity if known else None,
                "degraded": recommendation.degraded,
                "horizon_limited": recommendation.horizon_limited,
                # No radar reaches the window being recommended: an early answer the
                # coordinator is still checking, not a final one.
                "provisional": recommendation.provisional,
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


def _confirm_margin(options: Any) -> timedelta | None:
    """The reassurance margin as a duration, or None when it is switched off."""
    minutes = int(options.get(CONF_CONFIRM_MARGIN_MIN, DEFAULT_CONFIRM_MARGIN_MIN))
    return timedelta(minutes=minutes) if minutes > 0 else None


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
        self._search = Search(
            duration=timedelta(minutes=int(options.get(CONF_WALK_DURATION_MIN, 30))),
            earlier_margin=timedelta(
                minutes=int(options.get(CONF_EARLIER_MARGIN_MIN, DEFAULT_EARLIER_MARGIN_MIN))
            ),
            later_margin=timedelta(
                minutes=int(options.get(CONF_LATER_MARGIN_MIN, DEFAULT_LATER_MARGIN_MIN))
            ),
        )
        self._targets: dict[str, Any] = options.get(CONF_WALK_TARGETS) or {}

        self._confirm_margin = _confirm_margin(options)
        self._cache = SampleCache(self._geometry.key)
        self._cache.attach_store(hass)
        self._registry = SourceRegistry(build_user_agent(version), cache=self._cache)
        self.notifier = WalkNotifier(hass, entry, confirm_margin=self._confirm_margin)

        # Starts off: the switch restores the real state and turns it on, so a
        # restart with alerting disabled makes no request at all.
        self._enabled = False
        self._walk: Walk | None = None
        self._unsub_timer: Any = None
        self._unsub_action: Any = None
        # When each source last issued data, so the next cycle can be timed to the
        # frame it is waiting for rather than only to our own grid.
        self._issued: dict[str, datetime] = {}
        # The walk the user has said they already went on. In memory only: a restart
        # inside the window resurrects it, which is the safe way round to be wrong.
        self._dismissed: datetime | None = None

    # --- lifecycle ----------------------------------------------------------

    async def async_setup_cache(self) -> None:
        """Restore the persisted samples, so a restart inside a window is warm."""
        await self._cache.async_load()

    @callback
    def async_listen_actions(self) -> None:
        """Listen for a tap on the notification's own button.

        `mobile_app` is not a dependency and must not become one — a user without the
        companion app simply never fires this event, and everything else still works.
        """
        self._unsub_action = self.hass.bus.async_listen(
            EVENT_MOBILE_APP_ACTION, self._handle_action
        )

    async def async_shutdown(self) -> None:
        """Cancel the armed timer along with everything the base class owns."""
        self._cancel_timer()
        if self._unsub_action is not None:
            self._unsub_action()
            self._unsub_action = None
        await super().async_shutdown()

    @property
    def enabled(self) -> bool:
        """Whether alerting — and therefore polling — is switched on."""
        return self._enabled

    async def async_mark_walked(self, walk_start: datetime | None = None) -> None:
        """Close a walk: the user has already gone, so there is nothing left to advise.

        Alerting stays on — this is about one occurrence, not about the integration.
        Dropping the walk also drops its polling, which is the point: once the dog is
        out, every further request is spent on a decision nobody is going to make.

        `walk_start` names the occurrence, so a button tapped on a notification left
        over from yesterday cannot close today's walk. Omitted, it means the walk
        currently being watched — which is what the service defaults to.
        """
        walk = self._walk
        target = walk_start if walk_start is not None else (walk.start if walk else None)
        if target is None:
            return
        self._dismissed = target
        if walk is not None and walk.start == target:
            await self.notifier.async_clear(self._target_for(walk), target)
        self.notifier.reset()
        await self.async_refresh()

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
        previous = self._walk
        walk = self._walk = self._resolve_walk(now)
        if previous is not None and (walk is None or walk.start != previous.start):
            await self._async_take_down(previous)
        if not self._enabled or walk is None or not self._is_active(now):
            return self._idle(active=False)

        data = await self._cycle(now, walk)
        await self.notifier.async_process(
            data,
            now,
            arm_at=walk.start - self._search.earlier_margin,
            confirm_at=self._confirm_at(walk),
            target=self._target_for(walk),
        )
        return data

    async def _async_take_down(self, walk: Walk) -> None:
        """Take a finished walk's notification off the phones it is still sitting on.

        The push survives the tap that opens it, which is the point of it — so
        something has to remove it once the walk it advises about is over, or
        tomorrow's advice arrives underneath yesterday's. The cycle that lands
        exactly on the end of the window is where the coordinator moves on to the
        next walk (`_next_wake`), and that is this.
        """
        if not self.notifier.has_spoken(walk.start):
            return
        await self.notifier.async_clear(self._target_for(walk), walk.start)

    def _target_for(self, walk: Walk) -> WalkTarget:
        """This walk's own devices, mute switch and away entity, if it has any.

        Everything absent falls back to the entry-wide setting, so a walk the user
        never opened the notification step for behaves exactly as before.
        """
        stored = self._targets.get(walk.target_key) or {}
        return WalkTarget(
            services=tuple(stored.get(CONF_TARGET_SERVICES) or ()),
            muted=bool(stored.get(CONF_TARGET_MUTE, False)),
            away_entity=stored.get(CONF_TARGET_AWAY_ENTITY) or None,
        )

    async def _cycle(self, now: datetime, walk: Walk) -> WalkData:
        """Fetch what is due, score every slot the search can reach, recommend."""
        session = async_get_clientsession(self.hass)
        series, statuses = await self._registry.async_fetch(session, self._geometry, now)
        self._issued = {item.source_id: item.issued_at for item in series}
        self._cache.evict_expired(now)
        self._cache.async_schedule_save()

        consensus = build_consensus(
            series,
            statuses,
            slots=evaluation_slots(walk.start, self._search),
            threshold=self._threshold,
            now=now,
        )
        recommendation = recommend(
            consensus,
            scheduled_start=walk.start,
            search=self._search,
            # Without this the search would keep offering slots that have passed,
            # for as long as the window below stays open.
            now=now,
        )
        spent, cap = self._registry.budget(now)
        return WalkData(
            enabled=True,
            active=True,
            walk_start=walk.start,
            recommendation=recommendation,
            statuses=consensus.statuses,
            attributions=tuple(self._registry.attributions(list(consensus.statuses))),
            fetched_at=now,
            failover=self._registry.failover_active,
            requests_last_hour=spent,
            requests_hourly_cap=cap,
        )

    def _idle(self, *, active: bool) -> WalkData:
        """A cycle that made no request: the schedule is known, the forecast is not."""
        walk = self._walk
        return WalkData(
            enabled=self._enabled, active=active, walk_start=None if walk is None else walk.start
        )

    # --- scheduling ---------------------------------------------------------

    def _walks(self, now: datetime) -> tuple[Walk, ...]:
        """Upcoming walk starts, including one whose window may still be running."""
        try:
            return walks_from(
                self._mode,
                self._schedule,
                moment=now - (self._search.later_margin + self._search.duration),
                tz=dt_util.get_default_time_zone(),
            )
        except ScheduleError:
            _LOGGER.warning("Stored walk schedule is unusable; no walk can be predicted")
            return ()

    def _window_start(self, walk: Walk) -> datetime:
        """When polling for this walk begins: `T - earlier_margin - lead_time`."""
        return walk.start - self._search.earlier_margin - LEAD_TIME

    def _walk_end(self, walk: Walk) -> datetime:
        """End of the window being watched — a `later` recommendation extends it.

        This is how a suggestion made beyond the radar's reach gets checked: told at
        12:00 to wait until 14:00, the coordinator stays awake through 14:00 and the
        radars see that hour long before it arrives. The extension is bounded by
        `later_margin`, because that is the furthest the search may ever point, and
        it collapses back to the walk itself the moment the advice stops pointing
        forward — which the engine now guarantees it eventually does.
        """
        recommended = self._recommended_start(walk)
        return max(walk.start, recommended or walk.start) + self._search.duration

    def _recommended_start(self, walk: Walk) -> datetime | None:
        """The current recommendation's start, but only if it is about this walk."""
        data = self.data
        if data is None or data.walk_start != walk.start or data.recommendation is None:
            return None
        return data.recommendation.recommended_start

    def _departure(self, walk: Walk) -> datetime:
        """When the user is expected to set off — the suggested time, else the walk's own."""
        return self._recommended_start(walk) or walk.start

    def _confirm_at(self, walk: Walk) -> datetime | None:
        """When to say "this still stands", or None when the option is off."""
        if self._confirm_margin is None:
            return None
        return self._departure(walk) - self._confirm_margin

    def _resolve_walk(self, now: datetime) -> Walk | None:
        """The walk currently being watched: the first whose window has not ended.

        A walk the user has said they already went on is skipped outright, which
        moves the coordinator on to the next one and stops polling for this one.
        """
        for walk in self._walks(now):
            if walk.start == self._dismissed:
                continue
            if now < self._walk_end(walk):
                return walk
        return None

    def _is_active(self, now: datetime) -> bool:
        """True inside `[T - E - lead_time, walk end]` — the only time we fetch."""
        walk = self._walk
        return walk is not None and self._window_start(walk) <= now < self._walk_end(walk)

    def _cadence(self, now: datetime, walk: Walk) -> timedelta:
        """How long until the next cycle: the sprint near the door, else a grid slot.

        Polling a source faster than it publishes returns the same bytes, so the
        sprint is only worth running where a source actually publishes faster than
        the grid — today that means CHMI's five minutes, and only inside its
        composite. Everywhere else the extra cycles would re-score identical data.
        """
        if not self._registry.fast_cadence(self._geometry):
            return CYCLE
        departure = self._departure(walk)
        return SPRINT if departure - SPRINT_LEAD <= now < departure else CYCLE

    def _aligned_wake(self, now: datetime, cadence: timedelta) -> datetime | None:
        """When the source that publishes at this cadence is next due, or None.

        The cycle grid is anchored to the walk, and a provider's frames are not: at
        a 10-minute cadence the two run at whatever phase they happen to run at, so a
        frame published a minute after a cycle waits nearly a full slot to be read.
        The data is no staler for it — a fetch always returns the newest frame that
        exists — but the *alert* it would trigger waits with it, and a shower that
        builds in twenty minutes is exactly the case where those minutes are the
        answer.

        The reference is the source whose own publication interval equals the cadence
        being run: LibreWXR at ten minutes, CHMI at five. Sources that publish hourly
        have nothing to align to at this timescale, and a location with no fast source
        gets `None` and keeps the plain grid.
        """
        seconds = int(cadence.total_seconds())
        reference = next(
            (
                (source_id, stamp)
                for source_id, stamp in self._issued.items()
                if UPDATE_INTERVAL_S.get(source_id) == seconds
            ),
            None,
        )
        if reference is None:
            return None
        source_id, issued = reference
        due = issued + cadence + publish_settle(source_id)
        if due > now:
            return due
        # A publication we missed, or a source that has stopped: roll forward to the
        # next one it owes us rather than firing immediately and repeatedly.
        return due + ((now - due) // cadence + 1) * cadence

    def _next_wake(self, now: datetime) -> datetime | None:
        """When to run the next cycle, or None when there is nothing to wait for."""
        walk = self._walk
        if walk is None:
            return None
        start = self._window_start(walk)
        if now < start:
            return start
        # Anchored to the window start, not the wall clock: because lead_time is a
        # whole number of slots, one cycle then lands exactly on `T - E`. The sprint
        # subdivides that grid rather than replacing it, so the guarantee survives.
        elapsed = now - start
        cadence = self._cadence(now, walk)
        slots = int(elapsed // cadence) + 1
        wake = start + slots * cadence
        # Publication alignment may only ever pull a cycle *earlier*. Taking the
        # minimum is what makes it safe: the grid keeps running underneath at its own
        # rate whatever the provider does, so a wrong guess about when a frame lands
        # costs one cheap extra cycle and can never cost a cycle that was due.
        aligned = self._aligned_wake(now, cadence)
        if aligned is not None:
            wake = min(wake, aligned)
        # The promised notification moment is a hard point, not a consequence of the
        # arithmetic above: never sleep through it.
        arm_at = walk.start - self._search.earlier_margin
        if now < arm_at:
            wake = min(wake, arm_at)
        # Never sleep past the end of the window: waking exactly then is what moves
        # the coordinator on to the following walk without an idle cycle in between.
        return min(wake, self._walk_end(walk))

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

    @callback
    def _handle_action(self, event: Event) -> None:
        """A companion-app notification action was tapped — ours, or another integration's."""
        walk_start = walk_start_from_action(str(event.data.get("action") or ""))
        if walk_start is None:
            return
        self.config_entry.async_create_task(
            self.hass, self.async_mark_walked(walk_start), "walk_the_dog_walked"
        )


__all__ = [
    "CYCLE",
    "LEAD_TIME",
    "SPRINT",
    "SPRINT_LEAD",
    "WalkCoordinator",
    "WalkData",
    "publish_settle",
]
