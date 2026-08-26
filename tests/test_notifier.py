"""Notification dispatch: when the user is interrupted, and when they are not.

The promise is one message at `T - earlier_margin` and silence afterwards unless
something material changes (docs/ARCHITECTURE.md § Material change). A rain alarm
that cries every ten minutes is worse than none, so most of these tests assert
that nothing was sent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN
from pytest_homeassistant_custom_component.common import async_capture_events, async_mock_service

from custom_components.walk_the_dog.const import (
    CLEAR_NOTIFICATION,
    CONF_AUTO_MUTE_ENTITY,
    CONF_CONFIRM_MARGIN_MIN,
    CONF_FIRE_EVENT,
    CONF_NOTIFY_SERVICE,
    CONF_TARGET_AWAY_ENTITY,
    CONF_TARGET_MUTE,
    CONF_TARGET_SERVICES,
    CONF_WALK_TARGETS,
    EVENT_ALERT,
    NOTIFY_DOMAIN,
    SOURCE_LIBREWXR,
)
from custom_components.walk_the_dog.coordinator import CYCLE, WalkCoordinator
from custom_components.walk_the_dog.engine import DIRECTION_EARLIER
from custom_components.walk_the_dog.notifier import TAG_PREFIX, walked_action
from custom_components.walk_the_dog.schedule import KEY_ALL, target_key

from .conftest import (
    ARM_AT,
    WALK_END,
    WALK_HHMM,
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
    from homeassistant.core import HomeAssistant, ServiceCall
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import FakeFetch

IDLE = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)

NOTIFY_SERVICE = "mobile_app_test"
MUTE_ENTITY = "person.owner"
WALK_MUTE_ENTITY = "person.walker"

#: Two phones that share the dog: the standard walk is addressed to them by name.
MORNING_PHONE = "mobile_app_morning"
EVENING_PHONE = "mobile_app_evening"

#: The tracker the companion app registers beside each phone's notify service:
#: same slug, different domain. This is the whole of the link the notifier uses.
DEFAULT_TRACKER = "device_tracker.test"
MORNING_TRACKER = "device_tracker.morning"
EVENING_TRACKER = "device_tracker.evening"

#: Where the standard walk's own notification settings are stored.
WALK_KEY = target_key(KEY_ALL, WALK_HHMM)

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


async def test_the_alert_speaks_the_users_language(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """A Polish Home Assistant is told in Polish, from `translations/pl.json`.

    The whole chain is under test here, not the file: the texts are looked up at
    dispatch time under `hass.config.language`, and a key that does not resolve is
    sent as the bare key — which is exactly what would reach the phone if the
    Polish file were missing, misnamed or missing this string.
    """
    await hass.config.async_update(language="pl")

    await run_cycle(hass, freezer, WINDOW_START)
    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1
    sent = notifications[0].data
    assert sent["title"] == "Idź już z psem"
    assert sent["message"].startswith("Deszcz spodziewany około 05:00.")
    assert "Wyjdź o 04:30" in sent["message"]
    assert sent["data"]["actions"][0]["title"] == "Już byliśmy"


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


async def test_the_away_entity_does_not_silence_the_always_notified_device(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    alerts: list,
) -> None:
    """Nobody being home stops the walk's own phones, never the entry-wide one.

    That device is the phone the user asked to be told about every walk; only the
    alerting switch takes that away.
    """
    hass.states.async_set(MUTE_ENTITY, STATE_NOT_HOME)

    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1
    assert len(alerts) == 1
    assert alerts[0].data["muted"] is False


async def test_the_always_notified_device_hears_even_when_it_is_out(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Not even its own tracker silences it — "always" has no exceptions but one."""
    hass.states.async_set(DEFAULT_TRACKER, STATE_NOT_HOME)

    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1


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


@pytest.fixture
def morning(hass: HomeAssistant) -> list[ServiceCall]:
    """Capture the first phone's pushes."""
    return async_mock_service(hass, NOTIFY_DOMAIN, MORNING_PHONE)


