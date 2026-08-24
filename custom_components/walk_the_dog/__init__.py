"""The Walk the dog integration.

Predicts whether it will rain during the user's recurring dog walks and suggests
going out earlier or later so the walk stays dry. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

# sensor and switch platforms are added in phase 6
PLATFORMS: list[Platform] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Walk the dog from a config entry (coordinator wiring lands in phase 6)."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
