"""The walk-schedule model: what the three modes store, and what they mean.

`schedule.py` is pure, so these tests are plain calls — no Home Assistant, no
clock. The config flow writes exactly what `normalize_schedule` returns, and the
coordinator reads exactly what `walks_from` returns; between them nothing else
needs to know that "weekday_weekend" means Saturday starts the weekend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.walk_the_dog.const import (
    SCHEDULE_MODE_DAILY,
    SCHEDULE_MODE_PER_DAY,
    SCHEDULE_MODE_WEEKDAY_WEEKEND,
)
from custom_components.walk_the_dog.schedule import (
    DAY_KEYS,
    ERROR_INVALID_TIME,
    ERROR_NO_WALK_TIMES,
    SCHEDULE_KEYS,
    SCHEDULE_MODES,
    ScheduleError,
    expand,
    normalize_schedule,
    normalize_time,
    normalize_times,
    walks_from,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("07:00", "07:00"),
        ("7:00", "07:00"),
        (" 07:05 ", "07:05"),
        ("07:30:00", "07:30"),
        ("00:00", "00:00"),
        ("23:59", "23:59"),
    ],
)
def test_normalize_time_accepts_what_the_frontend_sends(raw: str, expected: str) -> None:
    """Browsers send HH:MM or HH:MM:SS; seconds are below the engine's resolution."""
    assert normalize_time(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "07", "07:60", "24:00", "-1:00", "seven", "07:00:00:00", "07:xx"]
)
def test_normalize_time_rejects_anything_else(raw: str) -> None:
    """A time that cannot be read is an error the user has to see, not a guess."""
    with pytest.raises(ScheduleError) as err:
        normalize_time(raw)

    assert err.value.error_key == ERROR_INVALID_TIME


def test_normalize_times_sorts_and_deduplicates() -> None:
    """The same walk entered twice is one walk, and the list is chronological."""
    assert normalize_times(["18:30", "7:00", "07:00", "12:15"]) == ("07:00", "12:15", "18:30")


def test_normalize_times_of_nothing_is_empty() -> None:
    """An empty slot is allowed — the whole-schedule check catches an empty week."""
    assert normalize_times([]) == ()


def test_schedule_keys_cover_every_mode() -> None:
    """Every mode the config flow offers has to have a storage shape."""
    assert set(SCHEDULE_MODES) == {
        SCHEDULE_MODE_DAILY,
        SCHEDULE_MODE_WEEKDAY_WEEKEND,
        SCHEDULE_MODE_PER_DAY,
    }
    assert SCHEDULE_KEYS[SCHEDULE_MODE_PER_DAY] == DAY_KEYS


def test_normalize_schedule_daily() -> None:
    """Daily mode stores one list, normalized."""
    assert normalize_schedule(SCHEDULE_MODE_DAILY, {"all": ["18:30", "7:00"]}) == {
        "all": ["07:00", "18:30"]
    }


def test_normalize_schedule_fills_in_missing_slots() -> None:
    """A slot the form did not send is stored as empty, not left out."""
    result = normalize_schedule(SCHEDULE_MODE_WEEKDAY_WEEKEND, {"weekday": ["07:00"]})

    assert result == {"weekday": ["07:00"], "weekend": []}


def test_normalize_schedule_ignores_keys_the_mode_does_not_use() -> None:
    """Switching mode must not carry the previous mode's slots into storage."""
    result = normalize_schedule(SCHEDULE_MODE_DAILY, {"all": ["07:00"], "weekend": ["09:00"]})

    assert result == {"all": ["07:00"]}


def test_normalize_schedule_rejects_a_week_without_a_single_walk() -> None:
    """There would be nothing to predict for."""
    with pytest.raises(ScheduleError) as err:
        normalize_schedule(SCHEDULE_MODE_PER_DAY, {key: [] for key in DAY_KEYS})

    assert err.value.error_key == ERROR_NO_WALK_TIMES


def test_normalize_schedule_rejects_an_unknown_mode() -> None:
    """Storage shape is defined per mode; an unknown mode has none."""
    with pytest.raises(ScheduleError) as err:
        normalize_schedule("fortnightly", {"all": ["07:00"]})

    assert err.value.error_key == ERROR_NO_WALK_TIMES


def test_expand_daily_gives_every_day_the_same_times() -> None:
    """Daily mode is seven identical days."""
    result = expand(SCHEDULE_MODE_DAILY, {"all": ["07:00", "18:30"]})

    assert result == dict.fromkeys(range(7), ("07:00", "18:30"))


def test_expand_weekday_weekend_splits_at_saturday() -> None:
    """Monday-Friday are weekdays; Saturday (5) and Sunday (6) are the weekend."""
    result = expand(SCHEDULE_MODE_WEEKDAY_WEEKEND, {"weekday": ["07:00"], "weekend": ["09:30"]})

    assert [result[day] for day in range(7)] == [
        ("07:00",),
        ("07:00",),
        ("07:00",),
        ("07:00",),
        ("07:00",),
        ("09:30",),
        ("09:30",),
    ]


