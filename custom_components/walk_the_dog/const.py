"""Constants for the Walk the dog integration.

Values fixed by the phase 0 research (docs/DATA_SOURCES.md) and the phase 1
architecture (docs/ARCHITECTURE.md). The config-entry storage shape is pinned in
phase 5; keys here name the options documented in docs/CONFIG.md.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "walk_the_dog"

#: The untranslated name, exactly as `manifest.json` carries it. A brand, so it is
#: the same in every language: it labels the device's manufacturer, and it is what
#: the config entry falls back to when the translated title cannot be read.
INTEGRATION_NAME: Final = "Walk the dog"

#: `strings.json` -> top-level `title`, the integration's name in the user's
#: language. Home Assistant exposes it as a translation category of its own.
TITLE_CATEGORY: Final = "title"

#: `strings.json` -> `device.<key>.name`. The device name is the prefix Home
#: Assistant puts in front of every entity's friendly name, so it is translated
#: rather than taken from the config entry title (see `entity.py`).
DEVICE_TRANSLATION_KEY: Final = "service"

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
CONF_WALK_TARGETS: Final = "walk_targets"
CONF_CONFIRM_MARGIN_MIN: Final = "confirm_margin_min"

# Keys inside one walk's entry of `walk_targets` (docs/CONFIG.md § Per-walk alerts)
CONF_TARGET_SERVICES: Final = "notify_services"
CONF_TARGET_MUTE: Final = "mute"
CONF_TARGET_AWAY_ENTITY: Final = "away_entity"

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

#: How long before setting off the "it still stands" message is sent. 0 disables it,
#: which is the default: silence already means "nothing changed", and this option
#: exists for users who would rather be told so than infer it (docs/CONFIG.md).
DEFAULT_CONFIRM_MARGIN_MIN: Final = 0
MAX_CONFIRM_MARGIN_MIN: Final = 60
CONFIRM_STEP_MIN: Final = 5

# Config-flow input bounds. Margins move in whole slots so a search offset always
# lands on the 10-minute grid the engine evaluates on.
MIN_MARGIN_MIN: Final = 0
MAX_MARGIN_MIN: Final = 180
MARGIN_STEP_MIN: Final = 10
MIN_WALK_DURATION_MIN: Final = 5
MAX_WALK_DURATION_MIN: Final = 240
WALK_DURATION_STEP_MIN: Final = 5
RADIUS_STEP_KM: Final = 0.5

# Notification target: only the companion app's per-device services qualify.
NOTIFY_DOMAIN: Final = "notify"
NOTIFY_SERVICE_PREFIX: Final = "mobile_app_"

#: `translation_key` of the recommendation sensor, and the tail of its unique id.
#: Named here rather than written twice because the notifier resolves it back to an
#: entity id: tapping a notification opens the entity that holds the same advice in
#: full, so the two modules have to mean the same entity by it.
ENTITY_KEY_RECOMMENDATION: Final = "recommendation"

DEFAULT_FIRE_EVENT: Final = False

# Time grid: all window evaluation happens on a 10-minute UTC grid
SLOT_MINUTES: Final = 10

#: Cycle length used in the final approach to setting off, where a source publishes
#: fast enough to make it worth it. A convective cell can form and arrive inside one
#: 10-minute slot, so the last minutes before the door are worth watching at the
#: fastest cadence any source actually offers — CHMI's 5 minutes. It divides
#: `SLOT_MINUTES`, so the anchored cycle grid stays a superset of the normal one.
SPRINT_MINUTES: Final = 5

#: How long before setting off the sprint cadence begins.
SPRINT_LEAD_MIN: Final = 20


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

#: Regional radar nowcast (CHMI CZRAD open data), added after phase 6. Unlike the
#: four above it covers only part of Poland, so it is silent outside its own box
#: (docs/DATA_SOURCES.md § CHMI).
SOURCE_CHMI: Final = "chmi"

#: The two radar-extrapolation sources. They are the only ones that can say *when*
#: rain starts to the minute, so a window they do not reach is a weaker answer than
#: one they do, and the difference is worth telling the user about.
NOWCAST_SOURCES: Final[frozenset[str]] = frozenset({SOURCE_LIBREWXR, SOURCE_CHMI})

#: Grace between a frame's nominal timestamp and asking the server for it. A frame
#: stamped 12:10 is not there at 12:10:00, and asking too early spends the request
#: on the frame we already hold — so this has to be **at least** the publication lag
#: or the aligned wakeup fetches nothing.
#:
#: Phase 8 replaced the estimate with a measurement (`scripts/measure_publish_lag.py`,
#: 2026-08-26; method and figures in STATE.md): CHMI publishes 18 s after the stamp
#: on almost every run and 68 s on its worst, while LibreWXR ranges from 78 s to
#: 158 s. The values below sit above the worst observed lag of each, which is what
#: makes the alignment able to fetch rather than merely to wake.
PUBLISH_SETTLE_S: Final[dict[str, int]] = {
    SOURCE_LIBREWXR: 180,
    SOURCE_CHMI: 90,
}

#: For a source with no measurement of its own — none exists today, and an hourly
#: model has nothing to align to at this timescale anyway.
DEFAULT_PUBLISH_SETTLE_S: Final = 60


def publish_settle_s(source_id: str) -> int:
    """How long after its stamp a frame from this source can be asked for."""
    return PUBLISH_SETTLE_S.get(source_id, DEFAULT_PUBLISH_SETTLE_S)


#: How far ahead a radar nowcast reaches, in minutes. Both radar sources publish
#: +10...+60 min and nothing beyond, so anything further out rests on hourly models
#: alone: right about *whether* it will rain, vague about *when*
#: (docs/DATA_SOURCES.md § Effective resolution). The config flow measures the
#: user's margins against this.
NOWCAST_HORIZON_MIN: Final = 60

# Event fired when a notification would fire (opt-in; payload documented in phase 6)
EVENT_ALERT: Final = "walk_the_dog_alert"

#: Home Assistant fires this when a companion-app notification action is tapped. Named
#: here rather than imported: `mobile_app` is not a dependency of this integration and
#: must not become one — a user without the app simply never fires it.
EVENT_MOBILE_APP_ACTION: Final = "mobile_app_notification_action"

#: Action identifier on the "I have already gone" button. The walk's UTC start is
#: appended to it, because the event carries the action string reliably on both
#: platforms and nothing else is guaranteed to survive the round trip.
ACTION_WALKED: Final = "WALK_THE_DOG_WALKED"

#: The magic message that makes the companion app dismiss a notification by tag.
CLEAR_NOTIFICATION: Final = "clear_notification"

#: Service that closes the current walk: no more alerts, no more polling for it.
SERVICE_WALKED: Final = "walked"
ATTR_WALK_START: Final = "walk_start"
