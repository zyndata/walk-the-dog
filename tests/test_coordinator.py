"""Coordinator scheduling: when a cycle runs, and — more importantly — when it does not.

The polling design is the whole low-end-hardware argument (docs/ARCHITECTURE.md
§ Coordinator scheduling): one cycle every 10 minutes inside a walk's window, zero
requests and zero timers outside it or while alerting is off. These tests hold the
clock still and count requests, because "zero polling" is not something a
behavioural assertion on the sensor would ever notice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.walk_the_dog.const import (
    CONF_LOCATION,
    DOMAIN,
    EVENT_MOBILE_APP_ACTION,
    SERVICE_WALKED,
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
)
from custom_components.walk_the_dog.coordinator import (
    CYCLE,
    SPRINT,
    WalkCoordinator,
    publish_settle,
)
from custom_components.walk_the_dog.engine import (
    DIRECTION_EARLIER,
    DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW,
    DIRECTION_NONE,
    DIRECTION_UNKNOWN,
)
from custom_components.walk_the_dog.notifier import walked_action

from .conftest import (
    ARM_AT,
    CHMI_GEOMETRY,
    ENTRY_OPTIONS,
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


# --- "I have already gone" ------------------------------------------------


async def test_the_service_closes_the_walk_and_stops_polling(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Once the dog is out, every further request buys a decision nobody will make."""
    await run_cycle(hass, freezer, ARM_AT)
    spent = fetch.calls

    await hass.services.async_call(DOMAIN, SERVICE_WALKED, blocking=True)

    assert coordinator.data.active is False
    assert coordinator.data.walk_start == WALK_START + timedelta(days=1)

    await run_cycle(hass, freezer, ARM_AT + CYCLE)
    await run_cycle(hass, freezer, WALK_START)

    assert fetch.calls == spent
    # Alerting itself is untouched — this was about one walk, not the integration.
    assert coordinator.enabled is True


