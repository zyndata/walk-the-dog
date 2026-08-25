"""Config flow wizard and options flow for the Walk the dog integration.

Implements docs/CONFIG.md: location -> walk schedule -> who to notify per walk ->
parameters. The schedule, notification and parameter steps are shared verbatim
with the options flow (`_WalkFlowSteps`), so what the wizard accepts and what the
options flow accepts can never drift.

The notification step repeats, once per configured walk, because a form's schema
is fixed before it is rendered and the number of walks is not known until the
times are submitted. One form per walk also keeps every label translatable — a
single form with a field per walk could only fall back to raw storage keys.

There is exactly one entry per Home Assistant (`single_config_entry` in the
manifest): one home, one schedule, one recommendation sensor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_AUTO_MUTE_ENTITY,
    CONF_EARLIER_MARGIN_MIN,
    CONF_FIRE_EVENT,
    CONF_INTENSITY_THRESHOLD,
    CONF_LATER_MARGIN_MIN,
    CONF_LOCATION,
    CONF_NOTIFY_SERVICE,
    CONF_RADIUS_KM,
    CONF_SCHEDULE,
    CONF_SCHEDULE_MODE,
    CONF_TARGET_MUTE,
    CONF_TARGET_SERVICES,
    CONF_WALK_DURATION_MIN,
    CONF_WALK_TARGETS,
    DEFAULT_EARLIER_MARGIN_MIN,
    DEFAULT_FIRE_EVENT,
    DEFAULT_INTENSITY_THRESHOLD,
    DEFAULT_LATER_MARGIN_MIN,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    INTENSITY_MM_H,
    MARGIN_STEP_MIN,
    MAX_MARGIN_MIN,
    MAX_RADIUS_KM,
    MAX_WALK_DURATION_MIN,
    MIN_MARGIN_MIN,
    MIN_RADIUS_KM,
    MIN_WALK_DURATION_MIN,
    NOTIFY_DOMAIN,
    NOTIFY_SERVICE_PREFIX,
    RADIUS_STEP_KM,
    SCHEDULE_MODE_DAILY,
    WALK_DURATION_STEP_MIN,
    WALK_DURATION_WARN_MIN,
)
from .schedule import (
    SCHEDULE_KEYS,
    SCHEDULE_MODES,
    ScheduleError,
    configured_walks,
    normalize_schedule,
    target_key,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry

    from .schedule import WalkSlot

CONF_CONFIRM: Final = "confirm"

ERROR_INVALID_NOTIFY_SERVICE: Final = "invalid_notify_service"

#: The notification step names the days a walk belongs to in its description, and
#: that text belongs to no form field. `common` is the only top-level strings.json
#: key Home Assistant allows for such strings (see `notifier.py`), so the labels
#: live there and are read back at runtime like the notification texts are.
SLOT_LABEL_CATEGORY: Final = "common"
SLOT_LABEL_PREFIX: Final = "walk_slot_"

#: Sentinel for "this field has no default" — `None` is a legitimate default.
_NO_DEFAULT: Final = object()


def _marker(
    key: str,
    current: Mapping[str, Any],
    default: Any = _NO_DEFAULT,
    *,
    required: bool = True,
) -> vol.Marker:
    """Build a schema key that prefills with the stored value, else with the default.

    `suggested_value` and `default` are never combined: a suggestion always wins
    in the frontend, so passing both would hide a field's documented default on a
    fresh install.
    """
    marker = vol.Required if required else vol.Optional
    if key in current:
        return marker(key, description={"suggested_value": current[key]})
    if default is _NO_DEFAULT:
        return marker(key)
    return marker(key, default=default)


def _minutes_selector(minimum: int, maximum: int, step: int) -> NumberSelector:
    """A whole-minutes box input."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement="min",
            mode=NumberSelectorMode.BOX,
        )
    )


def _validate_notify_service(raw: str | None) -> str | None:
    """Accept a bare `mobile_app_*` service name, tolerating a `notify.` prefix.

    The field allows a custom value so a companion-app device whose service is not
    registered yet can still be configured ahead of time.
    """
    if raw is None:
        return None
    value = str(raw).strip().removeprefix(f"{NOTIFY_DOMAIN}.")
    if not value:
        return None
    if not value.startswith(NOTIFY_SERVICE_PREFIX) or not value.replace("_", "").isalnum():
        raise vol.Invalid(ERROR_INVALID_NOTIFY_SERVICE)
    return value


