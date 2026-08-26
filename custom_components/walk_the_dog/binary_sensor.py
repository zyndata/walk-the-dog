"""Whether a walk window is open right now.

The recommendation sensor answers "what should I do about the next walk"; this one
answers "is the integration awake". They are different questions and belong to
different entities: an automation that wants to react to polling starting — turn a
dashboard card on, log a cycle, watch the request count — should not have to parse
a text state and its attributes to find out (docs/CONFIG.md § Entities).

`on` means exactly what `WalkData.active` means: the coordinator is inside a walk's
window and fetching. Between windows, and while the alerting switch is off, it is
`off` and nothing at all is being requested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity

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
    """Set up the walk-window binary sensor."""
    coordinator: WalkCoordinator = entry.runtime_data
    async_add_entities([WalkWindowBinarySensor(coordinator, entry)])


class WalkWindowBinarySensor(WalkEntity, BinarySensorEntity):
    """On while the coordinator is watching a walk."""

    def __init__(self, coordinator: WalkCoordinator, entry: ConfigEntry) -> None:
        """Name the entity after the window it reports."""
        super().__init__(coordinator, entry, "walk_window")

    @property
    def is_on(self) -> bool:
        """True while a walk window is open and cycles are running."""
        data = self.coordinator.data
        return data is not None and data.active

    @property
    def available(self) -> bool:
        """Always available: "no window is open" is an answer, not a failure."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The walk the window belongs to, so an automation needs nothing else."""
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "scheduled_start": None if data.walk_start is None else data.walk_start.isoformat(),
            "alerting": data.enabled,
        }


__all__ = ["WalkWindowBinarySensor"]
