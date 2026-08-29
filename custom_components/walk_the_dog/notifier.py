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

When nothing of the full length is dry, the advice may be to walk a shorter one.
That message has to name the length as well as the time — "set off at 07:20" is
not the whole of it if the walk is twenty minutes shorter than usual — and so does
the confirmation that follows it.

Who is interrupted is decided per walk, not per integration: each configured walk
carries its own list of companion-app devices, its own mute switch and its own
away entity (docs/CONFIG.md § Per-walk alerts), because the morning walk and the
evening walk are often not the same person's job. The entry-wide device is added
to every walk's list — it is the phone that always hears about a walk — and the
combined list is de-duplicated, so naming that same phone on a walk as well still
buys exactly one push.

*Whether* a phone on that list is told is then decided one phone at a time, not
once for the walk. A companion-app device tracks itself, so each phone answers
for its own presence and one person leaving the house cannot take the alert away
from everybody else. The away entities are the fallback for a phone that cannot
answer — a device with no tracker, or one whose tracker is unavailable — and a
phone that nothing can answer for is notified rather than silently skipped.

The entry-wide device is exempt from all of it: mute, both away entities and its
own tracker. It is the phone the user asked to be told about every walk, and only
the alerting switch takes that away.

A push is a summary, and a summary is worth being able to re-read. Tapping one
opens the recommendation sensor rather than merely the app, and — on Android, which
is the platform that offers the choice — the message stays in the shade instead of
being taken away by the tap that opened it. What removes it is the walk ending, the
*Already went* button, or the user; not the act of reading it.

The `walk_the_dog_alert` event fires whenever a notification *would* fire, even
when nothing is sent — an automation may well want to know while nobody is home.
Its payload is `WalkData.payload()`, documented in docs/CONFIG.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import STATE_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_WALKED,
    CLEAR_NOTIFICATION,
    CONF_AUTO_MUTE_ENTITY,
    CONF_FIRE_EVENT,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
    ENTITY_KEY_RECOMMENDATION,
    EVENT_ALERT,
    NOTIFY_DOMAIN,
    NOTIFY_SERVICE_PREFIX,
)
from .engine import (
    DIRECTION_EARLIER,
    DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW,
    DIRECTION_NONE,
    DIRECTION_SHORTER,
    is_actionable,
    is_material_change,
    superseded_by_the_clock,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import WalkData
    from .engine import Recommendation

_LOGGER = logging.getLogger(__name__)

#: Directions worth interrupting the user for. `none` means the walk is fine and
#: `unknown` means we do not know — neither is news.
ALERT_DIRECTIONS: Final = frozenset(
    {DIRECTION_EARLIER, DIRECTION_LATER, DIRECTION_SHORTER, DIRECTION_NO_DRY_WINDOW}
)

#: strings.json section the notification texts live in, so phase 7 translates them
#: the same way as everything else the user reads. `common` is the only top-level
#: key Home Assistant allows for strings that belong to no form and no entity
#: (hassfest rejects any other), hence the explicit `notification_` prefix.
TRANSLATION_CATEGORY: Final = "common"
TEXT_PREFIX: Final = "notification_"

#: Appended when the recommended window is beyond the radar's reach, so the user
#: knows the suggestion is an early answer that is still being checked.
TEXT_PROVISIONAL: Final = "provisional"

#: The shapes the optional confirmation takes shortly before setting off: the plan
#: still stands, or the rain has gone and the walk is back to its normal time. A
#: shortened plan gets its own wording — "still on" must not quietly drop the one
#: thing that makes this advice unusual, which is that the walk is cut short.
TEXT_CONFIRMED: Final = "confirmed"
TEXT_CONFIRMED_SHORTER: Final = "confirmed_shorter"
TEXT_STAND_DOWN: Final = "stand_down"

#: Label on the one action button the push carries.
TEXT_ACTION_WALKED: Final = "action_walked"

#: How a walk occurrence is written into a tag or an action identifier.
STAMP_FORMAT: Final = "%Y%m%dT%H%M"

#: Where the companion app registers the phone a `mobile_app_*` notify service
#: belongs to. The two share a slug, which is the whole of the link between them.
TRACKER_DOMAIN: Final = "device_tracker"

#: A tracker in one of these states is not saying "away", it is saying nothing.
UNREADABLE_STATES: Final = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE})