def _collect_params(user_input: dict[str, Any], *, keep_notify: bool = True) -> dict[str, Any]:
    """Normalize step 3's raw form values into the stored option types.

    Number selectors hand back floats; minutes are stored as whole minutes.
    Optional fields cleared in the form are dropped rather than stored as `None`,
    so an options round-trip really removes them.
    """
    params: dict[str, Any] = {
        CONF_RADIUS_KM: float(user_input[CONF_RADIUS_KM]),
        CONF_INTENSITY_THRESHOLD: user_input[CONF_INTENSITY_THRESHOLD],
        CONF_EARLIER_MARGIN_MIN: int(user_input[CONF_EARLIER_MARGIN_MIN]),
        CONF_LATER_MARGIN_MIN: int(user_input[CONF_LATER_MARGIN_MIN]),
        CONF_WALK_DURATION_MIN: int(user_input[CONF_WALK_DURATION_MIN]),
        CONF_FIRE_EVENT: bool(user_input[CONF_FIRE_EVENT]),
    }
    if auto_mute := user_input.get(CONF_AUTO_MUTE_ENTITY):
        params[CONF_AUTO_MUTE_ENTITY] = auto_mute
    if not keep_notify:
        return params
    if notify_service := _validate_notify_service(user_input.get(CONF_NOTIFY_SERVICE)):
        params[CONF_NOTIFY_SERVICE] = notify_service
    return params


