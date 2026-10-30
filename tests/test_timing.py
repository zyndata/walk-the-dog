"""The radar-reach rule: which notice periods the radar can back, and fitting old entries.

Since 1.3.0 the alert waits until a radar sees the whole walk, so the notice period
plus the walk has to fit inside `RADAR_REACH_MIN`. These are plain arithmetic, and
they are the numbers both the config flow and the upgrade migration rest on.
"""

from __future__ import annotations

import pytest

from custom_components.walk_the_dog.const import (
    CONF_EARLIER_MARGIN_MIN,
    CONF_LATER_MARGIN_MIN,
    CONF_MIN_WALK_DURATION_MIN,
    CONF_WALK_DURATION_MIN,
    DEFAULT_EARLIER_MARGIN_MIN,
    MAX_WALK_DURATION_MIN,
    RADAR_REACH_MIN,
)
from custom_components.walk_the_dog.timing import fit_to_radar, fits_radar, max_earlier_margin


@pytest.mark.parametrize(
    ("duration", "widest"),
    [(5, 40), (10, 40), (15, 30), (20, 30), (25, 20), (30, 20), (40, 10), (45, 0), (50, 0)],
)
def test_the_widest_notice_rounds_down_to_the_margin_step(duration: int, widest: int) -> None:
    """Notice plus walk inside the reach, and margins only ever move in whole slots."""
    assert max_earlier_margin(duration) == widest
    assert fits_radar(widest, duration)
    assert not fits_radar(widest + 10, duration)


def test_a_walk_longer_than_the_reach_has_no_notice_at_all() -> None:
    """Not zero notice — no notice period works, so the walk itself must change."""
    assert max_earlier_margin(RADAR_REACH_MIN + 5) is None
    assert not fits_radar(0, RADAR_REACH_MIN + 5)


def test_the_default_notice_fits_a_typical_walk() -> None:
    """The default has to be a valid answer for the walks most people configure."""
    assert fits_radar(DEFAULT_EARLIER_MARGIN_MIN, 15)
    assert fits_radar(DEFAULT_EARLIER_MARGIN_MIN, 20)


def test_settings_that_fit_come_back_unchanged() -> None:
    """The migration must be a no-op for everyone who was already inside the rule."""
    options = {
        CONF_EARLIER_MARGIN_MIN: 30,
        CONF_WALK_DURATION_MIN: 15,
        CONF_LATER_MARGIN_MIN: 50,
        CONF_MIN_WALK_DURATION_MIN: 10,
    }

    assert fit_to_radar(options) == options


def test_the_notice_gives_way_and_the_walk_keeps_its_length() -> None:
    """The reported case: 60 minutes of notice for a 15-minute walk becomes 30.

    The later margin is left alone — going later is re-checked as the hour comes,
    so it never decides anything the radar has not seen.
    """
    fitted = fit_to_radar(
        {CONF_EARLIER_MARGIN_MIN: 60, CONF_WALK_DURATION_MIN: 15, CONF_LATER_MARGIN_MIN: 50}
    )

    assert fitted == {
        CONF_EARLIER_MARGIN_MIN: 30,
        CONF_WALK_DURATION_MIN: 15,
        CONF_LATER_MARGIN_MIN: 50,
    }


def test_a_walk_the_radar_cannot_see_whole_is_shortened_and_its_minimum_follows() -> None:
    """Only when no notice at all would do is the walk itself cut to the reach."""
    fitted = fit_to_radar(
        {CONF_EARLIER_MARGIN_MIN: 60, CONF_WALK_DURATION_MIN: 90, CONF_MIN_WALK_DURATION_MIN: 60}
    )

    assert fitted[CONF_WALK_DURATION_MIN] == MAX_WALK_DURATION_MIN
    assert fitted[CONF_EARLIER_MARGIN_MIN] == 0
    assert fitted[CONF_MIN_WALK_DURATION_MIN] == MAX_WALK_DURATION_MIN


def test_shortening_switched_off_stays_off() -> None:
    """0 is "never shorten", which no walk length can contradict."""
    fitted = fit_to_radar(
        {CONF_EARLIER_MARGIN_MIN: 0, CONF_WALK_DURATION_MIN: 90, CONF_MIN_WALK_DURATION_MIN: 0}
    )

    assert fitted[CONF_MIN_WALK_DURATION_MIN] == 0


def test_options_without_a_walk_are_left_alone() -> None:
    """Nothing to measure against: an entry that never got that far is not invented into one."""
    assert fit_to_radar({CONF_EARLIER_MARGIN_MIN: 120}) == {CONF_EARLIER_MARGIN_MIN: 120}
