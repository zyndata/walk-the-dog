"""The shared 10-minute grid and how each source's own steps land on it.

Everything the engine decides rests on this projection being a step function with
honest gaps: an hourly model must cover all six slots of its hour, and a slot no
source reaches must be *absent* rather than zero — "we do not know" and "no rain"
must never be the same value.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.walk_the_dog.const import SOURCE_ICON_EU, SOURCE_LIBREWXR
from custom_components.walk_the_dog.engine.grid import (
    MAX_SLOTS,
    SLOT,
    align,
    ceil_slot,
    floor_slot,
    slots_between,
    slots_for_window,
)

from .conftest import make_series

T = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 25, 7, 0, tzinfo=UTC), datetime(2026, 8, 25, 7, 0, tzinfo=UTC)),
        (datetime(2026, 8, 25, 7, 9, 59, tzinfo=UTC), datetime(2026, 8, 25, 7, 0, tzinfo=UTC)),
        (datetime(2026, 8, 25, 7, 15, tzinfo=UTC), datetime(2026, 8, 25, 7, 10, tzinfo=UTC)),
        (datetime(2026, 8, 25, 7, 59, tzinfo=UTC), datetime(2026, 8, 25, 7, 50, tzinfo=UTC)),
    ],
)
def test_floor_slot(moment: datetime, expected: datetime) -> None:
    """Any instant belongs to the grid slot that starts at or before it."""
    assert floor_slot(moment) == expected


def test_floor_slot_drops_sub_second_noise() -> None:
    """Microseconds never survive onto the grid — slot identity is exact equality."""
    assert floor_slot(datetime(2026, 8, 25, 7, 3, 21, 456789, tzinfo=UTC)) == T


def test_ceil_slot_is_identity_on_the_grid() -> None:
    """A time already on the grid is its own next slot; anything else rounds up."""
    assert ceil_slot(T) == T
    assert ceil_slot(T + timedelta(seconds=1)) == T + SLOT


def test_slots_between_is_half_open_at_the_start() -> None:
    """The slot containing the start is included even when the start is mid-slot."""
    assert slots_between(T + timedelta(minutes=3), T + timedelta(minutes=20)) == (T, T + SLOT)


def test_slots_between_reaches_into_a_partial_final_slot() -> None:
    """Rain in the last three minutes of a walk is rain on the walk."""
    assert slots_between(T, T + timedelta(minutes=23)) == (T, T + SLOT, T + 2 * SLOT)


def test_slots_between_empty_for_non_positive_span() -> None:
    """A zero or reversed interval covers nothing rather than raising."""
    assert slots_between(T, T) == ()
    assert slots_between(T, T - SLOT) == ()


def test_slots_between_is_bounded() -> None:
    """A pathological span cannot turn the slot walk into a long loop."""
    assert len(slots_between(T, T + timedelta(days=7))) == MAX_SLOTS


def test_slots_for_window_covers_the_whole_walk() -> None:
    """A 25-minute walk from 07:00 touches three slots."""
    assert slots_for_window(T, timedelta(minutes=25)) == (T, T + SLOT, T + 2 * SLOT)


def test_align_expands_an_hourly_source_over_its_six_slots() -> None:
    """An hourly value is valid over [H, H+1) — a step function, never interpolated."""
    series = make_series(SOURCE_ICON_EU, [0.0, 1.4], start=T)
    slots = slots_between(T, T + timedelta(hours=2))
    aligned = align(series, slots)

    assert len(aligned) == 12
    assert {aligned[T + index * SLOT] for index in range(6)} == {0.0}
    assert {aligned[T + index * SLOT] for index in range(6, 12)} == {1.4}


def test_align_is_the_identity_for_a_ten_minute_source() -> None:
    """The radar's own step is the grid step, so nothing is stretched or lost."""
    values = [0.0, 0.3, 2.9, 0.0]
    series = make_series(SOURCE_LIBREWXR, values, start=T)
    slots = slots_between(T, T + 4 * SLOT)

    assert align(series, slots) == {T + index * SLOT: value for index, value in enumerate(values)}


def test_align_omits_slots_past_the_horizon() -> None:
    """Beyond a source's last step it has no vote — not a zero one."""
    series = make_series(SOURCE_LIBREWXR, [0.0, 0.0], start=T)
    slots = slots_between(T, T + 4 * SLOT)

    assert set(align(series, slots)) == {T, T + SLOT}


def test_align_omits_slots_before_the_series_starts() -> None:
    """A source that only starts forecasting later says nothing about earlier slots."""
    series = make_series(SOURCE_LIBREWXR, [1.0], start=T + 2 * SLOT)

    assert set(align(series, slots_between(T, T + 4 * SLOT))) == {T + 2 * SLOT}


def test_align_leaves_a_provider_gap_uncovered() -> None:
    """A missing hour in the middle of a response is a gap, not a bridge."""
    hourly = make_series(SOURCE_ICON_EU, [0.0, 0.0, 0.0], start=T)
    series = replace(hourly, slots=(hourly.slots[0], hourly.slots[2]))
    slots = slots_between(T, T + timedelta(hours=3))
    aligned = align(series, slots)

    assert all(T + timedelta(hours=1) + index * SLOT not in aligned for index in range(6))
    assert len(aligned) == 12


def test_align_of_an_empty_series_is_empty() -> None:
    """A source that parsed to nothing contributes nothing, without special-casing upstream."""
    series = make_series(SOURCE_LIBREWXR, [], start=T)

    assert align(series, slots_between(T, T + SLOT)) == {}
