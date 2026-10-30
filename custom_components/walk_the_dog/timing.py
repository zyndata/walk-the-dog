"""Which notice periods the radar can back, and fitting stored options to them. PURE.

The alert is sent `earlier_margin` before the walk, and since 1.3.0 it is only sent
once a radar reaches every minute of the walk. Both facts together give one rule:
the notice period plus the walk has to fit inside `RADAR_REACH_MIN`. A setting that
breaks it does not get a worse alert — it gets the real alert late, after the moment
it promised — so the config flow refuses it, and an entry stored before the rule
existed is brought inside it once, on upgrade (`fit_to_radar`).

No I/O, no homeassistant imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import (
    CONF_EARLIER_MARGIN_MIN,
    CONF_MIN_WALK_DURATION_MIN,
    CONF_WALK_DURATION_MIN,
    DEFAULT_EARLIER_MARGIN_MIN,
    MARGIN_STEP_MIN,
    MAX_WALK_DURATION_MIN,
    RADAR_REACH_MIN,
    SHORT_WALK_STEP_MIN,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The value of `min_walk_duration_min` that switches shortening off.
SHORTENING_OFF = 0


def max_earlier_margin(duration_min: int) -> int | None:
    """The widest notice period the radar can back for a walk this long.

    Rounded down to the margin step, because margins move in whole slots. `None`
    when the walk alone is already longer than the radar can see.
    """
    room = RADAR_REACH_MIN - duration_min
    if room < 0:
        return None
    return room - room % MARGIN_STEP_MIN


def fits_radar(earlier_min: int, duration_min: int) -> bool:
    """Whether the radar can see the whole walk at the moment it is announced."""
    widest = max_earlier_margin(duration_min)
    return widest is not None and earlier_min <= widest


def fit_to_radar(options: Mapping[str, Any]) -> dict[str, Any]:
    """The same options with the walk and its notice period inside the radar's reach.

    The walk keeps its length when it can and the notice period gives way, because
    the length is a fact about the dog and the notice is a preference. Only a walk
    the radar cannot see whole even with no notice at all is shortened, and then the
    shortest acceptable walk follows it down so the two never contradict each other.
    Anything already inside the rule comes back unchanged.
    """
    fitted = dict(options)
    if CONF_WALK_DURATION_MIN not in fitted:
        return fitted
    duration = min(int(fitted[CONF_WALK_DURATION_MIN]), MAX_WALK_DURATION_MIN)
    fitted[CONF_WALK_DURATION_MIN] = duration
    widest = max_earlier_margin(duration) or 0
    earlier = int(fitted.get(CONF_EARLIER_MARGIN_MIN, DEFAULT_EARLIER_MARGIN_MIN))
    fitted[CONF_EARLIER_MARGIN_MIN] = min(earlier, widest)
    shortest = int(fitted.get(CONF_MIN_WALK_DURATION_MIN, SHORTENING_OFF))
    if shortest != SHORTENING_OFF and shortest > duration:
        fitted[CONF_MIN_WALK_DURATION_MIN] = duration - duration % SHORT_WALK_STEP_MIN
    return fitted


__all__ = ["SHORTENING_OFF", "fit_to_radar", "fits_radar", "max_earlier_margin"]