async def test_a_tapped_button_closes_the_walk_it_names(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The companion app hands back the action string; that is what identifies the walk."""
    await run_cycle(hass, freezer, ARM_AT)

    hass.bus.async_fire(EVENT_MOBILE_APP_ACTION, {"action": walked_action(WALK_START)})
    await hass.async_block_till_done()

    assert coordinator.data.walk_start == WALK_START + timedelta(days=1)


async def test_a_button_from_another_walk_is_ignored(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A leftover notification from yesterday must not close today's walk."""
    await run_cycle(hass, freezer, ARM_AT)

    hass.bus.async_fire(
        EVENT_MOBILE_APP_ACTION, {"action": walked_action(WALK_START - timedelta(days=1))}
    )
    await hass.async_block_till_done()

    assert coordinator.data.walk_start == WALK_START


async def test_another_integrations_notification_action_is_ignored(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The bus event is shared with every integration that puts buttons on a push."""
    await run_cycle(hass, freezer, ARM_AT)

    hass.bus.async_fire(EVENT_MOBILE_APP_ACTION, {"action": "SOMEONE_ELSES_BUTTON"})
    await hass.async_block_till_done()

    assert coordinator.data.walk_start == WALK_START


# --- the sprint cadence ---------------------------------------------------


def _cycle_moments(fetch: FakeFetch, values: list[float]) -> list[datetime]:
    """Record the moment of every cycle the coordinator runs."""
    moments: list[datetime] = []

    def build(now: datetime) -> tuple[list, list]:
        moments.append(now)
        return hourly_sources(now, values)

    fetch.build = build
    return moments


async def _run_minute_by_minute(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, until: datetime
) -> None:
    """Advance a minute at a time so whatever timer is armed fires when it is due."""
    moment = WINDOW_START
    while moment <= until:
        await run_cycle(hass, freezer, moment)
        moment += timedelta(minutes=1)


@pytest.fixture
async def chmi_entry(hass: HomeAssistant) -> MockConfigEntry:
    """The same walk, moved inside the CHMI composite — the one place a 5-minute source exists."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Walk the dog",
        data={
            CONF_LOCATION: {
                "latitude": CHMI_GEOMETRY.latitude,
                "longitude": CHMI_GEOMETRY.longitude,
            }
        },
        options=ENTRY_OPTIONS,
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


async def test_the_last_stretch_before_setting_off_runs_at_five_minutes(
    hass: HomeAssistant,
    chmi_entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A shower can build inside one 10-minute slot, so the approach is watched twice as often.

    The sprint runs into every moment the user might walk out of the door, and there
    are two of them here. The walk is at 05:00 and the recommendation moves it to
    04:30, so the first sprint covers 04:10-04:30; once 04:30 passes unused the
    suggestion lapses and the scheduled 05:00 becomes the moment again, so a second
    sprint covers 04:40-05:00.
    """
    freezer.move_to(IDLE)
    moments = _cycle_moments(fetch, RAIN_AT_FIVE)
    await setup_entry(hass, chmi_entry)

    await _run_minute_by_minute(hass, freezer, WALK_START)

    sprint = [moment for moment in moments if moment.minute % 10 == 5]

    assert sprint == [
        datetime(2026, 8, 25, 4, 15, tzinfo=UTC),
        datetime(2026, 8, 25, 4, 25, tzinfo=UTC),
        datetime(2026, 8, 25, 4, 45, tzinfo=UTC),
        datetime(2026, 8, 25, 4, 55, tzinfo=UTC),
    ]
    # The grid is subdivided, not replaced: the promised moment still gets a cycle.
    assert ARM_AT in moments
    assert min(b - a for a, b in pairwise(moments)) == SPRINT


async def test_a_location_without_a_fast_source_keeps_the_ten_minute_grid(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Polling faster than every source publishes would re-score identical bytes."""
    freezer.move_to(IDLE)
    moments = _cycle_moments(fetch, RAIN_AT_FIVE)
    await setup_entry(hass, entry)

    await _run_minute_by_minute(hass, freezer, WALK_START)

    assert moments
    assert all(moment.minute % 10 == 0 for moment in moments)


# --- following the provider's clock, not only our own ---------------------

#: The radar in these tests publishes at :03, :13, :23 — deliberately out of phase
#: with a cycle grid anchored to an 03:30 window start.
FRAME_PHASE_MIN = 3


def _newest_frame(now: datetime) -> datetime:
    """The frame a provider publishing at `FRAME_PHASE_MIN` past would have out by `now`."""
    minute = now.replace(second=0, microsecond=0)
    return minute - timedelta(minutes=(minute.minute - FRAME_PHASE_MIN) % 10)


async def test_a_cycle_follows_the_frame_it_is_waiting_for(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Every published frame is read about a minute after it lands, not up to ten.

    A fetch always returns the newest frame that exists, so the *data* was never
    stale — but the alert a new frame would trigger waited for the next cycle, and
    for a shower that builds in twenty minutes those minutes are the whole answer.
    """
    moments: list[datetime] = []
    frames: list[datetime] = []

    def build(now: datetime) -> tuple[list, list]:
        issued = _newest_frame(now)
        moments.append(now)
        frames.append(issued)
        series = make_series(SOURCE_LIBREWXR, LATE_CLEARANCE, start=WINDOW_START, issued_at=issued)
        age = int((now - issued).total_seconds())
        return [series], [make_status(SOURCE_LIBREWXR, age_s=age, contributed=True)]

    freezer.move_to(IDLE)
    fetch.build = build
    await setup_entry(hass, entry)

    await _run_minute_by_minute(hass, freezer, ARM_AT)

    published = sorted({frame for frame in frames if frame > WINDOW_START})

    assert published
    for frame in published:
        settle = publish_settle(SOURCE_LIBREWXR)
        assert any(frame <= moment <= frame + settle for moment in moments), frame


async def test_alignment_never_costs_a_scheduled_cycle(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The grid keeps running underneath, so a wrong guess about a frame cannot hurt.

    That is what makes the alignment safe to have: it may only ever pull a cycle
    earlier. Every cycle the plain grid would have run still runs, the promised
    notification moment included.
    """
    moments: list[datetime] = []

    def build(now: datetime) -> tuple[list, list]:
        moments.append(now)
        issued = _newest_frame(now)
        series = make_series(SOURCE_LIBREWXR, LATE_CLEARANCE, start=WINDOW_START, issued_at=issued)
        return [series], [make_status(SOURCE_LIBREWXR, age_s=0, contributed=True)]

    freezer.move_to(IDLE)
    fetch.build = build
    await setup_entry(hass, entry)

    await _run_minute_by_minute(hass, freezer, ARM_AT)

    # 03:30 to 04:00 inclusive — the plain grid over the stretch that was run.
    grid = {WINDOW_START + n * CYCLE for n in range(4)}

    assert grid <= set(moments)
    assert ARM_AT in moments