@pytest.fixture
def evening(hass: HomeAssistant) -> list[ServiceCall]:
    """Capture the second phone's pushes."""
    return async_mock_service(hass, NOTIFY_DOMAIN, EVENING_PHONE)


async def setup_with_target(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    target: dict[str, object],
    *,
    auto_mute: str | None = None,
    default_device: str | None = NOTIFY_SERVICE,
) -> WalkCoordinator:
    """Set the entry up with an always-notified device and one walk-specific setting.

    `default_device=None` leaves the entry without one, which is the only way a
    walk can end up reaching nobody at all.
    """
    options: dict[str, object] = {
        **entry.options,
        CONF_FIRE_EVENT: True,
        CONF_WALK_TARGETS: {WALK_KEY: target},
    }
    if default_device:
        options[CONF_NOTIFY_SERVICE] = default_device
    if auto_mute:
        options[CONF_AUTO_MUTE_ENTITY] = auto_mute
    hass.config_entries.async_update_entry(entry, options=options)
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    return await setup_entry(hass, entry)


async def test_a_walks_own_devices_are_notified_as_well_as_the_default(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    morning: list[ServiceCall],
) -> None:
    """A walk's devices are extra phones, not a replacement for the default one."""
    await setup_with_target(hass, entry, fetch, freezer, {CONF_TARGET_SERVICES: [MORNING_PHONE]})

    await run_cycle(hass, freezer, ARM_AT)

    assert len(morning) == 1
    assert "05:00" in morning[0].data["message"]
    assert len(notifications) == 1
    assert notifications[0].data["message"] == morning[0].data["message"]


