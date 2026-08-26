"""Notification dispatch, material-change detection and auto-mute.

Implements docs/ARCHITECTURE.md § Coordinator scheduling, notification dispatch:
the first message goes out at `T - earlier_margin` — the latest moment at which
"go earlier" is still actionable — and afterwards only when the recommendation
changes materially (`engine.is_material_change`), so a forecast wobbling around
the threshold cannot notify twice in an hour.

That window has a far end as well as a near one. `engine.is_actionable` closes it:
advice the user can no longer follow is never sent, however material the change
that produced it. The coordinator deliberately keeps watching a walk past its
scheduled time — that is how "wait until 14:00" gets confirmed by a radar that
could not see 14:00 when the advice was given — and this is the rule that stops
those extra cycles from talking about the past.

Nothing is ever sent about a walk that looks dry: silence means "go as planned".

Who is interrupted is decided per walk, not per integration: each configured walk
carries its own list of companion-app devices and its own mute switch
(docs/CONFIG.md § Per-walk alerts), because the morning walk and the evening walk
are often not the same person's job. A walk with no devices of its own falls back
to the entry-wide default device.

The `walk_the_dog_alert` event fires whenever a notification *would* fire, even
when auto-mute suppresses the push — an automation may well want to know while
nobody is home. Its payload is `WalkData.payload()`, documented in docs/CONFIG.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import STATE_HOME
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_WALKED,
    CLEAR_NOTIFICATION,
    DOMAIN,
    EVENT_ALERT,
    NOTIFY_DOMAIN,
)
from .engine import (
    DIRECTION_EARLIER,
    DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW,
    DIRECTION_NONE,
    is_actionable,
    is_material_change,
    superseded_by_the_clock,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import WalkData
    from .engine import Recommendation

_LOGGER = logging.getLogger(__name__)

#: Directions worth interrupting the user for. `none` means the walk is fine and
#: `unknown` means we do not know — neither is news.
ALERT_DIRECTIONS: Final = frozenset({DIRECTION_EARLIER, DIRECTION_LATER, DIRECTION_NO_DRY_WINDOW})

#: strings.json section the notification texts live in, so phase 7 translates them
#: the same way as everything else the user reads. `common` is the only top-level
#: key Home Assistant allows for strings that belong to no form and no entity
#: (hassfest rejects any other), hence the explicit `notification_` prefix.
TRANSLATION_CATEGORY: Final = "common"
TEXT_PREFIX: Final = "notification_"

#: Appended when the recommended window is beyond the radar's reach, so the user
#: knows the suggestion is an early answer that is still being checked.
TEXT_PROVISIONAL: Final = "provisional"

#: The two shapes the optional confirmation takes shortly before setting off: the
#: plan still stands, or the rain has gone and the walk is back to its normal time.
TEXT_CONFIRMED: Final = "confirmed"
TEXT_STAND_DOWN: Final = "stand_down"

#: Label on the one action button the push carries.
TEXT_ACTION_WALKED: Final = "action_walked"

#: How a walk occurrence is written into a tag or an action identifier.
STAMP_FORMAT: Final = "%Y%m%dT%H%M"

#: Groups every alert about one walk under a single companion-app notification, so
#: a revised recommendation replaces the one it supersedes instead of stacking a
#: second, contradictory message underneath it.
TAG_PREFIX: Final = "walk_the_dog_"


@dataclass(frozen=True, slots=True)
class WalkTarget:
    """Who to interrupt about one particular walk, and whether to interrupt at all.

    `services` empty means "use the entry-wide default device"; silencing a walk
    is what `muted` is for, so an empty list can never be mistaken for one.
    """

    services: tuple[str, ...] = ()
    muted: bool = False


#: A walk the user has not given its own devices or mute switch.
DEFAULT_TARGET: Final = WalkTarget()


class WalkNotifier:
    """Decides whether to speak, and says it once."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        notify_service: str | None,
        fire_event: bool,
        mute_entity: str | None,
        confirm_margin: timedelta | None = None,
    ) -> None:
        """Take the notification options; they only change when the entry reloads."""
        self.hass = hass
        self._default_service = notify_service
        self._fire_event = fire_event
        self._mute_entity = mute_entity
        self._confirm_margin = confirm_margin
        self._walk_start: datetime | None = None
        self._notified: Any = None
        self._confirmed = False

    @property
    def confirm_margin(self) -> timedelta | None:
        """How long before setting off the reassurance goes out, or None if never."""
        return self._confirm_margin

    def reset(self) -> None:
        """Forget what was said — a new walk, or alerting switched back on."""
        self._walk_start = None
        self._notified = None
        self._confirmed = False

    @property
    def away(self) -> bool:
        """True while the tracked person or device is away from home."""
        if not self._mute_entity:
            return False
        state = self.hass.states.get(self._mute_entity)
        return state is None or state.state != STATE_HOME

    def services_for(self, target: WalkTarget) -> tuple[str, ...]:
        """The devices this walk's push goes to — its own, else the default one."""
        if target.services:
            return target.services
        return (self._default_service,) if self._default_service else ()

    async def async_process(
        self,
        data: WalkData,
        now: datetime,
        *,
        arm_at: datetime,
        confirm_at: datetime | None = None,
        target: WalkTarget = DEFAULT_TARGET,
    ) -> None:
        """Consider this cycle's result and dispatch if it is news.

        `arm_at` is `T - earlier_margin`. Before it, nothing is sent even when the
        forecast already looks bad: a recommendation to leave an hour early is not
        actionable two hours in advance, and it would only be superseded. After the
        advice expires — its suggested start has passed, or the walk has begun and
        there was no dry window to move to — nothing is sent either.

        `confirm_at` is the optional reassurance moment — `confirm_margin` before the
        walk actually sets off. `None` switches it off, which is the default: silence
        already means "nothing changed", and this is for users who would rather be
        told so than infer it.

        `target` is the walk's own notification setting; a muted walk still scores,
        still updates the sensor and still fires the event, it just says nothing.
        """
        if data.walk_start != self._walk_start:
            self.reset()
            self._walk_start = data.walk_start

        recommendation = data.recommendation
        if recommendation is None or now < arm_at:
            return
        if not any(source.contributed for source in recommendation.sources):
            # Zero contributing sources: never guess, never notify (phase 0 rule).
            return

        due = confirm_at is not None and now >= confirm_at

        if self._is_news(recommendation, now):
            # The decision is what advances, not the delivery: a muted alert is
            # suppressed, not queued, so coming home does not release a stale message.
            self._notified = recommendation
            # An alert sent at the reassurance moment *is* the reassurance.
            self._confirmed = self._confirmed or due
            await self._async_dispatch(data, recommendation, target, key=None)
            return

        if not due or self._confirmed or self._notified is None:
            return
        key = self._confirmation_key(recommendation, now)
        if key is None:
            return
        self._confirmed = True
        await self._async_dispatch(data, recommendation, target, key=key)

    def _is_news(self, recommendation: Recommendation, now: datetime) -> bool:
        """Whether this recommendation is worth interrupting the user with."""
        if recommendation.direction not in ALERT_DIRECTIONS:
            return False
        if not is_actionable(recommendation, now):
            # The window this cycle is still watching has outlived the advice in it.
            return False
        if not is_material_change(self._notified, recommendation):
            return False
        # The advice expired; the forecast behind it did not. Say nothing.
        return not superseded_by_the_clock(self._notified, recommendation, now)

    def _confirmation_key(self, recommendation: Recommendation, now: datetime) -> str | None:
        """Which reassurance this is, or None when there is nothing to reassure about.

        Two things are worth saying shortly before the door. That the plan the user
        was given still stands — and, more importantly, that the rain has gone and
        the walk is back to its normal time: `later` relaxing to `none` is not an
        alert direction, so without this the user would sit waiting for a 14:00
        window that stopped being necessary at 13:00.
        """
        if recommendation.direction == DIRECTION_NONE:
            return TEXT_STAND_DOWN
        if recommendation.direction in ALERT_DIRECTIONS and is_actionable(recommendation, now):
            return TEXT_CONFIRMED
        return None

    async def _async_dispatch(
        self,
        data: WalkData,
        recommendation: Recommendation,
        target: WalkTarget,
        *,
        key: str | None,
    ) -> None:
        """Fire the event and, unless muted, push. `key` None means a normal alert."""
        payload = data.payload()
        muted = target.muted or self.away
        payload["muted"] = muted
        payload["confirmation"] = key is not None
        if self._fire_event:
            self.hass.bus.async_fire(EVENT_ALERT, payload)
        if not muted:
            await self._async_send(recommendation, self.services_for(target), key=key)

    async def _async_send(
        self,
        recommendation: Recommendation,
        services: tuple[str, ...],
        *,
        key: str | None,
    ) -> None:
        """Call every companion-app notify service this walk is addressed to."""
        registered = [service for service in services if self._registered(service)]
        if not registered:
            return
        texts = await self._async_translations()
        title, message = _compose(texts, recommendation, key)
        data = {
            "tag": walk_tag(self._walk_start),
            "actions": [
                {
                    "action": walked_action(self._walk_start),
                    "title": _lookup(texts, TEXT_ACTION_WALKED, {}),
                }
            ],
        }
        for service in registered:
            await self.hass.services.async_call(
                NOTIFY_DOMAIN,
                service,
                {"title": title, "message": message, "data": data},
                blocking=False,
            )

    async def async_clear(self, target: WalkTarget, walk_start: datetime | None) -> None:
        """Take this walk's notification off every phone it was sent to.

        Only the device whose button was tapped dismisses its own copy, so without
        this the other phones would go on showing advice about a walk that is over.
        """
        tag = walk_tag(walk_start)
        for service in self.services_for(target):
            if not self._registered(service):
                continue
            await self.hass.services.async_call(
                NOTIFY_DOMAIN,
                service,
                {"message": CLEAR_NOTIFICATION, "data": {"tag": tag}},
                blocking=False,
            )

    def _registered(self, service: str) -> bool:
        """Whether a configured device still has its notify service, warning if not."""
        if self.hass.services.has_service(NOTIFY_DOMAIN, service):
            return True
        _LOGGER.warning(
            "Notification service %s.%s is not registered; no push sent",
            NOTIFY_DOMAIN,
            service,
        )
        return False

    async def _async_translations(self) -> dict[str, str]:
        """Every string this module can say, in the user's language."""
        return await async_get_translations(
            self.hass, self.hass.config.language, TRANSLATION_CATEGORY, {DOMAIN}
        )


