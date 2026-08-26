"""The two entities: one recommendation sensor and the switch that gates everything.

Contract in docs/ARCHITECTURE.md § Outputs. The sensor is deliberately singular —
it always speaks about the walk the coordinator is watching — and its attributes
carry the whole explanation, per-source verdicts included.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from homeassistant.const import ATTR_ATTRIBUTION, STATE_OFF, STATE_ON, STATE_UNKNOWN, Platform
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.walk_the_dog.const import SOURCE_ICON_EU, SOURCE_KNMI
from custom_components.walk_the_dog.coordinator import WalkCoordinator
from custom_components.walk_the_dog.engine import DIRECTION_EARLIER
from custom_components.walk_the_dog.sensor import OPTIONS

from .conftest import ARM_AT, WALK_START, WINDOW_START, hourly_sources, run_cycle, setup_entry

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import FakeFetch

IDLE = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)

SENSOR = "sensor.walk_the_dog_walk_recommendation"
SWITCH = "switch.walk_the_dog_alerting"
WINDOW = "binary_sensor.walk_the_dog_walk_window"

RAIN_AT_FIVE = [0.0, 0.0, 3.0, 3.0, 0.0]
NO_RAIN = [0.0, 0.0, 0.0, 0.0, 0.0]


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


def _state(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def test_one_entity_per_question_on_one_device(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: WalkCoordinator
) -> None:
    """Three entities, three different questions — and no second recommendation.

    "What should I do about the next walk" is the sensor, "is anything running" is
    the binary sensor, "should anything run at all" is the switch. A second
    recommendation sensor would only be ambiguous, which is why there is one.
    """
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)

    assert sorted(entity.entity_id for entity in entities) == [WINDOW, SENSOR, SWITCH]
    assert {entity.domain for entity in entities} == {
        Platform.BINARY_SENSOR,
        Platform.SENSOR,
        Platform.SWITCH,
    }
    assert len({entity.device_id for entity in entities}) == 1


async def test_the_sensor_is_unknown_before_any_forecast(
    hass: HomeAssistant, coordinator: WalkCoordinator
) -> None:
    """Outside the polling window nothing has been fetched, and it says so."""
    state = _state(hass, SENSOR)

    assert state.state == STATE_UNKNOWN
    assert state.attributes["scheduled_start"] == WALK_START.isoformat()
    assert state.attributes["polling"] is False
    assert state.attributes["alerting"] is True


async def test_the_sensor_reports_the_recommendation(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """State says what to do; attributes say why, per source."""
    await run_cycle(hass, freezer, ARM_AT)

    state = _state(hass, SENSOR)

    assert state.state == DIRECTION_EARLIER
    assert state.attributes["options"] == OPTIONS
    assert (
        state.attributes["recommended_start"]
        == datetime(2026, 8, 25, 4, 30, tzinfo=UTC).isoformat()
    )
    assert (
        state.attributes["recommended_end"] == datetime(2026, 8, 25, 5, 0, tzinfo=UTC).isoformat()
    )
    assert state.attributes["shift_min"] == -30
    # Only the hourly models answered, so no radar has seen the suggested window.
    assert state.attributes["provisional"] is True
    assert state.attributes["risk"] == 1.0
    assert state.attributes["confidence"] == 0.8
    assert state.attributes["expected_intensity"] == "moderate"
    assert state.attributes["horizon_limited"] is False
    assert state.attributes["polling"] is True
    assert {source["source_id"] for source in state.attributes["sources"]} == {
        SOURCE_ICON_EU,
        SOURCE_KNMI,
    }
    assert "Open-Meteo" in state.attributes[ATTR_ATTRIBUTION]


async def test_a_dry_walk_reports_ok(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """`ok` is the user-facing word for the engine's `none`."""
    fetch.build = lambda now: hourly_sources(now, NO_RAIN)

    await run_cycle(hass, freezer, WINDOW_START)

    assert _state(hass, SENSOR).state == "ok"


async def test_the_switch_defaults_to_on(hass: HomeAssistant, coordinator: WalkCoordinator) -> None:
    """Someone who installs a rain alarm wants the rain alarm."""
    assert _state(hass, SWITCH).state == STATE_ON
    assert coordinator.enabled is True


async def test_the_switch_turns_alerting_off_and_on(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The service call is what a user actually does, so that is what is tested."""
    await run_cycle(hass, freezer, WINDOW_START)
    assert fetch.calls == 1

    await hass.services.async_call(
        Platform.SWITCH, "turn_off", {"entity_id": SWITCH}, blocking=True
    )

    assert _state(hass, SWITCH).state == STATE_OFF
    assert coordinator.enabled is False
    assert _state(hass, SENSOR).attributes["alerting"] is False

    await hass.services.async_call(Platform.SWITCH, "turn_on", {"entity_id": SWITCH}, blocking=True)

    assert _state(hass, SWITCH).state == STATE_ON
    assert fetch.calls == 2


async def test_the_switch_survives_a_restart(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A restored `off` keeps the integration silent — and stops it fetching."""
    mock_restore_cache(hass, (State(SWITCH, STATE_OFF),))
    freezer.move_to(WINDOW_START)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    coordinator = await setup_entry(hass, entry)

    assert _state(hass, SWITCH).state == STATE_OFF
    assert coordinator.enabled is False
    assert fetch.calls == 0


async def test_entities_are_named_from_the_translations(
    hass: HomeAssistant, coordinator: WalkCoordinator
) -> None:
    """No hard-coded English in the code: both names come from strings.json."""
    assert _state(hass, SENSOR).attributes["friendly_name"] == "Walk the dog Walk recommendation"
    assert _state(hass, SWITCH).attributes["friendly_name"] == "Walk the dog Alerting"


async def test_the_sensor_reports_what_the_cycle_cost(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A walk window may stay open for hours, so its cost is visible, not implied.

    The fetch itself is faked in this suite, so nothing is actually spent — what is
    asserted here is that the ceiling every adapter polices itself against is
    published, and that the counter is a number rather than a placeholder.
    """
    await run_cycle(hass, freezer, ARM_AT)

    state = _state(hass, SENSOR)

    assert state.attributes["requests_hourly_cap"] > 0
    assert state.attributes["requests_last_hour"] == 0


async def test_the_window_sensor_follows_the_polling_window(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """ "Is anything running" is a different question from "what should I do"."""
    assert _state(hass, WINDOW).state == STATE_OFF

    await run_cycle(hass, freezer, WINDOW_START)

    window = _state(hass, WINDOW)

    assert window.state == STATE_ON
    assert window.attributes["scheduled_start"] == WALK_START.isoformat()
    assert window.attributes["alerting"] is True
