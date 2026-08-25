"""Walk-schedule model: the three modes, their storage shape, and validation.

PURE module: no I/O, no homeassistant imports, `now` is always a parameter.
Walk times are configured in the HA local timezone and resolved to UTC per
occurrence here (docs/ARCHITECTURE.md § Data flow, timezone rule) — the
next-walk computation (`walks_from`) is layered on top in phase 6.

Storage shape (docs/CONFIG.md § Config entry data shape)::

    schedule_mode: "daily" | "weekday_weekend" | "per_day"
    schedule:      {<slot key>: ["07:00", "18:30", ...], ...}

The slot keys depend on the mode, so nothing is stored that the chosen mode does
not mean: `daily` keeps one list, `weekday_weekend` two, `per_day` seven.
`expand()` is the single place that turns any of them into per-weekday times.

A walk is identified by the pair `(slot key, configured time)` rather than by the
UTC instant it resolves to, because that pair is what the user typed and what
survives a DST change. `target_key()` renders it as the key under which the
per-walk notification settings are stored (docs/CONFIG.md § Per-walk alerts).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from .const import (
    SCHEDULE_MODE_DAILY,
    SCHEDULE_MODE_PER_DAY,
    SCHEDULE_MODE_WEEKDAY_WEEKEND,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date, tzinfo

#: One configured walk as the user typed it: the slot key it belongs to and its
#: local `HH:MM` start. Together they identify a walk across DST and across days.
type WalkSlot = tuple[str, str]

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

_ONE_DAY: Final = timedelta(days=1)

#: How far ahead `walks_from` looks before giving up. A schedule with a single
#: walk a week still resolves; anything sparser has nothing to nowcast for.
_HORIZON_DAYS: Final = 9

#: Enough upcoming walks for the coordinator to find the one whose window is open.
_DEFAULT_COUNT: Final = 8

#: Separates the two halves of a `target_key`. Neither half can contain it: slot
#: keys are fixed identifiers and a time is always `HH:MM`.
TARGET_SEPARATOR: Final = "|"

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


def _slot_times(key: str, schedule: Mapping[str, Iterable[str]]) -> tuple[WalkSlot, ...]:
    """One slot's times, each tagged with the slot key it came from."""
    return tuple((key, time) for time in normalize_times(schedule.get(key) or ()))


def expand(mode: str, schedule: Mapping[str, Iterable[str]]) -> dict[int, tuple[WalkSlot, ...]]:
    """Map every weekday (0 = Monday) to its walks, as `(slot key, time)` pairs.

    The one place that knows what each mode's slot keys mean; everything
    downstream works per weekday and never re-reads the mode. The slot key travels
    with the time because together they identify the walk whose notification
    settings are looked up later.
    """
    if mode == SCHEDULE_MODE_DAILY:
        return dict.fromkeys(range(len(DAY_KEYS)), _slot_times(KEY_ALL, schedule))
    if mode == SCHEDULE_MODE_WEEKDAY_WEEKEND:
        weekday = _slot_times(KEY_WEEKDAY, schedule)
        weekend = _slot_times(KEY_WEEKEND, schedule)
        return {day: (weekend if day >= _WEEKEND_FROM else weekday) for day in range(len(DAY_KEYS))}
    if mode == SCHEDULE_MODE_PER_DAY:
        return {day: _slot_times(key, schedule) for day, key in enumerate(DAY_KEYS)}
    raise ScheduleError(ERROR_NO_WALK_TIMES)


def _by_start(walk: Walk) -> datetime:
    """Sort key — `Walk` is deliberately not ordered, only its instant is."""
    return walk.start


def target_key(slot: str, time: str) -> str:
    """Storage key for one configured walk's notification settings."""
    return f"{slot}{TARGET_SEPARATOR}{normalize_time(time)}"


def configured_walks(mode: str, schedule: Mapping[str, Iterable[str]]) -> tuple[WalkSlot, ...]:
    """Every configured walk as `(slot key, time)`, in the order the form shows them.

    The config flow builds one notification step per entry, and prunes stored
    targets down to these keys — a walk time that was deleted must not leave its
    device list behind.
    """
    if mode not in SCHEDULE_KEYS:
        raise ScheduleError(ERROR_NO_WALK_TIMES)
    return tuple(
        (key, time)
        for key in SCHEDULE_KEYS[mode]
        for time in normalize_times(schedule.get(key) or ())
    )


@dataclass(frozen=True, slots=True)
class Walk:
    """One occurrence of a configured walk: when it happens, and which walk it is."""

    #: When this occurrence starts, in UTC.
    start: datetime
    #: Schedule slot key the walk was configured under.
    slot: str
    #: The configured local start time, `HH:MM` — half of the walk's identity.
    time: str

    @property
    def target_key(self) -> str:
        """Key under which this walk's notification settings are stored."""
        return target_key(self.slot, self.time)


def walk_times_on(
    per_day: Mapping[int, Iterable[WalkSlot]], day: date, tz: tzinfo
) -> tuple[Walk, ...]:
    """The walks of one local calendar day, resolved to UTC.

    Times are configured in the local timezone and resolved to UTC per occurrence,
    so a 07:00 walk stays at 07:00 local across a DST change instead of drifting by
    an hour (docs/ARCHITECTURE.md § Data flow, timezone rule). A time that a
    spring-forward skips resolves through the pre-transition offset, which lands the
    walk at the first moment the clock actually reaches.
    """
    walks = []
    for slot, text in per_day.get(day.weekday(), ()):
        time = normalize_time(text)
        hour, minute = (int(part) for part in time.split(":"))
        start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(UTC)
        walks.append(Walk(start=start, slot=slot, time=time))
    return tuple(sorted(walks, key=_by_start))


def walks_from(
    mode: str,
    schedule: Mapping[str, Iterable[str]],
    *,
    moment: datetime,
    tz: tzinfo,
    count: int = _DEFAULT_COUNT,
) -> tuple[Walk, ...]:
    """The next `count` walks starting at or after `moment`, chronologically.

    Returns an empty tuple for a schedule with no walk times at all — there is
    then nothing to predict for, and the coordinator arms no timer.
    """
    per_day = expand(mode, schedule)
    if not any(per_day.values()):
        return ()

    # Start a day early: a late-evening local walk can still be ahead of `moment`
    # in UTC when the local date has already rolled over.
    day = moment.astimezone(tz).date() - _ONE_DAY
    found: list[Walk] = []
    for _ in range(_HORIZON_DAYS):
        found.extend(walk for walk in walk_times_on(per_day, day, tz) if walk.start >= moment)
        if len(found) >= count:
            break
        day += _ONE_DAY
    return tuple(sorted(found, key=_by_start)[:count])