def test_expand_per_day_maps_each_key_to_its_weekday() -> None:
    """Key order is `datetime.weekday()` order — Monday is 0."""
    schedule = {key: [f"{7 + index:02d}:00"] for index, key in enumerate(DAY_KEYS)}

    result = expand(SCHEDULE_MODE_PER_DAY, schedule)

    assert result[0] == ("07:00",)
    assert result[6] == ("13:00",)


def test_expand_keeps_an_empty_day_empty() -> None:
    """No weekend walks means no weekend walks — not "inherit the weekday ones"."""
    result = expand(SCHEDULE_MODE_WEEKDAY_WEEKEND, {"weekday": ["07:00"], "weekend": []})

    assert result[5] == ()
    assert result[6] == ()


def test_expand_rejects_an_unknown_mode() -> None:
    """Same guard as normalization: no mode, no meaning."""
    with pytest.raises(ScheduleError):
        expand("fortnightly", {"all": ["07:00"]})


#: The integration's home country, and the one whose DST rules matter for it.
WARSAW = ZoneInfo("Europe/Warsaw")


def test_walks_from_returns_the_next_occurrences_in_order() -> None:
    """Two walks a day, chronologically, starting with the next one due."""
    result = walks_from(
        SCHEDULE_MODE_DAILY,
        {"all": ["07:00", "18:30"]},
        moment=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
        tz=UTC,
        count=3,
    )

    assert result == (
        datetime(2026, 8, 25, 18, 30, tzinfo=UTC),
        datetime(2026, 8, 26, 7, 0, tzinfo=UTC),
        datetime(2026, 8, 26, 18, 30, tzinfo=UTC),
    )


def test_walks_from_includes_a_walk_starting_exactly_now() -> None:
    """A walk due this very second is still ahead of us, not behind."""
    moment = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)

    result = walks_from(SCHEDULE_MODE_DAILY, {"all": ["07:00"]}, moment=moment, tz=UTC, count=1)

    assert result == (moment,)


def test_walks_from_resolves_local_times_to_utc() -> None:
    """07:00 in Warszawa is 05:00 UTC in August — the engine only ever sees UTC."""
    result = walks_from(
        SCHEDULE_MODE_DAILY,
        {"all": ["07:00"]},
        moment=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        tz=WARSAW,
        count=1,
    )

    assert result == (datetime(2026, 8, 25, 5, 0, tzinfo=UTC),)


def test_a_walk_time_survives_the_end_of_summer_time() -> None:
    """07:00 stays 07:00 local across the October change, so its UTC offset moves."""
    result = walks_from(
        SCHEDULE_MODE_DAILY,
        {"all": ["07:00"]},
        moment=datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
        tz=WARSAW,
        count=3,
    )

    # Poland leaves summer time in the small hours of Sunday 25 October 2026:
    # CEST (UTC+2) before, CET (UTC+1) from that morning on.
    assert result == (
        datetime(2026, 10, 24, 5, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 6, 0, tzinfo=UTC),
        datetime(2026, 10, 26, 6, 0, tzinfo=UTC),
    )
    assert [start.astimezone(WARSAW).hour for start in result] == [7, 7, 7]


def test_walks_from_crosses_a_local_midnight() -> None:
    """A late local walk is still ahead of a UTC moment that has rolled over."""
    result = walks_from(
        SCHEDULE_MODE_DAILY,
        {"all": ["23:30"]},
        moment=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        tz=WARSAW,
        count=1,
    )

    assert result == (datetime(2026, 8, 25, 21, 30, tzinfo=UTC),)


def test_walks_from_skips_a_day_with_no_walks() -> None:
    """Weekend walks only: the next one is Saturday, not tomorrow."""
    result = walks_from(
        SCHEDULE_MODE_WEEKDAY_WEEKEND,
        {"weekday": [], "weekend": ["09:00"]},
        # 25 August 2026 is a Tuesday.
        moment=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        tz=UTC,
        count=2,
    )

    assert result == (
        datetime(2026, 8, 29, 9, 0, tzinfo=UTC),
        datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
    )


def test_walks_from_finds_a_single_weekly_walk() -> None:
    """One walk a week still resolves — the lookahead covers a full week."""
    schedule: dict[str, list[str]] = {key: [] for key in DAY_KEYS}
    schedule["mon"] = ["07:00"]

    result = walks_from(
        SCHEDULE_MODE_PER_DAY,
        schedule,
        # A Tuesday: the next Monday is six days away.
        moment=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        tz=UTC,
        count=1,
    )

    assert result == (datetime(2026, 8, 31, 7, 0, tzinfo=UTC),)


def test_walks_from_returns_nothing_for_an_empty_week() -> None:
    """No walk times, nothing to predict for — and no timer for the coordinator."""
    result = walks_from(
        SCHEDULE_MODE_DAILY,
        {"all": []},
        moment=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        tz=UTC,
    )

    assert result == ()
