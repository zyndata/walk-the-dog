"""Shared entity base: the one device both entities belong to.

There is exactly one config entry (`single_config_entry`), so there is exactly one
device — the walk location — and the sensor and the switch hang off it together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import WalkCoordinator


class WalkEntity(CoordinatorEntity["WalkCoordinator"]):
    """Common identity and device for every Walk the dog entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WalkCoordinator, entry: ConfigEntry, key: str) -> None:
        """Bind the entity to the coordinator and to the entry's single device."""
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Walk the dog",
            name=entry.title,
        )


__all__ = ["WalkEntity"]
