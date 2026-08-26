"""The Walk the dog integration.

Predicts whether it will rain during the user's recurring dog walks and suggests
going out earlier or later so the walk stays dry. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.loader import async_get_loaded_integration

from .const import DOMAIN, SERVICE_WALKED
from .coordinator import WalkCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]


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
