"""Walk-schedule model: the three modes, their storage shape, and validation.

PURE module: no I/O, no homeassistant imports, `now` is always a parameter.
Walk times are configured in the HA local timezone and resolved to UTC per
occurrence here (docs/ARCHITECTURE.md § Data flow, timezone rule) — the
next-walk computation itself lands in phase 6; phase 5 owns the model the config
flow writes and reads.

Storage shape (docs/CONFIG.md § Config entry data shape)::

    schedule_mode: "daily" | "weekday_weekend" | "per_day"
    schedule:      {<slot key>: ["07:00", "18:30", ...], ...}

The slot keys depend on the mode, so nothing is stored that the chosen mode does
not mean: `daily` keeps one list, `weekday_weekend` two, `per_day` seven.
`expand()` is the single place that turns any of them into per-weekday times.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from .const import (
    SCHEDULE_MODE_DAILY,
    SCHEDULE_MODE_PER_DAY,
    SCHEDULE_MODE_WEEKDAY_WEEKEND,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

#: Weekday slot keys in `datetime.weekday()` order — Monday is 0.
DAY_KEYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

KEY_ALL: Final = "all"
KEY_WEEKDAY: Final = "weekday"
KEY_WEEKEND: Final = "weekend"

#: Which slot keys a mode stores, in the order the config-flow form shows them.
SCHEDULE_KEYS: Final[dict[str, tuple[str, ...]]] = {
    SCHEDULE_MODE_DAILY: (KEY_ALL,),
    SCHEDULE_MODE_WEEKDAY_WEEKEND: (KEY_WEEKDAY, KEY_WEEKEND),
    SCHEDULE_MODE_PER_DAY: DAY_KEYS,
}

SCHEDULE_MODES: Final = tuple(SCHEDULE_KEYS)

#: First weekday counted as weekend by the weekday/weekend split (Saturday).
_WEEKEND_FROM: Final = 5

_MAX_HOUR: Final = 23
_MAX_MINUTE: Final = 59

#: ``HH:MM`` and ``HH:MM:SS`` are the two shapes the frontend's time input sends.
_WITH_SECONDS: Final = 3

#: Error keys — the config flow shows them through `strings.json`.
ERROR_INVALID_TIME: Final = "invalid_time"
ERROR_NO_WALK_TIMES: Final = "no_walk_times"


class ScheduleError(ValueError):
    """A schedule input the config flow must reject, carrying its message key."""

    def __init__(self, error_key: str) -> None:
        """Remember which `strings.json` error key describes the problem."""
        super().__init__(error_key)
        self.error_key = error_key


def normalize_time(raw: str) -> str:
    """Return `raw` as ``HH:MM``, or raise `ScheduleError`.

    The frontend's time input sends ``HH:MM`` or ``HH:MM:SS`` depending on the
    browser; a hand-typed value may drop the leading zero. Seconds are dropped —
    the engine's finest resolution is a 10-minute slot.
    """
    parts = str(raw).strip().split(":")
    if len(parts) not in (2, _WITH_SECONDS):
        raise ScheduleError(ERROR_INVALID_TIME)
    try:
        hour, minute = int(parts[0]), int(parts[1])
        seconds = int(parts[2]) if len(parts) == _WITH_SECONDS else 0
    except ValueError as err:
        raise ScheduleError(ERROR_INVALID_TIME) from err
    if not (0 <= hour <= _MAX_HOUR and 0 <= minute <= _MAX_MINUTE and 0 <= seconds <= _MAX_MINUTE):
        raise ScheduleError(ERROR_INVALID_TIME)
    return f"{hour:02d}:{minute:02d}"


def normalize_times(raw: Iterable[str]) -> tuple[str, ...]:
    """Normalize one slot's walk times: deduplicated and chronologically sorted."""
    return tuple(sorted({normalize_time(value) for value in raw}))


def normalize_schedule(mode: str, raw: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    """Validate a submitted schedule and return it in storage shape.

    Keys absent from `raw` become empty lists — a mode may legitimately have a
    slot with no walks (no weekend walks, say) — but a schedule with no walk time
    at all is rejected: there would be nothing to predict for.
    """
    if mode not in SCHEDULE_KEYS:
        raise ScheduleError(ERROR_NO_WALK_TIMES)
    schedule = {key: list(normalize_times(raw.get(key) or ())) for key in SCHEDULE_KEYS[mode]}
    if not any(schedule.values()):
        raise ScheduleError(ERROR_NO_WALK_TIMES)
    return schedule


def expand(mode: str, schedule: Mapping[str, Iterable[str]]) -> dict[int, tuple[str, ...]]:
    """Map every weekday (0 = Monday) to its walk times for the given mode.

    The one place that knows what each mode's slot keys mean; everything
    downstream works per weekday and never re-reads the mode.
    """
    if mode == SCHEDULE_MODE_DAILY:
        times = normalize_times(schedule.get(KEY_ALL) or ())
        return dict.fromkeys(range(len(DAY_KEYS)), times)
    if mode == SCHEDULE_MODE_WEEKDAY_WEEKEND:
        weekday = normalize_times(schedule.get(KEY_WEEKDAY) or ())
        weekend = normalize_times(schedule.get(KEY_WEEKEND) or ())
        return {day: (weekend if day >= _WEEKEND_FROM else weekday) for day in range(len(DAY_KEYS))}
    if mode == SCHEDULE_MODE_PER_DAY:
        return {day: normalize_times(schedule.get(key) or ()) for day, key in enumerate(DAY_KEYS)}
    raise ScheduleError(ERROR_NO_WALK_TIMES)
