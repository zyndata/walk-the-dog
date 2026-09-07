"""One line per alert in the recommendation sensor's own "Activity" list.

Tapping a push opens that sensor's more-info dialog (`notifier.py`), and the list
on it is the first thing the user reads. Until 1.2.0 it could only show the state
word — `later`, `ok`, `unknown` — because the state is a `SensorDeviceClass.ENUM`
with a closed set of options and the recommended hour lives in the attributes,
which the logbook does not display. So the screen that opened said *that* the walk
should move and never *to when*, while the notification that opened it said both.

This platform teaches Home Assistant to render `walk_the_dog_alert` — the event the
notifier fires for every alert — as a short line filed under that same sensor. It
is deliberately the thinnest thing that can work:

* **It renders nothing itself.** `async_describe_events` is registered once and the
  callback it hands over is synchronous, so it cannot await translations per event;
  `hass.config.language` can also change underneath it. The notifier already holds
  the loaded translations at the moment the alert is decided, so it writes the
  finished line into the payload and this module echoes it. Loading translations
  here at registration time was the alternative, and was rejected: it would freeze
  the language at whatever it was when Home Assistant started, and the sensor's
  history would end up written in two languages.
* **It decides nothing either.** Every alert gets a line, because Home Assistant
  renders every instance of an event type it has been taught to describe — there is
  no way to skip one, and a description with nothing in it yields a blank row rather
  than no row. What *is* decided, and again by the notifier, is where the line is
  filed: an alert carries the sensor's `entity_id` in its payload when it belongs on
  that sensor's screen, and carries `null` when it belongs only in the whole-home
  logbook. Only the reassurance that an unchanged plan still stands is filed that
  way (docs/CONFIG.md § Event payload).

Nothing here reaches back into the integration: a described event is rendered from
whatever the recorder stored, which is why lines appear for alerts fired before this
platform existed, and why the module must work with no config entry loaded at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import callback

from .const import ATTR_DIRECTION, ATTR_SUMMARY, DOMAIN, EVENT_ALERT, INTEGRATION_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event[Any]], dict[str, str]]], None],
) -> None:
    """Register how a `walk_the_dog_alert` reads in the logbook."""

    @callback
    def async_describe_alert(event: Event[Any]) -> dict[str, str]:
        """One alert as a line, filed under the sensor when it names one."""
        data = event.data
        entity_id = data.get(ATTR_ENTITY_ID)
        described = {
            LOGBOOK_ENTRY_NAME: _name(hass, entity_id),
            # An alert from before 1.2.0, replayed out of the recorder, has no
            # summary: name the direction rather than leave the row blank.
            LOGBOOK_ENTRY_MESSAGE: data.get(ATTR_SUMMARY) or data.get(ATTR_DIRECTION, ""),
        }
        if entity_id:
            described[LOGBOOK_ENTRY_ENTITY_ID] = entity_id
        return described

    async_describe_event(DOMAIN, EVENT_ALERT, async_describe_alert)


def _name(hass: HomeAssistant, entity_id: str | None) -> str:
    """What to call the thing this line is about.

    The sensor's current friendly name, so an entity the user renamed reads as they
    renamed it. The untranslated brand stands in when there is no entity to name —
    a line filed against the integration rather than the sensor, or one about a
    sensor that no longer exists.
    """
    state = None if entity_id is None else hass.states.get(entity_id)
    return INTEGRATION_NAME if state is None else state.name


__all__ = ["async_describe_events"]
