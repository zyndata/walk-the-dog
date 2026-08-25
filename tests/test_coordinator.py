"""Coordinator scheduling: when a cycle runs, and — more importantly — when it does not.

The polling design is the whole low-end-hardware argument (docs/ARCHITECTURE.md
§ Coordinator scheduling): one cycle every 10 minutes inside a walk's window, zero
requests and zero timers outside it or while alerting is off. These tests hold the
clock still and count requests, because "zero polling" is not something a
behavioural assertion on the sensor would ever notice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.walk_the_dog.const import SOURCE_ICON_EU, SOURCE_KNMI, SOURCE_LIBREWXR
from custom_components.walk_the_dog.coordinator import CYCLE, WalkCoordinator
from custom_components.walk_the_dog.engine import (
    DIRECTION_EARLIER,
    DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW,
    DIRECTION_NONE,
    DIRECTION_UNKNOWN,
)

from .conftest import (
    ARM_AT,
    WALK_END,
    WALK_START,
    WINDOW_START,
    hourly_sources,
    make_series,
    make_status,
    run_cycle,
    setup_entry,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import FakeFetch

#: Well before the window opens at 03:30 UTC.
IDLE = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)

#: Hourly mm/h for 03:00 to 07:00 UTC: dry until 05:00, then raining.
#: The 05:00 walk is therefore wet and the 04:30 one is dry.
RAIN_AT_FIVE = [0.0, 0.0, 3.0, 3.0, 0.0]

#: Dry all morning.
NO_RAIN = [0.0, 0.0, 0.0, 0.0, 0.0]

#: Raining across the whole searchable range.
RAIN_ALL_MORNING = [3.0, 3.0, 3.0, 3.0, 3.0]

#: Ten-minute radar nowcast from 03:30 UTC: rain from 04:30 to 05:20 and nothing
#: after it, so the only dry window a 05:00 walk can reach is a later one.
LATE_CLEARANCE = [0.0] * 6 + [3.0] * 5 + [0.0] * 6


def nowcast(now: datetime, values: list[float]) -> tuple[list, list]:
    """A single ten-minute radar series — the only source with sub-hourly steps."""
    series = make_series(SOURCE_LIBREWXR, values, start=WINDOW_START, issued_at=now)
    return [series], [make_status(SOURCE_LIBREWXR, age_s=0, contributed=True)]


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> WalkCoordinator:
    """An entry set up on an idle morning, with alerting on and nothing fetched yet."""
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    return await setup_entry(hass, entry)


async def test_setup_makes_no_request_outside_the_window(
    coordinator: WalkCoordinator, fetch: FakeFetch
) -> None:
    """Starting up at midnight for an 05:00 walk must not touch a provider."""
    assert coordinator.enabled is True
    assert fetch.calls == 0
    assert coordinator.data.active is False
    assert coordinator.data.walk_start == WALK_START


async def test_nothing_runs_until_the_window_opens(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The armed timer points at the window start, not at the next ten minutes."""
    await run_cycle(hass, freezer, WINDOW_START - timedelta(minutes=1))

    assert fetch.calls == 0

    await run_cycle(hass, freezer, WINDOW_START)

    assert fetch.calls == 1
    assert coordinator.data.active is True


