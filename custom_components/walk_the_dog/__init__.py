"""The Walk the dog integration.

Predicts whether it will rain during the user's recurring dog walks and suggests
going out earlier or later so the walk stays dry. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.loader import async_get_loaded_integration

from .const import DOMAIN
from .coordinator import WalkCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry. The coordinator's timer is cancelled by its shutdown."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
