"""The one line an alert leaves in the recommendation sensor's "Activity" list.

The screen a tapped notification opens (docs/ARCHITECTURE.md § Outputs) used to
show only the state word, because the state is an enum and the recommended hour
lives in the attributes. These tests are about the line that now names the hour:
that it exists, that it is filed under the sensor the user is looking at, that it
is a couple of words rather than a sentence, and that the reassurance which
changes nothing is kept off that screen.

`logbook` itself is not set up here. The platform's whole contract is the callback
it registers, so the tests register it the way Home Assistant would and call it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.walk_the_dog.const import (
    ATTR_DIRECTION,
    ATTR_SUMMARY,
    CONF_CONFIRM_MARGIN_MIN,
    DOMAIN,
    EVENT_ALERT,
    INTEGRATION_NAME,
)
from custom_components.walk_the_dog.engine import DIRECTION_EARLIER
from custom_components.walk_the_dog.logbook import async_describe_events

from .conftest import ARM_AT, WALK_START, hourly_sources, run_cycle, setup_entry

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.walk_the_dog.coordinator import WalkCoordinator

    from .conftest import FakeFetch

IDLE = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)

#: The sensor the line belongs to — the one a tapped notification opens.
SENSOR = "sensor.walk_the_dog_walk_recommendation"

#: Dry until 05:00 UTC, then rain: the 05:00 walk should be moved earlier.
RAIN_AT_FIVE = [0.0, 0.0, 3.0, 3.0, 0.0]
NO_RAIN = [0.0, 0.0, 0.0, 0.0, 0.0]

#: A log entry is a couple of words and a time. Well above what the texts actually
#: render to, and well below a sentence: this is a guard against a translation
#: quietly turning into prose, not a typographic rule.
MAX_LINE = 40


@pytest.fixture
def alerts(hass: HomeAssistant) -> list[Event[Any]]:
    """Capture the `walk_the_dog_alert` events the logbook line is rendered from."""
    return async_capture_events(hass, EVENT_ALERT)


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> WalkCoordinator:
    """A set-up entry on an idle morning, rain due during the scheduled walk."""
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    return await setup_entry(hass, entry)


@pytest.fixture
async def confirming(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> WalkCoordinator:
    """The same entry, asked to say something 15 minutes before setting off."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_CONFIRM_MARGIN_MIN: 15}
    )
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    return await setup_entry(hass, entry)


