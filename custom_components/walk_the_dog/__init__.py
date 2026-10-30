"""The Walk the dog integration.

Predicts whether it will rain during the user's recurring dog walks and suggests
going out earlier or later so the walk stays dry. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import async_get_loaded_integration

from .const import (
    CONF_EARLIER_MARGIN_MIN,
    CONF_WALK_DURATION_MIN,
    DOMAIN,
    RADAR_REACH_MIN,
    SERVICE_WALKED,
)
from .coordinator import WalkCoordinator
from .timing import fit_to_radar

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]

#: The repair issue that tells the user their timing was changed for them on upgrade.
ISSUE_TIMING_FITTED: Final = "timing_fitted"

#: The two options `fit_to_radar` may change and the repair issue reports.
_TIMING_KEYS: Final = (CONF_EARLIER_MARGIN_MIN, CONF_WALK_DURATION_MIN)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an entry stored before 1.3.0 inside the radar's reach, once.

    Since 1.3.0 the alert waits until a radar sees the whole walk, so a notice
    period plus walk longer than the radar's reach would not get a less reliable
    alert — it would get one later than it promised. The config flow now refuses
    such settings; an entry saved before it did is fitted here (`timing.py`), and a
    repair issue says what changed, because a notice period that quietly shrank is
    exactly the kind of surprise a user reports as a bug.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < 2:  # noqa: PLR2004 - the minor version this migrates to
        fitted = fit_to_radar(entry.options)
        before = {key: entry.options.get(key) for key in _TIMING_KEYS}
        after = {key: fitted.get(key) for key in _TIMING_KEYS}
        hass.config_entries.async_update_entry(entry, options=fitted, minor_version=2)
        if before != after:
            _LOGGER.warning("Walk timing fitted to the radar's reach: %s -> %s", before, after)
            ir.async_create_issue(
                hass,
                DOMAIN,
                ISSUE_TIMING_FITTED,
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_TIMING_FITTED,
                translation_placeholders={
                    "reach": str(RADAR_REACH_MIN),
                    "earlier_before": str(before[CONF_EARLIER_MARGIN_MIN]),
                    "earlier_after": str(after[CONF_EARLIER_MARGIN_MIN]),
                    "duration_before": str(before[CONF_WALK_DURATION_MIN]),
                    "duration_after": str(after[CONF_WALK_DURATION_MIN]),
                },
            )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Walk the dog from a config entry.

    The first refresh deliberately makes no request: the coordinator starts with
    alerting off and the switch platform, set up right after, restores the real
    position. A Home Assistant restarted with alerting disabled therefore never
    reaches a weather provider at all.

    No update listener is registered — the options flow is an
    `OptionsFlowWithReload`, which reloads the entry itself.
    """
    version = str(async_get_loaded_integration(hass, DOMAIN).version)
    coordinator = WalkCoordinator(hass, entry, version)
    await coordinator.async_setup_cache()
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_listen_actions()
    _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry. The coordinator's timer is cancelled by its shutdown."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register `walk_the_dog.walked`, once per Home Assistant.

    There is exactly one config entry (`single_config_entry` in the manifest), so
    the service needs no target: it always means "the walk being watched right now".
    Registration is idempotent because a reload sets the entry up again while the
    service from the previous setup is still there.
    """
    if hass.services.has_service(DOMAIN, SERVICE_WALKED):
        return

    async def async_walked(call: ServiceCall) -> None:
        """Close the current walk: no more advice about it, and no more polling."""
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_loaded_entry"
            )
        coordinator: WalkCoordinator = entries[0].runtime_data
        await coordinator.async_mark_walked()

    hass.services.async_register(DOMAIN, SERVICE_WALKED, async_walked, schema=vol.Schema({}))


__all__ = ["PLATFORMS", "async_setup_entry", "async_unload_entry"]
