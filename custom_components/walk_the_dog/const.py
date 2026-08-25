"""Constants for the Walk the dog integration.

Values fixed by the phase 0 research (docs/DATA_SOURCES.md) and the phase 1
architecture (docs/ARCHITECTURE.md). The config-entry storage shape is pinned in
phase 5; keys here name the options documented in docs/CONFIG.md.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "walk_the_dog"

# Config / option keys (semantics in docs/CONFIG.md)
CONF_LOCATION: Final = "location"
CONF_RADIUS_KM: Final = "radius_km"
CONF_INTENSITY_THRESHOLD: Final = "intensity_threshold"
CONF_EARLIER_MARGIN_MIN: Final = "earlier_margin_min"
CONF_LATER_MARGIN_MIN: Final = "later_margin_min"
CONF_WALK_DURATION_MIN: Final = "walk_duration_min"
CONF_SCHEDULE_MODE: Final = "schedule_mode"
CONF_SCHEDULE: Final = "schedule"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_FIRE_EVENT: Final = "fire_event"
CONF_AUTO_MUTE_ENTITY: Final = "auto_mute_entity"

# Schedule modes (docs/CONFIG.md, step 2)
SCHEDULE_MODE_DAILY: Final = "daily"
SCHEDULE_MODE_WEEKDAY_WEEKEND: Final = "weekday_weekend"
SCHEDULE_MODE_PER_DAY: Final = "per_day"

# Alert radius (phase 1 decision: disc must span ≥ 1 full ICON-EU cell)
DEFAULT_RADIUS_KM: Final = 5.0
MIN_RADIUS_KM: Final = 4.0
MAX_RADIUS_KM: Final = 15.0

# Search margins and timing (docs/CONFIG.md)
DEFAULT_EARLIER_MARGIN_MIN: Final = 60
DEFAULT_LATER_MARGIN_MIN: Final = 30
WALK_DURATION_WARN_MIN: Final = 30
LEAD_TIME_MIN: Final = 30

# Time grid: all window evaluation happens on a 10-minute UTC grid
SLOT_MINUTES: Final = 10

# Common intensity scale, mm/h lower bounds (docs/DATA_SOURCES.md)
INTENSITY_NONE: Final = "none"
INTENSITY_THRESHOLD_LIGHT: Final = "light"
INTENSITY_THRESHOLD_MODERATE: Final = "moderate"
INTENSITY_THRESHOLD_HEAVY: Final = "heavy"
INTENSITY_MM_H: Final[dict[str, float]] = {
    INTENSITY_THRESHOLD_LIGHT: 0.1,
    INTENSITY_THRESHOLD_MODERATE: 2.5,
    INTENSITY_THRESHOLD_HEAVY: 7.6,
}
DEFAULT_INTENSITY_THRESHOLD: Final = INTENSITY_THRESHOLD_LIGHT


def intensity_class(mm_per_h: float) -> str:
    """Classify a rain rate on the common scale (docs/DATA_SOURCES.md § Intensity mapping)."""
    if mm_per_h >= INTENSITY_MM_H[INTENSITY_THRESHOLD_HEAVY]:
        return INTENSITY_THRESHOLD_HEAVY
    if mm_per_h >= INTENSITY_MM_H[INTENSITY_THRESHOLD_MODERATE]:
        return INTENSITY_THRESHOLD_MODERATE
    if mm_per_h >= INTENSITY_MM_H[INTENSITY_THRESHOLD_LIGHT]:
        return INTENSITY_THRESHOLD_LIGHT
    return INTENSITY_NONE


# Source ids (roles fixed in phase 0; weights in docs/ARCHITECTURE.md § Consensus)
SOURCE_LIBREWXR: Final = "librewxr"
SOURCE_ICON_EU: Final = "icon_eu"
SOURCE_KNMI: Final = "knmi"
SOURCE_METNO: Final = "metno"

# Event fired when a notification would fire (opt-in; payload documented in phase 6)
EVENT_ALERT: Final = "walk_the_dog_alert"