def _collect_target(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one walk's notification form into what is stored for it.

    A walk left at the defaults stores nothing at all, so `walk_targets` only ever
    holds walks the user actually said something about.
    """
    services = [
        service
        for service in (
            _validate_notify_service(raw) for raw in user_input.get(CONF_TARGET_SERVICES) or ()
        )
        if service
    ]
    target: dict[str, Any] = {}
    if services:
        target[CONF_TARGET_SERVICES] = services
    if user_input.get(CONF_TARGET_MUTE):
        target[CONF_TARGET_MUTE] = True
    return target


class _WalkFlowSteps:
    """The schedule and parameter steps, shared by the wizard and the options flow.

    Mixed into a `FlowHandler` subclass, which supplies `hass`, `async_show_form`
    and `async_create_entry`.
    """

    # Supplied by the FlowHandler this mixin is combined with.
    hass: Any
    async_show_form: Any

    _current: Mapping[str, Any]
    _schedule_mode: str
    _schedule: dict[str, list[str]]
    _targets: dict[str, dict[str, Any]]
    _pending: list[WalkSlot]
    _params: dict[str, Any]

    def _init_steps(self, current: Mapping[str, Any]) -> None:
        """Seed the shared steps with the values they should prefill from."""
        self._current = current
        self._schedule_mode = current.get(CONF_SCHEDULE_MODE, SCHEDULE_MODE_DAILY)
        self._schedule = dict(current.get(CONF_SCHEDULE, {}))
        self._targets = {
            key: dict(value) for key, value in (current.get(CONF_WALK_TARGETS) or {}).items()
        }
        self._pending = []
        self._params = {}

    async def _async_finish(self) -> ConfigFlowResult:
        """Store the collected schedule and parameters. Implemented per flow."""
        raise NotImplementedError

    def _options(self) -> dict[str, Any]:
        """The full options payload both flows write."""
        options: dict[str, Any] = {
            CONF_SCHEDULE_MODE: self._schedule_mode,
            CONF_SCHEDULE: self._schedule,
        }
        if self._targets:
            options[CONF_WALK_TARGETS] = self._targets
        options.update(self._params)
        return options

    def _notify_services(self) -> list[str]:
        """The companion-app notify services registered right now, alphabetically."""
        return sorted(
            name
            for name in self.hass.services.async_services_for_domain(NOTIFY_DOMAIN)
            if name.startswith(NOTIFY_SERVICE_PREFIX)
        )

    async def async_step_schedule_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a — pick how the walk schedule is expressed."""
        if user_input is not None:
            self._schedule_mode = user_input[CONF_SCHEDULE_MODE]
            return await self.async_step_schedule_times()

        schema = vol.Schema(
            {
                vol.Required(CONF_SCHEDULE_MODE, default=self._schedule_mode): SelectSelector(
                    SelectSelectorConfig(
                        options=list(SCHEDULE_MODES),
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_SCHEDULE_MODE,
                    )
                )
            }
        )
        return self.async_show_form(step_id="schedule_mode", data_schema=schema)

    async def async_step_schedule_times(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b — the walk times, one editable list per slot of the chosen mode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._schedule = normalize_schedule(self._schedule_mode, user_input)
            except ScheduleError as err:
                errors["base"] = err.error_key
            else:
                return await self._async_start_walk_targets()

        times = TextSelector(TextSelectorConfig(type=TextSelectorType.TIME, multiple=True))
        source: Mapping[str, Any] = user_input if user_input is not None else self._schedule
        schema = vol.Schema(
            {
                vol.Optional(key, default=list(source.get(key) or ())): times
                for key in SCHEDULE_KEYS[self._schedule_mode]
            }
        )
        return self.async_show_form(step_id="schedule_times", data_schema=schema, errors=errors)

    async def _async_start_walk_targets(self) -> ConfigFlowResult:
        """Queue one notification step per configured walk and show the first.

        Targets of walk times that no longer exist are dropped here: a deleted
        07:00 walk must not leave its device list behind to be resurrected by a
        later walk that happens to land on the same key.
        """
        self._pending = list(configured_walks(self._schedule_mode, self._schedule))
        keys = {target_key(slot, time) for slot, time in self._pending}
        self._targets = {key: value for key, value in self._targets.items() if key in keys}
        return await self.async_step_walk_target()

    async def async_step_walk_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2c — who is told about this one walk, repeated for every walk."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                target = _collect_target(user_input)
            except vol.Invalid:
                errors[CONF_TARGET_SERVICES] = ERROR_INVALID_NOTIFY_SERVICE
            else:
                self._store_target(self._pending.pop(0), target)
                user_input = None
                if not self._pending:
                    return await self.async_step_params()
        return await self._async_show_walk_target(errors, user_input)

    def _store_target(self, walk: WalkSlot, target: dict[str, Any]) -> None:
        """Keep one walk's notification settings, or forget them if it has none."""
        key = target_key(*walk)
        if target:
            self._targets[key] = target
        else:
            self._targets.pop(key, None)

    async def _async_show_walk_target(
        self, errors: dict[str, str], user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Render the notification form for the walk at the head of the queue."""
        slot, time = self._pending[0]
        source: Mapping[str, Any] = (
            user_input if user_input is not None else self._targets.get(target_key(slot, time), {})
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TARGET_SERVICES,
                    default=list(source.get(CONF_TARGET_SERVICES) or ()),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=self._notify_services(),
                        multiple=True,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_TARGET_MUTE, default=bool(source.get(CONF_TARGET_MUTE, False))
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="walk_target",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "days": await self._async_slot_label(slot),
                "time": time,
                "index": str(self._walk_index()),
                "total": str(self._walk_total()),
            },
        )

    def _walk_total(self) -> int:
        """How many walks the notification steps will ask about in total."""
        return len(configured_walks(self._schedule_mode, self._schedule))

    def _walk_index(self) -> int:
        """Which of them is on screen, counting from one."""
        return self._walk_total() - len(self._pending) + 1

    async def _async_slot_label(self, slot: str) -> str:
        """The days a walk belongs to, in the user's language."""
        texts = await async_get_translations(
            self.hass, self.hass.config.language, SLOT_LABEL_CATEGORY, {DOMAIN}
        )
        key = f"component.{DOMAIN}.{SLOT_LABEL_CATEGORY}.{SLOT_LABEL_PREFIX}{slot}"
        return texts.get(key, slot)

    def _params_schema(self) -> vol.Schema:
        """Step 3's schema, in the order docs/CONFIG.md lists the options."""
        current: Mapping[str, Any] = self._params or self._current
        notify_services = self._notify_services()
        margin = _minutes_selector(MIN_MARGIN_MIN, MAX_MARGIN_MIN, MARGIN_STEP_MIN)
        return vol.Schema(
            {
                _marker(CONF_RADIUS_KM, current, DEFAULT_RADIUS_KM): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_RADIUS_KM,
                        max=MAX_RADIUS_KM,
                        step=RADIUS_STEP_KM,
                        unit_of_measurement="km",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                _marker(
                    CONF_INTENSITY_THRESHOLD, current, DEFAULT_INTENSITY_THRESHOLD
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(INTENSITY_MM_H),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_INTENSITY_THRESHOLD,
                    )
                ),
                _marker(CONF_EARLIER_MARGIN_MIN, current, DEFAULT_EARLIER_MARGIN_MIN): margin,
                _marker(CONF_LATER_MARGIN_MIN, current, DEFAULT_LATER_MARGIN_MIN): margin,
                # Deliberately no default — the user must state how long a walk takes.
                _marker(CONF_WALK_DURATION_MIN, current): _minutes_selector(
                    MIN_WALK_DURATION_MIN, MAX_WALK_DURATION_MIN, WALK_DURATION_STEP_MIN
                ),
                _marker(CONF_NOTIFY_SERVICE, current, required=False): SelectSelector(
                    SelectSelectorConfig(
                        options=notify_services,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                _marker(CONF_FIRE_EVENT, current, DEFAULT_FIRE_EVENT): BooleanSelector(),
                _marker(CONF_AUTO_MUTE_ENTITY, current, required=False): EntitySelector(
                    EntitySelectorConfig(domain=["person", "device_tracker"])
                ),
            }
        )

    async def async_step_params(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 3 — the parameters from docs/CONFIG.md § Options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._params = _collect_params(user_input)
            except vol.Invalid:
                # Keep everything else the user typed; only the bad field is flagged.
                self._params = _collect_params(user_input, keep_notify=False)
                errors[CONF_NOTIFY_SERVICE] = ERROR_INVALID_NOTIFY_SERVICE
            else:
                if self._params[CONF_WALK_DURATION_MIN] > WALK_DURATION_WARN_MIN:
                    return await self.async_step_long_walk()
                return await self._async_finish()

        return self.async_show_form(
            step_id="params", data_schema=self._params_schema(), errors=errors
        )

    async def async_step_long_walk(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Warn that a walk longer than the nowcast comfort zone is less reliable.

        Not an error — the user may confirm. Declining returns to step 3 with the
        entered values still in the form, so the duration can simply be lowered.
        """
        if user_input is not None:
            if user_input[CONF_CONFIRM]:
                return await self._async_finish()
            return await self.async_step_params()

        return self.async_show_form(
            step_id="long_walk",
            data_schema=vol.Schema({vol.Required(CONF_CONFIRM, default=False): BooleanSelector()}),
            description_placeholders={
                "duration": str(self._params.get(CONF_WALK_DURATION_MIN, "")),
                "limit": str(WALK_DURATION_WARN_MIN),
            },
        )


class WalkTheDogConfigFlow(_WalkFlowSteps, ConfigFlow, domain=DOMAIN):
    """Handle the Walk the dog setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with no collected input."""
        self._location: dict[str, float] = {}
        self._init_steps({})

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> WalkTheDogOptionsFlow:
        """Return the options flow — steps 2 and 3, editable later."""
        return WalkTheDogOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1 — where the walks happen, prefilled with the Home Assistant home."""
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            self._location = {
                CONF_LATITUDE: float(location[CONF_LATITUDE]),
                CONF_LONGITUDE: float(location[CONF_LONGITUDE]),
            }
            return await self.async_step_schedule_mode()

        home = {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_LOCATION, default=home): LocationSelector(
                    LocationSelectorConfig(radius=False)
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _async_finish(self) -> ConfigFlowResult:
        """Create the config entry: location in data, everything else in options."""
        return self.async_create_entry(
            title="Walk the dog",
            data={CONF_LOCATION: self._location},
            options=self._options(),
        )


class WalkTheDogOptionsFlow(_WalkFlowSteps, OptionsFlowWithReload):
    """Edit the schedule and the parameters of an existing entry.

    `OptionsFlowWithReload` reloads the entry itself, so the integration must not
    register a config-entry update listener (phase 6).
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Re-run the wizard from the schedule step, prefilled with what is stored."""
        self._init_steps(self.config_entry.options)
        return await self.async_step_schedule_mode()

    async def _async_finish(self) -> ConfigFlowResult:
        """Write the edited options back."""
        return self.async_create_entry(data=self._options())