def _describe(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Render one alert payload the way the logbook integration would."""
    described: dict[str, Callable[[Event[Any]], dict[str, str]]] = {}

    def collect(
        domain: str, event_name: str, callback: Callable[[Event[Any]], dict[str, str]]
    ) -> None:
        assert domain == DOMAIN
        described[event_name] = callback

    async_describe_events(hass, collect)

    assert EVENT_ALERT in described, "the alert event is the only one described"
    return described[EVENT_ALERT](Event(EVENT_ALERT, data))


async def test_the_alert_carries_its_own_short_line(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    alerts: list[Event[Any]],
) -> None:
    """The notifier renders the line, because only it has the translations loaded."""
    await run_cycle(hass, freezer, ARM_AT)

    payload = alerts[0].data

    assert payload[ATTR_DIRECTION] == DIRECTION_EARLIER
    assert payload[ATTR_SUMMARY] == "Earlier — 04:30"
    assert payload[ATTR_ENTITY_ID] == SENSOR


async def test_the_line_names_the_hour_under_the_sensor(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    alerts: list[Event[Any]],
) -> None:
    """The whole point: the screen the push opens now says *when*, not only *that*."""
    await run_cycle(hass, freezer, ARM_AT)

    line = _describe(hass, alerts[0].data)

    assert "04:30" in line[LOGBOOK_ENTRY_MESSAGE]
    assert line[LOGBOOK_ENTRY_ENTITY_ID] == SENSOR
    assert line[LOGBOOK_ENTRY_NAME] == hass.states.get(SENSOR).name
    assert len(line[LOGBOOK_ENTRY_MESSAGE]) <= MAX_LINE


async def test_a_revised_recommendation_earns_a_line_of_its_own(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    alerts: list[Event[Any]],
) -> None:
    """The hour moving is the whole subject of this log, so a move is worth a line."""
    await run_cycle(hass, freezer, ARM_AT)
    fetch.build = lambda now: hourly_sources(now, NO_RAIN)

    await run_cycle(hass, freezer, ARM_AT + timedelta(minutes=20))

    lines = [_describe(hass, alert.data) for alert in alerts]

    assert len(lines) == 1, "a walk that stopped needing advice is not a second alert"
    assert lines[0][LOGBOOK_ENTRY_ENTITY_ID] == SENSOR


async def test_the_reassurance_is_kept_off_the_sensors_screen(
    hass: HomeAssistant,
    confirming: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    alerts: list[Event[Any]],
) -> None:
    """A plan that has not changed must not push the line that matters out of sight.

    Home Assistant renders every instance of an event type it has been taught, so
    the confirmation is still described — it just names no entity, which is what
    keeps it out of the sensor's own list while leaving it in the whole-home one.
    """
    await run_cycle(hass, freezer, ARM_AT)
    await run_cycle(hass, freezer, WALK_START - timedelta(minutes=15))

    assert len(alerts) == 2, "the alert, then the reassurance before setting off"
    confirmation = alerts[1].data
    assert confirmation["confirmation"] is True
    assert confirmation[ATTR_ENTITY_ID] is None

    line = _describe(hass, confirmation)

    assert LOGBOOK_ENTRY_ENTITY_ID not in line
    assert line[LOGBOOK_ENTRY_MESSAGE], "a described event with no message is a blank row"
    assert len(line[LOGBOOK_ENTRY_MESSAGE]) <= MAX_LINE


async def test_the_rain_going_away_earns_a_line(
    hass: HomeAssistant,
    confirming: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    alerts: list[Event[Any]],
) -> None:
    """The stand-down is a confirmation and is filed all the same: the hour moved back."""
    await run_cycle(hass, freezer, ARM_AT)
    fetch.build = lambda now: hourly_sources(now, NO_RAIN)

    await run_cycle(hass, freezer, WALK_START - timedelta(minutes=15))

    assert len(alerts) == 2
    line = _describe(hass, alerts[1].data)

    assert alerts[1].data[ATTR_ENTITY_ID] == SENSOR
    assert line[LOGBOOK_ENTRY_ENTITY_ID] == SENSOR
    assert len(line[LOGBOOK_ENTRY_MESSAGE]) <= MAX_LINE


async def test_an_alert_from_before_the_line_existed_still_reads(
    hass: HomeAssistant, coordinator: WalkCoordinator
) -> None:
    """Described events are rendered out of the recorder, so old ones turn up too.

    A 1.1.0 payload has neither a summary nor an entity id. It must still produce a
    row with something in it — the alternative is a blank line in the user's history.
    """
    line = _describe(hass, {ATTR_DIRECTION: DIRECTION_EARLIER, "confirmation": False})

    assert line[LOGBOOK_ENTRY_MESSAGE] == DIRECTION_EARLIER
    assert line[LOGBOOK_ENTRY_NAME] == INTEGRATION_NAME
    assert LOGBOOK_ENTRY_ENTITY_ID not in line


async def test_a_line_about_a_deleted_sensor_still_names_the_integration(
    hass: HomeAssistant,
) -> None:
    """No config entry at all: the platform is loaded by logbook, not by the entry."""
    line = _describe(hass, {ATTR_SUMMARY: "Later — 05:20", ATTR_ENTITY_ID: "sensor.long_gone"})

    assert line[LOGBOOK_ENTRY_NAME] == INTEGRATION_NAME
    assert line[LOGBOOK_ENTRY_MESSAGE] == "Later — 05:20"
