"""Enable/disable switch for alerting — and, with it, for polling.

While it is off there are no timers, no requests and no cycles at all
(docs/ARCHITECTURE.md § Coordinator scheduling). The state survives a restart via
`RestoreEntity`, and the coordinator deliberately starts in the off position until
this entity restores it: that way a Home Assistant started with alerting disabled
makes no request even once.

Default on — someone who installs a rain alarm wants the rain alarm.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import WalkEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import WalkCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the alerting switch."""
    coordinator: WalkCoordinator = entry.runtime_data
    async_add_entities([WalkAlertingSwitch(coordinator, entry)])


class WalkAlertingSwitch(WalkEntity, SwitchEntity, RestoreEntity):
    """Turns the whole prediction loop on and off."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: WalkCoordinator, entry: ConfigEntry) -> None:
        """Name the entity after what it controls."""
        super().__init__(coordinator, entry, "alerting")

    async def async_added_to_hass(self) -> None:
        """Restore the previous position and hand it to the coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        enabled = True if last_state is None else last_state.state == STATE_ON
        await self.coordinator.async_set_enabled(enabled)

    @property
    def is_on(self) -> bool:
        """Whether alerting is running."""
        return self.coordinator.enabled

    @property
    def available(self) -> bool:
        """Always available: it is the control that decides whether anything runs."""
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Resume alerting — and run a cycle at once if a walk window is open."""
        await self.coordinator.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop alerting: the armed timer is cancelled, nothing is polled."""
        await self.coordinator.async_set_enabled(False)
        self.async_write_ha_state()


__all__ = ["WalkAlertingSwitch"]
