"""Decision engine: normalized source series in, a `Recommendation` out.

PURE package: no I/O, no homeassistant imports, no clock reads — `now` is always
a parameter (docs/ARCHITECTURE.md § Module layout, layering rules). That is what
lets phase 4 test every rain scenario as plain arithmetic, and what keeps the
whole decision reproducible from a recorded set of series.

Typical use from the coordinator, once per update cycle:

    search = Search(duration, earlier_margin, later_margin)
    slots = evaluation_slots(walk_start, search)
    consensus = build_consensus(series, statuses, slots=slots,
                                threshold=threshold, now=now)
    recommendation = recommend(consensus, scheduled_start=walk_start,
                               search=search, now=now)

`now` is not decoration there: without it the search will happily suggest a walk
time that has already passed.
"""

from __future__ import annotations

from .consensus import (
    SOURCE_COUNT_CAP,
    WET_RISK,
    Consensus,
    SlotScore,
    build_consensus,
    freshness,
    source_weight,
)
from .grid import SLOT, align, ceil_slot, floor_slot, slots_between, slots_for_window
from .window import (
    DIRECTION_EARLIER,
    DIRECTION_LATER,
    DIRECTION_NO_DRY_WINDOW,
    DIRECTION_NONE,
    DIRECTION_SHORTER,
    DIRECTION_UNKNOWN,
    VERDICT_DRY,
    VERDICT_UNKNOWN,
    VERDICT_WET,
    Recommendation,
    Search,
    SourceBreakdown,
    WindowVerdict,
    candidate_starts,
    evaluate_window,
    evaluation_slots,
    is_actionable,
    is_material_change,
    recommend,
    shorter_durations,
    source_breakdown,
    superseded_by_the_clock,
)

__all__ = [
    "DIRECTION_EARLIER",
    "DIRECTION_LATER",
    "DIRECTION_NONE",
    "DIRECTION_NO_DRY_WINDOW",
    "DIRECTION_SHORTER",
    "DIRECTION_UNKNOWN",
    "SLOT",
    "SOURCE_COUNT_CAP",
    "VERDICT_DRY",
    "VERDICT_UNKNOWN",
    "VERDICT_WET",
    "WET_RISK",
    "Consensus",
    "Recommendation",
    "Search",
    "SlotScore",
    "SourceBreakdown",
    "WindowVerdict",
    "align",
    "build_consensus",
    "candidate_starts",
    "ceil_slot",
    "evaluate_window",
    "evaluation_slots",
    "floor_slot",
    "freshness",
    "is_actionable",
    "is_material_change",
    "recommend",
    "shorter_durations",
    "slots_between",
    "slots_for_window",
    "source_breakdown",
    "source_weight",
    "superseded_by_the_clock",
]