async def test_one_cycle_per_slot_inside_the_window(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Ten-minute cycles from the window opening to the end of the walk, and no more."""
    moment = WINDOW_START
    while moment <= WALK_END:
        await run_cycle(hass, freezer, moment)
        moment += CYCLE

    # 03:30 to 05:30 inclusive is 13 slots; the 05:30 wakeup closes the window.
    assert fetch.calls == 12

    await run_cycle(hass, freezer, WALK_END + CYCLE)

    assert fetch.calls == 12
    assert coordinator.data.walk_start == WALK_START + timedelta(days=1)
    assert coordinator.data.active is False


async def test_a_cycle_lands_exactly_on_the_notification_moment(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The cycle grid is anchored to the window start, so `T - E` is a cycle."""
    moments = []
    moment = WINDOW_START
    while moment < ARM_AT + CYCLE:
        await run_cycle(hass, freezer, moment)
        moments.append(coordinator.data.fetched_at)
        moment += CYCLE

    assert ARM_AT in moments


async def test_switching_off_stops_every_timer_and_request(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Zero polling while alerting is off — the acceptance criterion, measured."""
    await run_cycle(hass, freezer, WINDOW_START)
    assert fetch.calls == 1

    await coordinator.async_set_enabled(False)
    before = fetch.calls

    for offset in range(1, 8):
        await run_cycle(hass, freezer, WINDOW_START + offset * CYCLE)

    assert fetch.calls == before
    assert coordinator.data.active is False

    await coordinator.async_set_enabled(True)

    assert fetch.calls == before + 1


async def test_restarting_with_alerting_off_never_fetches(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The coordinator starts off, so a restart inside a window is silent too."""
    freezer.move_to(WINDOW_START + CYCLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    coordinator = await setup_entry(hass, entry)
    assert fetch.calls == 1  # the switch restored "on" and ran the cycle at once

    await coordinator.async_set_enabled(False)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.enabled is False
    assert fetch.calls == 1


@pytest.mark.parametrize(
    ("values", "direction", "recommended"),
    [
        (NO_RAIN, DIRECTION_NONE, WALK_START),
        (RAIN_AT_FIVE, DIRECTION_EARLIER, datetime(2026, 8, 25, 4, 30, tzinfo=UTC)),
        (RAIN_ALL_MORNING, DIRECTION_NO_DRY_WINDOW, None),
    ],
)
async def test_the_cycle_produces_the_engine_recommendation(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    values: list[float],
    direction: str,
    recommended: datetime | None,
) -> None:
    """Sources in, recommendation out — the coordinator adds no opinion of its own."""
    fetch.build = lambda now: hourly_sources(now, values)

    await run_cycle(hass, freezer, WINDOW_START)

    assert coordinator.data.direction == direction
    assert coordinator.data.recommendation.recommended_start == recommended


async def test_a_later_recommendation_extends_the_window(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Polling follows the walk we recommended, not only the one that was scheduled."""
    fetch.build = lambda now: nowcast(now, LATE_CLEARANCE)

    await run_cycle(hass, freezer, WALK_START)

    assert coordinator.data.direction == DIRECTION_LATER
    assert coordinator.data.recommendation.recommended_start == datetime(
        2026, 8, 25, 5, 20, tzinfo=UTC
    )

    # The scheduled walk has ended, but the recommended one has not.
    await run_cycle(hass, freezer, WALK_END)

    assert coordinator.data.active is True
    assert coordinator.data.walk_start == WALK_START


async def test_no_sources_means_unknown_and_no_guess(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Every provider down is reported as unknown, never as a dry walk."""
    fetch.build = lambda now: ([], [])

    await run_cycle(hass, freezer, WINDOW_START)

    assert coordinator.data.direction == DIRECTION_UNKNOWN
    assert coordinator.data.payload()["risk"] is None


async def test_the_payload_carries_the_per_source_breakdown(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The event and the sensor share one serialization, so it is checked once."""
    await run_cycle(hass, freezer, ARM_AT)

    payload = coordinator.data.payload()

    assert payload["direction"] == DIRECTION_EARLIER
    assert payload["scheduled_start"] == WALK_START.isoformat()
    assert payload["shift_min"] == -30
    assert payload["duration_min"] == 30
    assert payload["expected_intensity"] == "moderate"
    assert payload["degraded"] is False
    assert {source["source_id"] for source in payload["sources"]} == {SOURCE_ICON_EU, SOURCE_KNMI}
    assert all(source["contributed"] for source in payload["sources"])
    assert payload["data_age_s"] == 0


async def test_attributions_name_only_the_sources_that_voted(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Licences oblige us to credit what we used — and only what we used."""
    await run_cycle(hass, freezer, WINDOW_START)

    assert len(coordinator.data.attributions) == 2
    assert all("Open-Meteo" in text for text in coordinator.data.attributions)


async def test_unload_cancels_the_timer(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An unloaded entry leaves nothing armed behind it."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED

    await run_cycle(hass, freezer, WINDOW_START)

    assert fetch.calls == 0


async def test_an_empty_schedule_arms_nothing(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A schedule with no walk time has nothing to predict for, and says so."""
    freezer.move_to(IDLE)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "schedule": {"all": []}}
    )
    coordinator = await setup_entry(hass, entry)

    assert coordinator.data.walk_start is None
    assert coordinator.data.direction == DIRECTION_UNKNOWN

    await run_cycle(hass, freezer, WINDOW_START)

    assert fetch.calls == 0
