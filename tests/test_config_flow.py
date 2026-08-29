"""The setup wizard and the options flow.

Every test drives the real flow through `hass.config_entries`, so the voluptuous
schemas and the selectors validate exactly as they do in the frontend. The
coordinates used here are a public landmark, never anyone's home.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.walk_the_dog.config_flow import (
    CONF_CONFIRM,
    ERROR_INVALID_NOTIFY_SERVICE,
    ERROR_MIN_WALK_TOO_LONG,
)
from custom_components.walk_the_dog.const import (
    CONF_AUTO_MUTE_ENTITY,
    CONF_CONFIRM_MARGIN_MIN,
    CONF_EARLIER_MARGIN_MIN,
    CONF_FIRE_EVENT,
    CONF_INTENSITY_THRESHOLD,
    CONF_LATER_MARGIN_MIN,
    CONF_LOCATION,
    CONF_MIN_WALK_DURATION_MIN,
    CONF_NOTIFY_SERVICE,
    CONF_RADIUS_KM,
    CONF_SCHEDULE,
    CONF_SCHEDULE_MODE,
    CONF_TARGET_AWAY_ENTITY,
    CONF_TARGET_MUTE,
    CONF_TARGET_SERVICES,
    CONF_WALK_DURATION_MIN,
    CONF_WALK_TARGETS,
    DEFAULT_CONFIRM_MARGIN_MIN,
    DEFAULT_EARLIER_MARGIN_MIN,
    DEFAULT_INTENSITY_THRESHOLD,
    DEFAULT_LATER_MARGIN_MIN,
    DEFAULT_MIN_WALK_DURATION_MIN,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    INTENSITY_THRESHOLD_MODERATE,
    MAX_RADIUS_KM,
    MIN_RADIUS_KM,
    NOWCAST_HORIZON_MIN,
    SCHEDULE_MODE_DAILY,
    SCHEDULE_MODE_PER_DAY,
    SCHEDULE_MODE_WEEKDAY_WEEKEND,
    SHORT_WALK_STEP_MIN,
    SLOT_MINUTES,
    WALK_DURATION_WARN_MIN,
)
from custom_components.walk_the_dog.schedule import DAY_KEYS, ERROR_NO_WALK_TIMES, target_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

#: Warszawa city centre — a public landmark, as everywhere else in this suite.
LOCATION = {CONF_LATITUDE: 52.2297, CONF_LONGITUDE: 21.0122}

PARAMS: dict[str, Any] = {
    CONF_RADIUS_KM: DEFAULT_RADIUS_KM,
    CONF_INTENSITY_THRESHOLD: DEFAULT_INTENSITY_THRESHOLD,
    CONF_EARLIER_MARGIN_MIN: DEFAULT_EARLIER_MARGIN_MIN,
    CONF_LATER_MARGIN_MIN: DEFAULT_LATER_MARGIN_MIN,
    CONF_WALK_DURATION_MIN: 30,
    CONF_MIN_WALK_DURATION_MIN: DEFAULT_MIN_WALK_DURATION_MIN,
    CONF_CONFIRM_MARGIN_MIN: DEFAULT_CONFIRM_MARGIN_MIN,
    CONF_FIRE_EVENT: False,
}

STORED_PARAMS: dict[str, Any] = {**PARAMS, CONF_WALK_DURATION_MIN: 30}


def marker_for(schema: vol.Schema, key: str) -> vol.Marker:
    """The schema key for `key`, so its default and suggestion can be inspected."""
    return next(marker for marker in schema.schema if marker == key)


def suggested(schema: vol.Schema, key: str) -> Any:
    """What the form prefills `key` with (a stored value, if there is one)."""
    return (marker_for(schema, key).description or {}).get("suggested_value")


def selector_config(schema: vol.Schema, key: str) -> dict[str, Any]:
    """The selector configuration behind a field."""
    return schema.schema[marker_for(schema, key)].config


async def start(hass: HomeAssistant) -> dict[str, Any]:
    """Open the wizard at step 1."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def advance(hass: HomeAssistant, result: dict[str, Any], user_input: Any) -> dict[str, Any]:
    """Submit one step."""
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def drain_targets(
    configure: Any, result: dict[str, Any], targets: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Submit the per-walk notification steps, one form per configured walk.

    Without `targets` every walk is left at its defaults, which is what most of
    these tests want: the step exists, but it stores nothing.
    """
    pending = list(targets or [])
    while result["type"] is FlowResultType.FORM and result["step_id"] == "walk_target":
        result = await configure(result["flow_id"], pending.pop(0) if pending else {})
    assert not pending, "more notification answers than the schedule has walks"
    return result


async def to_params(
    hass: HomeAssistant,
    *,
    mode: str = SCHEDULE_MODE_DAILY,
    times: dict[str, list[str]] | None = None,
    targets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Drive the wizard as far as the parameter form."""
    result = await start(hass)
    result = await advance(hass, result, {CONF_LOCATION: LOCATION})
    result = await advance(hass, result, {CONF_SCHEDULE_MODE: mode})
    result = await advance(hass, result, times if times is not None else {"all": ["07:00"]})
    return await drain_targets(hass.config_entries.flow.async_configure, result, targets)


async def run_wizard(
    hass: HomeAssistant,
    *,
    mode: str = SCHEDULE_MODE_DAILY,
    times: dict[str, list[str]] | None = None,
    targets: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drive the whole wizard with valid input and return the final result."""
    result = await to_params(hass, mode=mode, times=times, targets=targets)
    return await advance(hass, result, params if params is not None else PARAMS)


async def test_wizard_happy_path(hass: HomeAssistant) -> None:
    """Location lands in entry data, schedule and parameters in options."""
    result = await run_wizard(hass, times={"all": ["18:30", "7:00"]})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Walk the dog"
    assert result["data"] == {CONF_LOCATION: LOCATION}
    assert result["options"] == {
        CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY,
        CONF_SCHEDULE: {"all": ["07:00", "18:30"]},
        **STORED_PARAMS,
    }


async def test_wizard_prefills_the_home_location(hass: HomeAssistant) -> None:
    """Step 1 starts at the Home Assistant home, so most users just press next."""
    result = await start(hass)

    assert result["data_schema"]({})[CONF_LOCATION] == {
        CONF_LATITUDE: hass.config.latitude,
        CONF_LONGITUDE: hass.config.longitude,
    }


async def test_wizard_offers_the_documented_radius_bounds(hass: HomeAssistant) -> None:
    """The phase 1 decision — 5 km default between 4 and 15 km — is what the form shows."""
    result = await to_params(hass)

    config = selector_config(result["data_schema"], CONF_RADIUS_KM)
    assert (config["min"], config["max"]) == (MIN_RADIUS_KM, MAX_RADIUS_KM)
    assert marker_for(result["data_schema"], CONF_RADIUS_KM).default() == DEFAULT_RADIUS_KM


async def test_wizard_walk_duration_has_no_default(hass: HomeAssistant) -> None:
    """A required input with no default: the user must state how long a walk takes."""
    result = await to_params(hass)

    marker = marker_for(result["data_schema"], CONF_WALK_DURATION_MIN)
    assert marker.default is vol.UNDEFINED
    with pytest.raises(InvalidData):
        await advance(
            hass,
            result,
            {key: value for key, value in PARAMS.items() if key != CONF_WALK_DURATION_MIN},
        )


async def test_wizard_weekday_weekend_mode(hass: HomeAssistant) -> None:
    """Two lists, and an empty weekend is a legitimate answer."""
    result = await run_wizard(
        hass,
        mode=SCHEDULE_MODE_WEEKDAY_WEEKEND,
        times={"weekday": ["07:00", "18:00"], "weekend": []},
    )

    assert result["options"][CONF_SCHEDULE_MODE] == SCHEDULE_MODE_WEEKDAY_WEEKEND
    assert result["options"][CONF_SCHEDULE] == {"weekday": ["07:00", "18:00"], "weekend": []}


async def test_wizard_per_day_mode(hass: HomeAssistant) -> None:
    """Seven lists, one per weekday, in Monday-first order."""
    times = {key: [f"{7 + index:02d}:00"] for index, key in enumerate(DAY_KEYS)}

    result = await run_wizard(hass, mode=SCHEDULE_MODE_PER_DAY, times=times)

    assert result["options"][CONF_SCHEDULE] == {
        key: [f"{7 + index:02d}:00"] for index, key in enumerate(DAY_KEYS)
    }


async def test_schedule_times_form_adapts_to_the_mode(hass: HomeAssistant) -> None:
    """The chosen mode decides which fields step 2b shows."""
    result = await start(hass)
    result = await advance(hass, result, {CONF_LOCATION: LOCATION})
    result = await advance(hass, result, {CONF_SCHEDULE_MODE: SCHEDULE_MODE_PER_DAY})

    assert result["step_id"] == "schedule_times"
    assert tuple(str(marker) for marker in result["data_schema"].schema) == DAY_KEYS


async def test_schedule_rejects_an_empty_week(hass: HomeAssistant) -> None:
    """Nothing to predict for — the form comes back with an error, not an entry."""
    result = await start(hass)
    result = await advance(hass, result, {CONF_LOCATION: LOCATION})
    result = await advance(hass, result, {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY})

    result = await advance(hass, result, {"all": []})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "schedule_times"
    assert result["errors"] == {"base": ERROR_NO_WALK_TIMES}


async def test_schedule_recovers_after_an_empty_week(hass: HomeAssistant) -> None:
    """The error is not a dead end."""
    result = await start(hass)
    result = await advance(hass, result, {CONF_LOCATION: LOCATION})
    result = await advance(hass, result, {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY})
    result = await advance(hass, result, {"all": []})

    result = await advance(hass, result, {"all": ["07:00"]})
    result = await drain_targets(hass.config_entries.flow.async_configure, result)
    result = await advance(hass, result, PARAMS)

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_radius_below_the_minimum_is_refused(hass: HomeAssistant) -> None:
    """The selector enforces the phase 1 minimum; sampling below one cell is meaningless."""
    result = await to_params(hass)

    with pytest.raises(InvalidData):
        await advance(hass, result, {**PARAMS, CONF_RADIUS_KM: 1.0})


async def test_long_walk_warning_is_shown_and_can_be_confirmed(hass: HomeAssistant) -> None:
    """Over the warn threshold the wizard says so, then takes yes for an answer."""
    duration = WALK_DURATION_WARN_MIN + 15
    result = await to_params(hass)

    result = await advance(hass, result, {**PARAMS, CONF_WALK_DURATION_MIN: duration})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "long_walk"
    assert result["description_placeholders"] == {
        "duration": str(duration),
        "limit": str(WALK_DURATION_WARN_MIN),
        "horizon": str(NOWCAST_HORIZON_MIN),
    }

    result = await advance(hass, result, {CONF_CONFIRM: True})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_WALK_DURATION_MIN] == duration


async def test_long_walk_warning_declined_returns_to_the_parameters(hass: HomeAssistant) -> None:
    """Declining is how the user goes back and lowers the duration."""
    result = await to_params(hass)
    result = await advance(hass, result, {**PARAMS, CONF_WALK_DURATION_MIN: 90})

    result = await advance(hass, result, {CONF_CONFIRM: False})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "params"
    assert suggested(result["data_schema"], CONF_WALK_DURATION_MIN) == 90

    result = await advance(hass, result, PARAMS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_WALK_DURATION_MIN] == 30


async def test_exactly_the_warn_threshold_does_not_warn(hass: HomeAssistant) -> None:
    """The warning is for walks *longer* than the threshold."""
    result = await run_wizard(
        hass, params={**PARAMS, CONF_WALK_DURATION_MIN: WALK_DURATION_WARN_MIN}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_notification_devices_are_offered_from_the_service_registry(
    hass: HomeAssistant,
) -> None:
    """Only the companion app's per-device services qualify as a target."""
    hass.services.async_register("notify", "mobile_app_phone", lambda call: None)
    hass.services.async_register("notify", "persistent_notification", lambda call: None)

    result = await to_params(hass)

    assert selector_config(result["data_schema"], CONF_NOTIFY_SERVICE)["options"] == [
        "mobile_app_phone"
    ]


async def test_notify_service_is_stored_without_its_domain(hass: HomeAssistant) -> None:
    """`notify.mobile_app_phone` and `mobile_app_phone` mean the same target."""
    result = await run_wizard(
        hass, params={**PARAMS, CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"}
    )

    assert result["options"][CONF_NOTIFY_SERVICE] == "mobile_app_phone"


async def test_a_notify_service_that_is_not_a_companion_app_is_refused(
    hass: HomeAssistant,
) -> None:
    """A custom value still has to be a service this integration can actually use."""
    result = await to_params(hass)

    result = await advance(hass, result, {**PARAMS, CONF_NOTIFY_SERVICE: "telegram"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "params"
    assert result["errors"] == {CONF_NOTIFY_SERVICE: ERROR_INVALID_NOTIFY_SERVICE}
    # Everything else the user typed survives the correction.
    assert suggested(result["data_schema"], CONF_EARLIER_MARGIN_MIN) == DEFAULT_EARLIER_MARGIN_MIN


async def test_optional_fields_left_empty_are_not_stored(hass: HomeAssistant) -> None:
    """An absent notification target or mute entity is absent, not a stored `None`."""
    result = await run_wizard(hass)

    assert CONF_NOTIFY_SERVICE not in result["options"]
    assert CONF_AUTO_MUTE_ENTITY not in result["options"]


async def test_auto_mute_entity_is_stored(hass: HomeAssistant) -> None:
    """A person or device tracker that silences alerts while away."""
    result = await run_wizard(hass, params={**PARAMS, CONF_AUTO_MUTE_ENTITY: "person.owner"})

    assert result["options"][CONF_AUTO_MUTE_ENTITY] == "person.owner"


async def test_only_one_entry_is_allowed(hass: HomeAssistant) -> None:
    """One home, one schedule, one sensor — the manifest says so and HA enforces it."""
    MockConfigEntry(domain=DOMAIN, data={CONF_LOCATION: LOCATION}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_one_notification_step_per_configured_walk(hass: HomeAssistant) -> None:
    """Every walk gets its own form, in schedule order, and says which one it is."""
    result = await start(hass)
    result = await advance(hass, result, {CONF_LOCATION: LOCATION})
    result = await advance(hass, result, {CONF_SCHEDULE_MODE: SCHEDULE_MODE_WEEKDAY_WEEKEND})
    result = await advance(hass, result, {"weekday": ["07:00", "18:30"], "weekend": ["09:00"]})

    seen = []
    while result["step_id"] == "walk_target":
        seen.append(result["description_placeholders"])
        result = await advance(hass, result, {})

    assert result["step_id"] == "params"
    assert [(place["days"], place["time"]) for place in seen] == [
        ("Monday to Friday", "07:00"),
        ("Monday to Friday", "18:30"),
        ("Saturday and Sunday", "09:00"),
    ]
    assert [(place["index"], place["total"]) for place in seen] == [
        ("1", "3"),
        ("2", "3"),
        ("3", "3"),
    ]


async def test_each_walk_stores_its_own_devices_and_mute(hass: HomeAssistant) -> None:
    """The morning walk wakes one phone, the evening walk another, and one is muted."""
    result = await run_wizard(
        hass,
        times={"all": ["07:00", "18:30"]},
        targets=[
            {CONF_TARGET_SERVICES: ["mobile_app_morning"]},
            {CONF_TARGET_SERVICES: ["mobile_app_evening"], CONF_TARGET_MUTE: True},
        ],
    )

    assert result["options"][CONF_WALK_TARGETS] == {
        target_key("all", "07:00"): {CONF_TARGET_SERVICES: ["mobile_app_morning"]},
        target_key("all", "18:30"): {
            CONF_TARGET_SERVICES: ["mobile_app_evening"],
            CONF_TARGET_MUTE: True,
        },
    }


async def test_a_walk_stores_its_own_away_entity(hass: HomeAssistant) -> None:
    """The walk Anna does can watch Anna, whoever the entry-wide person is."""
    result = await run_wizard(
        hass,
        times={"all": ["07:00"]},
        targets=[{CONF_TARGET_AWAY_ENTITY: "person.anna"}],
        params={**PARAMS, CONF_AUTO_MUTE_ENTITY: "person.owner"},
    )

    assert result["options"][CONF_AUTO_MUTE_ENTITY] == "person.owner"
    assert result["options"][CONF_WALK_TARGETS] == {
        target_key("all", "07:00"): {CONF_TARGET_AWAY_ENTITY: "person.anna"}
    }


async def test_a_device_named_twice_on_one_walk_is_stored_once(hass: HomeAssistant) -> None:
    """`notify.mobile_app_x` typed next to a picked `mobile_app_x` is one phone."""
    result = await run_wizard(
        hass,
        times={"all": ["07:00"]},
        targets=[{CONF_TARGET_SERVICES: ["mobile_app_anna", "notify.mobile_app_anna"]}],
    )

    assert result["options"][CONF_WALK_TARGETS] == {
        target_key("all", "07:00"): {CONF_TARGET_SERVICES: ["mobile_app_anna"]}
    }


async def test_a_walk_left_at_the_defaults_stores_nothing(hass: HomeAssistant) -> None:
    """`walk_targets` only ever holds walks the user actually said something about."""
    result = await run_wizard(hass, times={"all": ["07:00"]})

    assert CONF_WALK_TARGETS not in result["options"]


async def test_the_notification_step_offers_the_registered_devices(
    hass: HomeAssistant,
) -> None:
    """Same rule as the default device: only companion-app services qualify."""
    hass.services.async_register("notify", "mobile_app_phone", lambda call: None)
    hass.services.async_register("notify", "persistent_notification", lambda call: None)

    result = await start(hass)
    result = await advance(hass, result, {CONF_LOCATION: LOCATION})
    result = await advance(hass, result, {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY})
    result = await advance(hass, result, {"all": ["07:00"]})

    config = selector_config(result["data_schema"], CONF_TARGET_SERVICES)
    assert config["options"] == ["mobile_app_phone"]
    assert config["multiple"] is True


async def test_a_device_that_is_not_a_companion_app_is_refused(hass: HomeAssistant) -> None:
    """A typed custom value has to be a service this integration can call."""
    result = await start(hass)
    result = await advance(hass, result, {CONF_LOCATION: LOCATION})
    result = await advance(hass, result, {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY})
    result = await advance(hass, result, {"all": ["07:00"]})

    result = await advance(hass, result, {CONF_TARGET_SERVICES: ["telegram"]})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "walk_target"
    assert result["errors"] == {CONF_TARGET_SERVICES: ERROR_INVALID_NOTIFY_SERVICE}


async def test_deleting_a_walk_time_drops_its_devices(hass: HomeAssistant) -> None:
    """A time that no longer exists must not leave a device list to be inherited."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Walk the dog",
        data={CONF_LOCATION: LOCATION},
        options={
            CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY,
            CONF_SCHEDULE: {"all": ["07:00", "18:30"]},
            CONF_WALK_TARGETS: {
                target_key("all", "07:00"): {CONF_TARGET_SERVICES: ["mobile_app_morning"]},
                target_key("all", "18:30"): {CONF_TARGET_SERVICES: ["mobile_app_evening"]},
            },
            **STORED_PARAMS,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"all": ["07:00"]}
    )
    # The surviving walk still shows what it had, and it is the only step left.
    assert marker_for(result["data_schema"], CONF_TARGET_SERVICES).default() == [
        "mobile_app_morning"
    ]
    result = await drain_targets(hass.config_entries.options.async_configure, result)
    await hass.config_entries.options.async_configure(result["flow_id"], PARAMS)
    await hass.async_block_till_done()

    assert entry.options[CONF_WALK_TARGETS] == {
        target_key("all", "07:00"): {CONF_TARGET_SERVICES: ["mobile_app_morning"]}
    }


async def options_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured entry to edit."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Walk the dog",
        data={CONF_LOCATION: LOCATION},
        options={
            CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY,
            CONF_SCHEDULE: {"all": ["07:00"]},
            **STORED_PARAMS,
            CONF_NOTIFY_SERVICE: "mobile_app_phone",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_flow_prefills_what_is_stored(hass: HomeAssistant) -> None:
    """Editing starts from the current configuration, not from the defaults."""
    entry = await options_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "schedule_mode"
    assert marker_for(result["data_schema"], CONF_SCHEDULE_MODE).default() == SCHEDULE_MODE_DAILY

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY}
    )
    assert marker_for(result["data_schema"], "all").default() == ["07:00"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"all": ["07:00"]}
    )
    result = await drain_targets(hass.config_entries.options.async_configure, result)
    assert suggested(result["data_schema"], CONF_NOTIFY_SERVICE) == "mobile_app_phone"
    assert suggested(result["data_schema"], CONF_WALK_DURATION_MIN) == 30


async def test_options_flow_round_trip(hass: HomeAssistant) -> None:
    """Every option from steps 2 and 3 is editable later, schedule mode included."""
    entry = await options_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCHEDULE_MODE: SCHEDULE_MODE_WEEKDAY_WEEKEND}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"weekday": ["06:45"], "weekend": ["10:00"]}
    )
    result = await drain_targets(hass.config_entries.options.async_configure, result)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_RADIUS_KM: 8.0,
            CONF_INTENSITY_THRESHOLD: INTENSITY_THRESHOLD_MODERATE,
            CONF_EARLIER_MARGIN_MIN: 90,
            CONF_LATER_MARGIN_MIN: 20,
            CONF_WALK_DURATION_MIN: 25,
            CONF_MIN_WALK_DURATION_MIN: 20,
            CONF_CONFIRM_MARGIN_MIN: 15,
            CONF_FIRE_EVENT: True,
        },
    )
    # 90 minutes of notice is more than the radar forecasts, so the flow asks first.
    assert result["step_id"] == "beyond_radar"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CONFIRM: True}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SCHEDULE_MODE: SCHEDULE_MODE_WEEKDAY_WEEKEND,
        CONF_SCHEDULE: {"weekday": ["06:45"], "weekend": ["10:00"]},
        CONF_RADIUS_KM: 8.0,
        CONF_INTENSITY_THRESHOLD: INTENSITY_THRESHOLD_MODERATE,
        CONF_EARLIER_MARGIN_MIN: 90,
        CONF_LATER_MARGIN_MIN: 20,
        CONF_WALK_DURATION_MIN: 25,
        CONF_MIN_WALK_DURATION_MIN: 20,
        CONF_CONFIRM_MARGIN_MIN: 15,
        CONF_FIRE_EVENT: True,
    }
    # The location is entry data, so the options flow never touches it.
    assert entry.data == {CONF_LOCATION: LOCATION}


async def test_options_flow_validates_like_the_wizard(hass: HomeAssistant) -> None:
    """The shared steps mean the two flows cannot drift apart."""
    entry = await options_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"all": []})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": ERROR_NO_WALK_TIMES}


async def test_options_flow_warns_about_a_long_walk(hass: HomeAssistant) -> None:
    """The warning belongs to the parameter step, so the options flow shows it too."""
    entry = await options_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"all": ["07:00"]}
    )
    result = await drain_targets(hass.config_entries.options.async_configure, result)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**PARAMS, CONF_WALK_DURATION_MIN: 60}
    )

    assert result["step_id"] == "long_walk"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CONFIRM: True}
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_WALK_DURATION_MIN] == 60


async def test_options_flow_can_clear_an_optional_field(hass: HomeAssistant) -> None:
    """Removing the notification device really removes it from the stored options."""
    entry = await options_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"all": ["07:00"]}
    )
    result = await drain_targets(hass.config_entries.options.async_configure, result)
    result = await hass.config_entries.options.async_configure(result["flow_id"], PARAMS)
    await hass.async_block_till_done()

    assert CONF_NOTIFY_SERVICE not in entry.options


async def test_a_notice_period_past_the_radar_horizon_is_confirmed_first(
    hass: HomeAssistant,
) -> None:
    """Asked for two hours of warning, the wizard explains what that costs."""
    earlier = NOWCAST_HORIZON_MIN + 60
    result = await to_params(hass)

    result = await advance(hass, result, {**PARAMS, CONF_EARLIER_MARGIN_MIN: earlier})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "beyond_radar"
    assert result["description_placeholders"] == {
        "earlier": str(earlier),
        "horizon": str(NOWCAST_HORIZON_MIN),
        "over": str(earlier - NOWCAST_HORIZON_MIN),
    }

    result = await advance(hass, result, {CONF_CONFIRM: True})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_EARLIER_MARGIN_MIN] == earlier


async def test_a_notice_period_warning_declined_returns_to_the_parameters(
    hass: HomeAssistant,
) -> None:
    """Declining is how the user goes back and lowers the margin."""
    result = await to_params(hass)
    result = await advance(hass, result, {**PARAMS, CONF_EARLIER_MARGIN_MIN: 120})

    result = await advance(hass, result, {CONF_CONFIRM: False})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "params"
    assert suggested(result["data_schema"], CONF_EARLIER_MARGIN_MIN) == 120


async def test_exactly_the_radar_horizon_does_not_warn(hass: HomeAssistant) -> None:
    """The warning is for notice periods *longer* than the radar reaches."""
    result = await run_wizard(hass, params={**PARAMS, CONF_EARLIER_MARGIN_MIN: NOWCAST_HORIZON_MIN})

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_both_timing_warnings_are_shown_in_turn(hass: HomeAssistant) -> None:
    """A long walk with a long notice period earns one confirmation each."""
    result = await to_params(hass)

    result = await advance(
        hass,
        result,
        {**PARAMS, CONF_WALK_DURATION_MIN: 90, CONF_EARLIER_MARGIN_MIN: 120},
    )
    assert result["step_id"] == "long_walk"

    result = await advance(hass, result, {CONF_CONFIRM: True})
    assert result["step_id"] == "beyond_radar"

    result = await advance(hass, result, {CONF_CONFIRM: True})
    assert result["type"] is FlowResultType.CREATE_ENTRY


# --- shortening the walk ---------------------------------------------------


async def test_the_shortest_walk_moves_in_whole_radar_frames(hass: HomeAssistant) -> None:
    """One grid slot is one radar frame, and nothing finer is worth offering."""
    result = await to_params(hass)

    config = selector_config(result["data_schema"], CONF_MIN_WALK_DURATION_MIN)
    assert config["step"] == SHORT_WALK_STEP_MIN == SLOT_MINUTES
    # 0 is on the scale because it is how the feature is switched off.
    assert config["min"] == 0
    assert marker_for(result["data_schema"], CONF_MIN_WALK_DURATION_MIN).default() == 10


async def test_a_minimum_longer_than_the_walk_is_refused(hass: HomeAssistant) -> None:
    """A window both shorter than the walk and longer than it is not a preference."""
    result = await to_params(hass)

    result = await advance(
        hass,
        result,
        {**PARAMS, CONF_WALK_DURATION_MIN: 20, CONF_MIN_WALK_DURATION_MIN: 30},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "params"
    assert result["errors"] == {CONF_MIN_WALK_DURATION_MIN: ERROR_MIN_WALK_TOO_LONG}


async def test_a_minimum_equal_to_the_walk_is_accepted(hass: HomeAssistant) -> None:
    """The boundary is legitimate: it simply means nothing shorter is ever offered."""
    result = await run_wizard(
        hass, params={**PARAMS, CONF_WALK_DURATION_MIN: 20, CONF_MIN_WALK_DURATION_MIN: 20}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_MIN_WALK_DURATION_MIN] == 20


async def test_shortening_switched_off_is_never_too_long(hass: HomeAssistant) -> None:
    """0 means "never shorten a walk", which no walk duration can contradict."""
    result = await run_wizard(
        hass, params={**PARAMS, CONF_WALK_DURATION_MIN: 20, CONF_MIN_WALK_DURATION_MIN: 0}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_MIN_WALK_DURATION_MIN] == 0
