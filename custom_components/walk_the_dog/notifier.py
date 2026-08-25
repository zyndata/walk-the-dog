"""Notification dispatch, material-change detection and auto-mute.

Implements docs/ARCHITECTURE.md § Coordinator scheduling, notification dispatch:
the first message goes out at `T - earlier_margin` — the latest moment at which
"go earlier" is still actionable — and afterwards only when the recommendation
changes materially (`engine.is_material_change`), so a forecast wobbling around
the threshold cannot notify twice in an hour.

Nothing is ever sent about a walk that looks dry: silence means "go as planned".

The `walk_the_dog_alert` event fires whenever a notification *would* fire, even
when auto-mute suppresses the push — an automation may well want to know while
nobody is home. Its payload is `WalkData.payload()`, documented in docs/CONFIG.md.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import STATE_HOME
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_ALERT, NOTIFY_DOMAIN
from .engine import (
    DIRECTION_EARLIER,
    DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW,
    is_material_change,
)

if TYPE_CHECKING:
    from datetime import datetime

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


class WalkNotifier:
    """Decides whether to speak, and says it once."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        notify_service: str | None,
        fire_event: bool,
        mute_entity: str | None,
    ) -> None:
        """Take the notification options; they only change when the entry reloads."""
        self.hass = hass
        self._service = notify_service
        self._fire_event = fire_event
        self._mute_entity = mute_entity
        self._walk_start: datetime | None = None
        self._notified: Any = None

    def reset(self) -> None:
        """Forget what was said — a new walk, or alerting switched back on."""
        self._walk_start = None
        self._notified = None

    @property
    def muted(self) -> bool:
        """True while the tracked person or device is away from home."""
        if not self._mute_entity:
            return False
        state = self.hass.states.get(self._mute_entity)
        return state is None or state.state != STATE_HOME

    async def async_process(self, data: WalkData, now: datetime, *, arm_at: datetime) -> None:
        """Consider this cycle's result and dispatch if it is news.

        `arm_at` is `T - earlier_margin`. Before it, nothing is sent even when the
        forecast already looks bad: a recommendation to leave an hour early is not
        actionable two hours in advance, and it would only be superseded.
        """
        if data.walk_start != self._walk_start:
            self.reset()
            self._walk_start = data.walk_start

        recommendation = data.recommendation
        if recommendation is None or now < arm_at:
            return
        if recommendation.direction not in ALERT_DIRECTIONS:
            return
        if not any(source.contributed for source in recommendation.sources):
            # Zero contributing sources: never guess, never notify (phase 0 rule).
            return
        if not is_material_change(self._notified, recommendation):
            return

        # The decision is what advances, not the delivery: a muted alert is
        # suppressed, not queued, so coming home does not release a stale message.
        self._notified = recommendation

        payload = data.payload()
        muted = self.muted
        payload["muted"] = muted
        if self._fire_event:
            self.hass.bus.async_fire(EVENT_ALERT, payload)
        if not muted:
            await self._async_send(recommendation)

    async def _async_send(self, recommendation: Recommendation) -> None:
        """Call the configured companion-app notify service."""
        if not self._service:
            return
        if not self.hass.services.has_service(NOTIFY_DOMAIN, self._service):
            _LOGGER.warning(
                "Notification service %s.%s is not registered; no push sent",
                NOTIFY_DOMAIN,
                self._service,
            )
            return
        title, message = await self._async_text(recommendation)
        await self.hass.services.async_call(
            NOTIFY_DOMAIN,
            self._service,
            {"title": title, "message": message},
            blocking=False,
        )

    async def _async_text(self, recommendation: Recommendation) -> tuple[str, str]:
        """Build the notification, in the user's language."""
        texts = await async_get_translations(
            self.hass, self.hass.config.language, TRANSLATION_CATEGORY, {DOMAIN}
        )
        shift = recommendation.shift
        placeholders = {
            "scheduled": _local_time(recommendation.scheduled_start),
            "recommended": _local_time(recommendation.recommended_start),
            "shift": str(abs(int(shift.total_seconds() // 60))) if shift else "0",
            "duration": str(recommendation.duration_s // 60),
            "intensity": recommendation.peak_intensity,
        }
        title = _lookup(texts, "title", placeholders)
        return title, _lookup(texts, recommendation.direction, placeholders)


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


__all__ = ["ALERT_DIRECTIONS", "TEXT_PREFIX", "TRANSLATION_CATEGORY", "WalkNotifier"]