async def test_the_same_device_in_both_places_is_notified_once(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Naming the always-notified phone on a walk as well must not double the push.

    Easy to do by accident now that the two lists are added together rather than
    chosen between, and two identical alerts on one phone is exactly the noise this
    integration exists to avoid.
    """
    await setup_with_target(hass, entry, fetch, freezer, {CONF_TARGET_SERVICES: [NOTIFY_SERVICE]})

    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1


async def test_one_device_listed_twice_on_a_walk_is_notified_once(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    morning: list[ServiceCall],
) -> None:
    """A stored duplicate — from an older entry or a typed custom value — collapses."""
    await setup_with_target(
        hass, entry, fetch, freezer, {CONF_TARGET_SERVICES: [MORNING_PHONE, MORNING_PHONE]}
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert len(morning) == 1


async def test_a_walk_follows_its_own_away_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    morning: list[ServiceCall],
    alerts: list,
) -> None:
    """Anna's phone answers to Anna, not to whoever the entry watches.

    The phone here has no tracker of its own, which is the case the away entities
    exist for: something has to answer for a device that cannot answer for itself.
    """
    hass.states.async_set(MUTE_ENTITY, STATE_HOME)
    hass.states.async_set(WALK_MUTE_ENTITY, STATE_NOT_HOME)
    await setup_with_target(
        hass,
        entry,
        fetch,
        freezer,
        {CONF_TARGET_SERVICES: [MORNING_PHONE], CONF_TARGET_AWAY_ENTITY: WALK_MUTE_ENTITY},
        auto_mute=MUTE_ENTITY,
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert morning == []
    # The entry-wide device is not a walk phone, so none of this reaches it.
    assert len(notifications) == 1
    assert len(alerts) == 1
    assert alerts[0].data["muted"] is False


async def test_a_walks_own_away_entity_overrides_the_entry_wide_one(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    morning: list[ServiceCall],
) -> None:
    """Away from home himself, Piotr's walk still alerts while Piotr is in."""
    hass.states.async_set(MUTE_ENTITY, STATE_NOT_HOME)
    hass.states.async_set(WALK_MUTE_ENTITY, STATE_HOME)
    await setup_with_target(
        hass,
        entry,
        fetch,
        freezer,
        {CONF_TARGET_SERVICES: [MORNING_PHONE], CONF_TARGET_AWAY_ENTITY: WALK_MUTE_ENTITY},
        auto_mute=MUTE_ENTITY,
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert len(morning) == 1
    assert len(notifications) == 1


async def test_a_walk_can_address_several_devices(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    morning: list[ServiceCall],
    evening: list[ServiceCall],
) -> None:
    """Both phones get the same message when the walk names both."""
    await setup_with_target(
        hass, entry, fetch, freezer, {CONF_TARGET_SERVICES: [MORNING_PHONE, EVENING_PHONE]}
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert len(morning) == 1
    assert len(evening) == 1
    assert morning[0].data["message"] == evening[0].data["message"]


async def test_a_walk_with_no_devices_of_its_own_uses_the_default(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    morning: list[ServiceCall],
) -> None:
    """An empty device list means "only the always-notified device", never "nobody"."""
    await setup_with_target(hass, entry, fetch, freezer, {CONF_TARGET_MUTE: False})

    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1
    assert morning == []


async def test_a_muted_walk_still_reaches_the_always_notified_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    morning: list[ServiceCall],
    alerts: list,
) -> None:
    """Muting a walk silences the phones it added, and only those.

    The entry-wide device is not one of the walk's phones — it is the one the user
    asked to hear about every walk — so the switch cannot reach it.
    """
    await setup_with_target(
        hass,
        entry,
        fetch,
        freezer,
        {CONF_TARGET_SERVICES: [MORNING_PHONE], CONF_TARGET_MUTE: True},
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert morning == []
    assert len(notifications) == 1
    assert len(alerts) == 1
    assert alerts[0].data["muted"] is False


async def test_one_phone_being_out_does_not_silence_the_other(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    morning: list[ServiceCall],
    evening: list[ServiceCall],
) -> None:
    """The reason this rule exists: presence belongs to a phone, not to a walk.

    Two people share the dog and both asked to be told. One of them is out. The
    one who is in still has to be told, or the alert is worse than useless — it is
    silently absent exactly when somebody could have acted on it.
    """
    hass.states.async_set(MORNING_TRACKER, STATE_NOT_HOME)
    hass.states.async_set(EVENING_TRACKER, STATE_HOME)
    await setup_with_target(
        hass,
        entry,
        fetch,
        freezer,
        {CONF_TARGET_SERVICES: [MORNING_PHONE, EVENING_PHONE]},
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert morning == []
    assert len(evening) == 1
    assert len(notifications) == 1


async def test_a_phone_with_no_tracker_and_no_away_entity_is_notified(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    morning: list[ServiceCall],
) -> None:
    """Nothing can say where this phone is, so it is told rather than skipped."""
    await setup_with_target(hass, entry, fetch, freezer, {CONF_TARGET_SERVICES: [MORNING_PHONE]})

    await run_cycle(hass, freezer, ARM_AT)

    assert len(morning) == 1


@pytest.mark.parametrize("unreadable", [STATE_UNKNOWN, STATE_UNAVAILABLE])
async def test_a_tracker_that_cannot_answer_is_not_read_as_away(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    morning: list[ServiceCall],
    unreadable: str,
) -> None:
    """`unknown` and `unavailable` mean the tracker is silent, not that Anna is out."""
    hass.states.async_set(MORNING_TRACKER, unreadable)
    await setup_with_target(hass, entry, fetch, freezer, {CONF_TARGET_SERVICES: [MORNING_PHONE]})

    await run_cycle(hass, freezer, ARM_AT)

    assert len(morning) == 1


async def test_the_away_entity_answers_only_for_a_phone_that_cannot(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    morning: list[ServiceCall],
    evening: list[ServiceCall],
) -> None:
    """A phone with a tracker is never overruled by the entry-wide person.

    Both phones sit under an away entity that is out. The one that tracks itself
    and is at home is still notified; the one that cannot answer is not.
    """
    hass.states.async_set(MUTE_ENTITY, STATE_NOT_HOME)
    hass.states.async_set(EVENING_TRACKER, STATE_HOME)
    await setup_with_target(
        hass,
        entry,
        fetch,
        freezer,
        {CONF_TARGET_SERVICES: [MORNING_PHONE, EVENING_PHONE]},
        auto_mute=MUTE_ENTITY,
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert morning == []
    assert len(evening) == 1


async def test_muted_in_the_event_means_nobody_was_reached(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    morning: list[ServiceCall],
    alerts: list,
) -> None:
    """With no always-notified device, one absent phone can still leave silence."""
    hass.states.async_set(MORNING_TRACKER, STATE_NOT_HOME)
    await setup_with_target(
        hass,
        entry,
        fetch,
        freezer,
        {CONF_TARGET_SERVICES: [MORNING_PHONE]},
        default_device=None,
    )

    await run_cycle(hass, freezer, ARM_AT)

    assert morning == []
    assert len(alerts) == 1
    assert alerts[0].data["muted"] is True


async def test_another_walks_settings_do_not_leak_into_this_one(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
    evening: list[ServiceCall],
) -> None:
    """A target stored under a different walk's key must not silence this walk."""
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NOTIFY_SERVICE: NOTIFY_SERVICE,
            CONF_WALK_TARGETS: {
                target_key(KEY_ALL, "18:30"): {
                    CONF_TARGET_SERVICES: [EVENING_PHONE],
                    CONF_TARGET_MUTE: True,
                }
            },
        },
    )
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    await setup_entry(hass, entry)

    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1
    assert evening == []


# --- advice that has run out of time --------------------------------------


async def test_nothing_is_said_once_the_advice_has_run_out_of_time(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """The regression: the watch window outlives the walk, the advice must not.

    Told at 04:00 to set off at 04:30, the coordinator keeps cycling to the end of
    the walk. From 04:40 on, the search can no longer offer 04:30 and the answer
    flips to `no_dry_window` — a different direction, and so a material change by
    every other rule. Nothing about the weather changed, so nothing is sent.
    """
    moment = ARM_AT
    while moment < WALK_END:
        await run_cycle(hass, freezer, moment)
        moment += CYCLE

    assert len(notifications) == 1
    assert "04:30" in notifications[0].data["message"]


async def test_the_message_says_when_the_walk_would_get_home(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Moving a walk moves its end too, and that is what has to fit the evening."""
    await run_cycle(hass, freezer, ARM_AT)

    assert "05:00" in notifications[0].data["message"]


async def test_alerts_about_one_walk_share_a_notification_tag(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """A revised recommendation replaces the one it supersedes on the phone."""
    await run_cycle(hass, freezer, ARM_AT)
    fetch.build = lambda now: hourly_sources(now, HEAVY_AT_FIVE)
    await run_cycle(hass, freezer, ARM_AT + CYCLE)

    assert len(notifications) == 2
    tags = {call.data["data"]["tag"] for call in notifications}
    assert tags == {f"{TAG_PREFIX}{WALK_START:%Y%m%dT%H%M}"}


# --- how far the radar reaches --------------------------------------------


def _with_radar(now: datetime) -> Any:
    """The two hourly models, plus a radar frame covering the whole search window."""
    series, statuses = hourly_sources(now, RAIN_AT_FIVE)
    series.append(
        make_series(
            SOURCE_LIBREWXR,
            [0.0] * 12,
            start=WINDOW_START,
            step_s=600,
            issued_at=now,
        )
    )
    statuses.append(make_status(SOURCE_LIBREWXR, age_s=0, contributed=True))
    return series, statuses


async def test_a_model_only_answer_says_it_is_still_being_checked(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Only the hourly models are answering here, so the timing is an estimate."""
    await run_cycle(hass, freezer, ARM_AT)

    assert "still watching" in notifications[0].data["message"]


async def test_a_radar_backed_answer_is_stated_plainly(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """With a radar over the suggested window there is nothing left to hedge."""
    fetch.build = _with_radar
    await run_cycle(hass, freezer, ARM_AT)

    assert len(notifications) == 1
    assert "04:30" in notifications[0].data["message"]
    assert "still watching" not in notifications[0].data["message"]


# --- the "already went" button --------------------------------------------


async def test_the_push_carries_the_already_went_button(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """One button, naming the occurrence it belongs to rather than "the current walk"."""
    await run_cycle(hass, freezer, ARM_AT)

    actions = notifications[0].data["data"]["actions"]

    assert [action["action"] for action in actions] == [walked_action(WALK_START)]
    assert actions[0]["title"]


async def test_going_out_takes_the_notification_off_every_phone(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Only the phone that was tapped dismisses its own copy; the rest need telling."""
    await run_cycle(hass, freezer, ARM_AT)

    await coordinator.async_mark_walked()

    assert notifications[-1].data["message"] == CLEAR_NOTIFICATION
    assert notifications[-1].data["data"]["tag"] == f"{TAG_PREFIX}{WALK_START:%Y%m%dT%H%M}"


# --- the confirmation before setting off ----------------------------------


@pytest.fixture
async def confirming(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> WalkCoordinator:
    """The same entry, asked to say something 15 minutes before setting off."""
    hass.states.async_set(MUTE_ENTITY, STATE_HOME)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NOTIFY_SERVICE: NOTIFY_SERVICE,
            CONF_FIRE_EVENT: True,
            CONF_AUTO_MUTE_ENTITY: MUTE_ENTITY,
            CONF_CONFIRM_MARGIN_MIN: 15,
        },
    )
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, RAIN_AT_FIVE)
    return await setup_entry(hass, entry)


async def test_no_confirmation_is_sent_unless_it_is_asked_for(
    hass: HomeAssistant,
    coordinator: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Off by default: no news already means nothing has changed."""
    for moment in (ARM_AT, ARM_AT + CYCLE, ARM_AT + 2 * CYCLE):
        await run_cycle(hass, freezer, moment)

    assert len(notifications) == 1


async def test_the_confirmation_says_the_plan_still_stands(
    hass: HomeAssistant,
    confirming: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """Told at 04:00 to go at 04:30, the user hears at 04:15 that it is still on."""
    await run_cycle(hass, freezer, ARM_AT)
    assert len(notifications) == 1

    await run_cycle(hass, freezer, ARM_AT + CYCLE)
    assert len(notifications) == 1

    await run_cycle(hass, freezer, ARM_AT + 2 * CYCLE)

    assert len(notifications) == 2
    assert "04:30" in notifications[1].data["message"]


async def test_the_confirmation_is_sent_once(
    hass: HomeAssistant,
    confirming: WalkCoordinator,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """It is reassurance, not a countdown."""
    moment = ARM_AT
    while moment <= WALK_START:
        await run_cycle(hass, freezer, moment)
        moment += CYCLE

    assert len(notifications) == 2


async def test_the_rain_going_away_stands_the_alert_down(
    hass: HomeAssistant,
    confirming: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """The case silence handles badly: a walk that no longer needs moving.

    `later` relaxing to `none` is not an alert direction, so without the
    confirmation the user would go on waiting for a window that stopped being
    necessary.
    """
    await run_cycle(hass, freezer, ARM_AT)
    fetch.build = lambda now: hourly_sources(now, NO_RAIN)

    await run_cycle(hass, freezer, ARM_AT + 2 * CYCLE)

    assert len(notifications) == 2
    assert "05:00" in notifications[1].data["message"]


async def test_nothing_is_confirmed_that_was_never_announced(
    hass: HomeAssistant,
    confirming: WalkCoordinator,
    fetch: FakeFetch,
    freezer: FrozenDateTimeFactory,
    notifications: list[ServiceCall],
) -> None:
    """A walk that was fine all along is not worth a message about being fine."""
    fetch.build = lambda now: hourly_sources(now, NO_RAIN)

    moment = ARM_AT
    while moment <= WALK_START:
        await run_cycle(hass, freezer, moment)
        moment += CYCLE

    assert notifications == []