#: Where tapping the body of a notification lands: the more-info dialog of the
#: recommendation sensor, whose attributes carry the whole answer — both times, the
#: per-source verdicts, how far the radar reached. Android reads `clickAction` and
#: iOS reads `url`; the `entityId:` scheme is the companion apps' own, and a target
#: neither app understands costs nothing more than the tap already did.
CLICK_ENTITY_PREFIX: Final = "entityId:"

#: Keeps an Android notification in the shade after it has been tapped. Without it
#: the tap opens Home Assistant and takes the advice away with it — which is exactly
#: the moment the user wants to read it again. The message is still swipeable; it is
#: sticky, not persistent, so the user is never left with one they cannot get rid of.
STICKY: Final = "true"

#: Groups every alert about one walk under a single companion-app notification, so
#: a revised recommendation replaces the one it supersedes instead of stacking a
#: second, contradictory message underneath it.
TAG_PREFIX: Final = "walk_the_dog_"


@dataclass(frozen=True, slots=True)
class WalkTarget:
    """Who to interrupt about one particular walk, and whether to interrupt at all.

    `services` holds the devices this walk adds to the entry-wide one, which is
    always notified as well; silencing a walk's own phones is what `muted` is for,
    so an empty list means "only the entry-wide device", never "notify nobody".

    `muted` silences this walk's own phones. It cannot silence the entry-wide
    device — only the alerting switch does that.

    `away_entity` replaces the entry-wide away entity for this walk alone, and
    both only ever answer for a phone that cannot answer for itself; `None` means
    the walk is happy with whichever person the entry watches.
    """

    services: tuple[str, ...] = ()
    muted: bool = False
    away_entity: str | None = None


#: A walk the user never opened the notification step for: entry-wide device,
#: entry-wide away entity, not muted.
DEFAULT_TARGET: Final = WalkTarget()


