"""Notification dispatch: when the user is interrupted, and when they are not.

The promise is one message at `T - earlier_margin` and silence afterwards unless
something material changes (docs/ARCHITECTURE.md § Material change). A rain alarm
that cries every ten minutes is worse than none, so most of these tests assert
that nothing was sent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from pytest_homeassistant_custom_component.common import async_capture_events, async_mock_service

from custom_components.walk_the_dog.const import (
    CONF_AUTO_MUTE_ENTITY,
    CONF_FIRE_EVENT,
    CONF_NOTIFY_SERVICE,
    EVENT_ALERT,
    NOTIFY_DOMAIN,
)
from custom_components.walk_the_dog.coordinator import CYCLE, WalkCoordinator
from custom_components.walk_the_dog.engine import DIRECTION_EARLIER

from .conftest import (
    ARM_AT,
    WALK_START,
    WINDOW_START,
    hourly_sources,
    run_cycle,
    setup_entry,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant, ServiceCall
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import FakeFetch

IDLE = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)

NOTIFY_SERVICE = "mobile_app_test"
MUTE_ENTITY = "person.owner"

#: Dry until 05:00 UTC, then rain — the 05:00 walk should be moved earlier.
RAIN_AT_FIVE = [0.0, 0.0, 3.0, 3.0, 0.0]

#: Heavier rain at the same time: same direction, a different intensity class.
HEAVY_AT_FIVE = [0.0, 0.0, 9.0, 9.0, 0.0]

#: No rain at all — nothing to say.
NO_RAIN = [0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def notifications(hass: HomeAssistant) -> list[ServiceCall]:
    """Capture calls to the configured companion-app notify service."""
    return async_mock_service(hass, NOTIFY_DOMAIN, NOTIFY_SERVICE)


@pytest.fixture
def alerts(hass: HomeAssistant) -> list:
    """Capture the opt-in `walk_the_dog_alert` events."""
    return async_capture_events(hass, EVENT_ALERT)


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> WalkCoordinator:
    """An entry that notifies a device and fires the event, watching a person."""
    hass.states.async_set(MUTE_ENTITY, STATE_HOME)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NOTIFY_SERVICE: NOTIFY_SERVICE,
            CONF_FIRE_EVENT: True,
            CONF_AUTO_MUTE_ENTITY: MUTE_ENTITY,
        },
    )
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    return await setup_entry(hass, entry)


async def test_nothing_is_sent_before_the_arming_moment(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """A recommendation an hour and a half out is not actionable, so it is not sent."""
    await run_cycle(hass, freezer, WINDOW_START)
    await run_cycle(hass, freezer, WINDOW_START + CYCLE)

    assert coordinator.data.direction == DIRECTION_EARLIER
    assert notifications == []


async def test_the_alert_arrives_at_the_earlier_margin(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    alerts: list,
) -> None:
    """One message, at `T - earlier_margin`, naming both times."""
    await run_cycle(hass, freezer, WINDOW_START)
    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1
    message = notifications[0].data["message"]
    assert "05:00" in message
    assert "04:30" in message
    assert notifications[0].data["title"] == "Walk the dog"

    assert len(alerts) == 1
    assert alerts[0].data["direction"] == DIRECTION_EARLIER
    assert alerts[0].data["muted"] is False


async def test_an_unchanged_recommendation_is_not_repeated(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Every later cycle re-checks material change and stays quiet."""
    moment = ARM_AT
    while moment < WALK_START:
        await run_cycle(hass, freezer, moment)
        moment += CYCLE

    assert len(notifications) == 1


async def test_a_material_change_notifies_again(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Light rain becoming heavy is a different message, so it is sent."""
    await run_cycle(hass, freezer, ARM_AT)
    assert len(notifications) == 1

    fetch.build = lambda now: hourly_sources(now, HEAVY_AT_FIVE)
    await run_cycle(hass, freezer, ARM_AT + CYCLE)

    assert len(notifications) == 2


async def test_a_dry_walk_is_never_announced(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    alerts: list,
) -> None:
    """Silence means "go as planned" — the integration does not congratulate you."""
    fetch.build = lambda now: hourly_sources(now, NO_RAIN)

    await run_cycle(hass, freezer, ARM_AT)

    assert notifications == []
    assert alerts == []


async def test_no_contributing_source_is_never_announced(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    alerts: list,
) -> None:
    """Zero sources means unknown, and unknown is never worth waking someone for."""
    fetch.build = lambda now: ([], [])

    await run_cycle(hass, freezer, ARM_AT)

    assert notifications == []
    assert alerts == []


async def test_auto_mute_suppresses_the_push_but_not_the_event(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    alerts: list,
) -> None:
    """Away from home: no push, but an automation may still want to know."""
    hass.states.async_set(MUTE_ENTITY, STATE_NOT_HOME)

    await run_cycle(hass, freezer, ARM_AT)

    assert notifications == []
    assert len(alerts) == 1
    assert alerts[0].data["muted"] is True


async def test_alerting_switched_off_sends_nothing(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    alerts: list,
) -> None:
    """No cycles at all means no notifications at all."""
    await coordinator.async_set_enabled(False)

    await run_cycle(hass, freezer, ARM_AT)

    assert notifications == []
    assert alerts == []


async def test_the_next_walk_starts_a_fresh_conversation(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Tomorrow's identical forecast is news again — it is a different walk."""
    await run_cycle(hass, freezer, ARM_AT)
    assert len(notifications) == 1

    tomorrow = timedelta(days=1)
    fetch.build = lambda now: hourly_sources(
        now, RAIN_AT_FIVE, start=datetime(2026, 8, 26, 3, 0, tzinfo=UTC)
    )
    await run_cycle(hass, freezer, WINDOW_START + tomorrow)
    await run_cycle(hass, freezer, ARM_AT + tomorrow)

    assert len(notifications) == 2


async def test_an_unregistered_notify_service_is_reported_not_raised(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    alerts: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A phone configured before its companion service exists must not break a cycle."""
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_NOTIFY_SERVICE: "mobile_app_missing", CONF_FIRE_EVENT: True},
    )
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    await setup_entry(hass, entry)

    await run_cycle(hass, freezer, ARM_AT)

    assert len(alerts) == 1
    assert "mobile_app_missing" in caplog.text


async def test_no_notify_service_still_fires_the_event(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    alerts: list,
) -> None:
    """Push notification is optional; the event is a separate opt-in."""
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_FIRE_EVENT: True})
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    await setup_entry(hass, entry)

    await run_cycle(hass, freezer, ARM_AT)

    assert len(alerts) == 1
