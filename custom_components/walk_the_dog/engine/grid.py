"""Shared 10-minute UTC time grid and source-to-grid alignment. PURE.

Sources publish on their own steps — 10 minutes for the radar nowcast, 60 for
every NWP source — so before anything can be compared they are projected onto one
common grid (docs/ARCHITECTURE.md § Consensus scoring). The projection is a step
function and never interpolates: the value published for a step is the value of
every grid slot that step covers, and a grid slot no step covers simply has no
value for that source, which is what makes "out of range" distinguishable from
"forecast zero".

No I/O, no homeassistant imports, no clock reads: `now` is always a parameter.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..const import SLOT_MINUTES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..sources.base import SourceSeries

#: Length of one grid slot. Ten minutes is LibreWXR's frame cadence — the finest
#: step any source publishes, so the grid loses no information from any of them.
SLOT = timedelta(minutes=SLOT_MINUTES)

#: Guard against a pathological duration turning the slot walk into a long loop.
#: 24 h of slots is far beyond any nowcast horizon the engine can be asked about.
MAX_SLOTS = 144


def floor_slot(moment: datetime) -> datetime:
    """Start of the grid slot containing `moment`."""
    seconds = (moment.minute % SLOT_MINUTES) * 60 + moment.second
    return (moment - timedelta(seconds=seconds)).replace(microsecond=0)


def ceil_slot(moment: datetime) -> datetime:
    """Start of the first grid slot at or after `moment`."""
    floored = floor_slot(moment)
    return floored if floored == moment else floored + SLOT


def slots_between(start: datetime, end: datetime) -> tuple[datetime, ...]:
    """Grid slots overlapping the half-open interval `[start, end)`.

    The slot containing `start` is included even when `start` falls inside it, and
    so is the slot a mid-slot `end` reaches into: rain in the last three minutes of
    a walk is rain on the walk.
    """
    if end <= start:
        return ()
    slots: list[datetime] = []
    current = floor_slot(start)
    while current < end and len(slots) < MAX_SLOTS:
        slots.append(current)
        current += SLOT
    return tuple(slots)


def slots_for_window(start: datetime, duration: timedelta) -> tuple[datetime, ...]:
    """Grid slots a walk starting at `start` and lasting `duration` covers."""
    return slots_between(start, start + duration)


def align(series: SourceSeries, slots: Sequence[datetime]) -> dict[datetime, float]:
    """Project one source's own steps onto the given grid slots.

    Returns only the slots the source actually covers. A slot is missing when it
    falls before the series starts, after its horizon, or in a gap the provider
    left — all three mean the same thing to the consensus vote: this source casts
    no vote here.
    """
    if not series.slots:
        return {}
    step = timedelta(seconds=series.step_s)
    starts = [start for start, _ in series.slots]
    values = [value for _, value in series.slots]
    aligned: dict[datetime, float] = {}
    for slot in slots:
        index = bisect_right(starts, slot) - 1
        if index >= 0 and slot < starts[index] + step:
            aligned[slot] = values[index]
    return aligned