class WalkNotifier:
    """Decides whether to speak, and says it once."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        confirm_margin: timedelta | None = None,
    ) -> None:
        """Read the notification options off the entry; a change reloads it anyway.

        The entry rather than the four values it holds, because the notifier needs
        its id as well: that is what resolves the recommendation sensor a tapped
        message opens. `confirm_margin` stays a parameter — the coordinator derives
        it from the same options and has to agree with this about when it is due.
        """
        self.hass = hass
        self._entry_id = entry.entry_id
        options = entry.options
        self._default_service = options.get(CONF_NOTIFY_SERVICE)
        self._fire_event = bool(options.get(CONF_FIRE_EVENT, False))
        self._mute_entity = options.get(CONF_AUTO_MUTE_ENTITY)
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

    def has_spoken(self, walk_start: datetime | None) -> bool:
        """Whether anything was said about this particular walk.

        The coordinator asks before taking a finished walk's notification down: a
        walk nobody was told about has nothing on the phones to remove, and asking
        every device to clear a tag that was never used spends a service call
        saying nothing.
        """
        return (
            walk_start is not None and walk_start == self._walk_start and self._notified is not None
        )

    def away_entity_for(self, target: WalkTarget) -> str | None:
        """Whose absence stands in for a phone that cannot answer for itself."""
        return target.away_entity or self._mute_entity

    def is_away(self, target: WalkTarget = DEFAULT_TARGET) -> bool:
        """True while the person this walk falls back to is away from home.

        An entity that has no state yet counts as away: a tracker Home Assistant
        has not restored is not evidence that anybody is in.
        """
        entity_id = self.away_entity_for(target)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is None or state.state != STATE_HOME

    def services_for(self, target: WalkTarget) -> tuple[str, ...]:
        """Every device this walk is addressed to, each named exactly once.

        The entry-wide device is always among them; a walk's own devices are extra
        phones, not a replacement. `dict.fromkeys` is the de-duplication: the same
        service listed on the walk and as the entry-wide default — or twice within
        one walk — collapses to one recipient, and the order the user chose is kept.

        This is who the walk is *addressed* to. Who it reaches right now is
        `recipients_for`, which asks each phone whether it is at home.
        """
        services = [*target.services]
        if self._default_service:
            services.append(self._default_service)
        return tuple(dict.fromkeys(services))

    def recipients_for(self, target: WalkTarget) -> tuple[str, ...]:
        """The devices this walk's push reaches at this moment.

        Presence is a property of each phone, not of the walk: one person leaving
        the house must not take the alert away from everybody else who was going
        to be told.
        """
        return tuple(
            service for service in self.services_for(target) if self._reaches(service, target)
        )

    def _reaches(self, service: str, target: WalkTarget) -> bool:
        """Whether this one device is told about this walk right now.

        Three rules, in order. The entry-wide device always hears — it is the phone
        the user asked to be told about every walk, and only the alerting switch
        silences it. A walk's own phones obey its mute switch. Otherwise the phone
        answers for itself, and the away entity answers only when it cannot.
        """
        if service == self._default_service:
            return True
        if target.muted:
            return False
        at_home = self._device_is_home(service)
        if at_home is not None:
            return at_home
        return not self.is_away(target)

    def _device_is_home(self, service: str) -> bool | None:
        """Whether this phone is at home, or None when nothing can say.

        `notify.mobile_app_jan_phone` and `device_tracker.jan_phone` are the same
        phone: the companion app registers both from one device name, so the link
        costs the user no configuration at all. A phone whose tracker does not
        exist — or exists and cannot answer — returns None and is notified rather
        than skipped: a needless alert is a far cheaper mistake than a missed one.
        """
        entity_id = f"{TRACKER_DOMAIN}.{service.removeprefix(NOTIFY_SERVICE_PREFIX)}"
        state = self.hass.states.get(entity_id)
        if state is None or state.state in UNREADABLE_STATES:
            return None
        return state.state == STATE_HOME

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
            if recommendation.direction == DIRECTION_SHORTER:
                return TEXT_CONFIRMED_SHORTER
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
        recipients = self.recipients_for(target)
        # `muted` says nobody at all was reached, not that one phone was skipped:
        # an automation cares whether the advice got out, not to how many phones.
        payload["muted"] = not recipients
        payload["confirmation"] = key is not None
        if self._fire_event:
            self.hass.bus.async_fire(EVENT_ALERT, payload)
        if recipients:
            await self._async_send(recommendation, recipients, key=key)

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
        data: dict[str, Any] = {
            "tag": walk_tag(self._walk_start),
            "sticky": STICKY,
            "actions": [
                {
                    "action": walked_action(self._walk_start),
                    "title": _lookup(texts, TEXT_ACTION_WALKED, {}),
                }
            ],
        }
        if (click := self._click_target()) is not None:
            # The two companion apps spell the same idea differently; sending both
            # is what makes one message behave the same way on either phone.
            data["clickAction"] = click
            data["url"] = click
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

    def _click_target(self) -> str | None:
        """What a tapped notification should open, or None while it does not exist.

        Resolved at every send rather than stored once: the entity id belongs to the
        user, who may rename it, and the registry is the only thing that knows the
        current one. Before the sensor is registered — the first cycle of a fresh
        install — there is nothing to point at, and the tap falls back to opening
        the app, which is what it did before.
        """
        entity_id = er.async_get(self.hass).async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{self._entry_id}_{ENTITY_KEY_RECOMMENDATION}"
        )
        return None if entity_id is None else f"{CLICK_ENTITY_PREFIX}{entity_id}"

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
    except (KeyError, IndexError):
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
        # How long the walk being suggested actually is — the same number as
        # `duration` unless the advice is to cut the walk short.
        "recommended_duration": str(int(recommendation.recommended_duration.total_seconds()) // 60),
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
    "CLICK_ENTITY_PREFIX",
    "DEFAULT_TARGET",
    "STICKY",
    "TAG_PREFIX",
    "TEXT_ACTION_WALKED",
    "TEXT_CONFIRMED",
    "TEXT_CONFIRMED_SHORTER",
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
