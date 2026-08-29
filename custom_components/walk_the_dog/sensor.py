"""The single recommendation sensor for the next upcoming walk.

Contract in docs/ARCHITECTURE.md § Outputs. Exactly one sensor exists, and it
always speaks about the walk the coordinator is currently watching: the state says
what to do, and the attributes say why — per-source verdicts included, because
"the radar says wet, both models say dry" is a different message from "everything
says wet" even when the vote lands in the same place.

`unknown` is never good news: it means no source reaches the walk, or there is no
walk to reach. The state is only ever `ok` when the scheduled window really is dry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import ATTR_ATTRIBUTION

from .const import ENTITY_KEY_RECOMMENDATION
from .engine import (
    DIRECTION_EARLIER,
    DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW,
    DIRECTION_NONE,
    DIRECTION_UNKNOWN,
)
from .entity import WalkEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import WalkCoordinator

#: `none` is the engine's word for "walk as planned"; `ok` is the user's.
STATE_OK: Final = "ok"

#: Every state the sensor can report, minus `unknown` — which HA renders itself
#: when the value is None, and which an enum sensor may not list as an option.
OPTIONS: Final = [STATE_OK, DIRECTION_EARLIER, DIRECTION_LATER, DIRECTION_NO_DRY_WINDOW]

_STATES: Final = {
    DIRECTION_NONE: STATE_OK,
    DIRECTION_EARLIER: DIRECTION_EARLIER,
    DIRECTION_LATER: DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW: DIRECTION_NO_DRY_WINDOW,
    DIRECTION_UNKNOWN: None,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the one recommendation sensor."""
    coordinator: WalkCoordinator = entry.runtime_data
    async_add_entities([WalkRecommendationSensor(coordinator, entry)])


class WalkRecommendationSensor(WalkEntity, SensorEntity):
    """What to do about the next walk, and everything behind that answer."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = OPTIONS

    def __init__(self, coordinator: WalkCoordinator, entry: ConfigEntry) -> None:
        """Name the entity after the recommendation it carries."""
        super().__init__(coordinator, entry, ENTITY_KEY_RECOMMENDATION)

    @property
    def native_value(self) -> str | None:
        """`ok` / `earlier` / `later` / `no_dry_window`, or None for unknown."""
        data = self.coordinator.data
        if data is None:
            return None
        return _STATES.get(data.direction)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The serialized recommendation, plus the state of the integration itself."""
        data = self.coordinator.data
        if data is None:
            return {}
        attributes = data.payload()
        attributes.update(
            {
                "alerting": data.enabled,
                "polling": data.active,
                "failover": data.failover,
                "last_fetch": None if data.fetched_at is None else data.fetched_at.isoformat(),
                # A walk window may stay open for hours while a "wait until" answer
                # is checked, so what that costs the providers is worth showing.
                "requests_last_hour": data.requests_last_hour,
                "requests_hourly_cap": data.requests_hourly_cap,
            }
        )
        if data.attributions:
            attributes[ATTR_ATTRIBUTION] = " | ".join(data.attributions)
        return attributes


__all__ = ["OPTIONS", "WalkRecommendationSensor"]