def _lookup(texts: dict[str, str], key: str, placeholders: dict[str, str]) -> str:
    """One translated string, formatted — falling back to the key if it is missing."""
    template = texts.get(f"component.{DOMAIN}.{TRANSLATION_CATEGORY}.{TEXT_PREFIX}{key}")
    if template is None:
        return key
    try:
        return template.format(**placeholders)
    except KeyError, IndexError:
        # A translation with a placeholder we do not supply must not break the alert.
        return template


def _local_time(moment: datetime | None) -> str:
    """`HH:MM` in the user's timezone — how a walk time is written everywhere else."""
    return "" if moment is None else dt_util.as_local(moment).strftime("%H:%M")


def _compose(
    texts: dict[str, str], recommendation: Recommendation, key: str | None
) -> tuple[str, str]:
    """Title and body for one message. `key` None means the ordinary alert."""
    shift = recommendation.shift
    placeholders = {
        "scheduled": _local_time(recommendation.scheduled_start),
        "recommended": _local_time(recommendation.recommended_start),
        # The other half of the advice: a walk that starts later also ends later,
        # and whether that still fits the evening is the user's call, not ours.
        "until": _local_time(recommendation.recommended_end),
        "shift": str(abs(int(shift.total_seconds() // 60))) if shift else "0",
        "duration": str(recommendation.duration_s // 60),
        "intensity": recommendation.peak_intensity,
    }
    title = _lookup(texts, "title", placeholders)
    if key is not None:
        return title, _lookup(texts, key, placeholders)
    message = _lookup(texts, recommendation.direction, placeholders)
    if recommendation.provisional and recommendation.recommended_start is not None:
        message = f"{message} {_lookup(texts, TEXT_PROVISIONAL, placeholders)}"
    return title, message


def _stamp(walk_start: datetime | None) -> str:
    """One walk occurrence, as a string that survives a trip through a phone."""
    return "unknown" if walk_start is None else walk_start.strftime(STAMP_FORMAT)


def walk_tag(walk_start: datetime | None) -> str:
    """Companion-app notification tag: one per walk occurrence.

    Keyed on the walk's UTC start, so every revision of the same walk's advice
    replaces the last one on the phone, and tomorrow's walk gets a tag of its own.
    """
    return TAG_PREFIX + _stamp(walk_start)


def walked_action(walk_start: datetime | None) -> str:
    """Identifier for this walk's "I have already gone" button.

    The walk is encoded in the action string rather than passed alongside it: the
    action is the one field both companion apps are guaranteed to hand back, and a
    button tapped from yesterday's leftover notification must not close today's walk.
    """
    return f"{ACTION_WALKED}_{_stamp(walk_start)}"


def walk_start_from_action(action: str) -> datetime | None:
    """The walk a tapped button belongs to, or None if it is not one of ours."""
    prefix = f"{ACTION_WALKED}_"
    if not action.startswith(prefix):
        return None
    try:
        return datetime.strptime(action[len(prefix) :], STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


__all__ = [
    "ALERT_DIRECTIONS",
    "DEFAULT_TARGET",
    "TAG_PREFIX",
    "TEXT_ACTION_WALKED",
    "TEXT_CONFIRMED",
    "TEXT_PREFIX",
    "TEXT_PROVISIONAL",
    "TEXT_STAND_DOWN",
    "TRANSLATION_CATEGORY",
    "WalkNotifier",
    "WalkTarget",
    "walk_start_from_action",
    "walk_tag",
    "walked_action",
]
