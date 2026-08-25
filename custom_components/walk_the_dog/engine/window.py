"""Window evaluation, recommendation search and material change. PURE.

Implements docs/ARCHITECTURE.md § Walk-window evaluation & recommendation search:
score the scheduled walk window, and when it is not dry look outwards on the
10-minute grid for the nearest window that is — earlier beating later at equal
distance, because the dog waits less and nearer-term forecasts are better.

No I/O, no homeassistant imports, no clock reads: `now` is always a parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from ..const import (
    INTENSITY_NONE,
    INTENSITY_THRESHOLD_HEAVY,
    INTENSITY_THRESHOLD_LIGHT,
    INTENSITY_THRESHOLD_MODERATE,
    intensity_class,
)
from .grid import SLOT, floor_slot, slots_between, slots_for_window

if TYPE_CHECKING:
    from datetime import datetime

    from .consensus import Consensus

#: What to do about the next walk. `none` = go as planned; `unknown` = no source
#: reaches the walk at all, which is never reported as good news.
DIRECTION_NONE = "none"
DIRECTION_EARLIER = "earlier"
DIRECTION_LATER = "later"
DIRECTION_NO_DRY_WINDOW = "no_dry_window"
DIRECTION_UNKNOWN = "unknown"

VERDICT_DRY = "dry"
VERDICT_WET = "wet"
VERDICT_UNKNOWN = "unknown"

#: Confidence ceiling for a window the sources only partly cover.
HORIZON_CONFIDENCE_CAP = 0.5

#: Material change thresholds (docs/ARCHITECTURE.md § Material change).
MATERIAL_SHIFT = timedelta(minutes=20)
HYSTERESIS_DRY = 0.4
HYSTERESIS_WET = 0.6

#: Intensity classes in ascending order, for "changed by ≥ 1 class" comparisons.
INTENSITY_ORDER = (
    INTENSITY_NONE,
    INTENSITY_THRESHOLD_LIGHT,
    INTENSITY_THRESHOLD_MODERATE,
    INTENSITY_THRESHOLD_HEAVY,
)


@dataclass(frozen=True)
class WindowVerdict:
    """The consensus verdict for one candidate walk window."""

    start: datetime
    end: datetime
    dry: bool
    risk: float
    confidence: float
    peak_mm_h: float
    covered_slots: int
    total_slots: int
    degraded: bool
    horizon_limited: bool

    @property
    def has_data(self) -> bool:
        """False when not one slot of the window is covered by any source."""
        return self.covered_slots > 0

    @property
    def peak_intensity(self) -> str:
        """Class of the heaviest expected rain in the window."""
        return intensity_class(self.peak_mm_h)

    @property
    def verdict(self) -> str:
        """`dry` / `wet` / `unknown` — the window in one word."""
        if not self.has_data:
            return VERDICT_UNKNOWN
        return VERDICT_DRY if self.dry else VERDICT_WET


@dataclass(frozen=True)
class SourceBreakdown:
    """One source's own verdict on the scheduled window, and why it counted or not."""

    source_id: str
    state: str
    verdict: str
    contributed: bool
    weight: float
    age_s: int | None = None
    peak_mm_h: float | None = None
    detail: str | None = None

    @property
    def peak_intensity(self) -> str | None:
        """Class of this source's own peak over the scheduled window."""
        return None if self.peak_mm_h is None else intensity_class(self.peak_mm_h)


@dataclass(frozen=True)
class Recommendation:
    """The engine's complete answer about the next walk — the phase 5-7 output contract."""

    direction: str
    scheduled_start: datetime
    duration_s: int
    scheduled: WindowVerdict
    recommended_start: datetime | None = None
    recommended: WindowVerdict | None = None
    sources: tuple[SourceBreakdown, ...] = ()

    @property
    def risk(self) -> float:
        """Risk of the scheduled window — what the user asked about."""
        return self.scheduled.risk

    @property
    def confidence(self) -> float:
        """Confidence in the scheduled window's verdict."""
        return self.scheduled.confidence

    @property
    def peak_intensity(self) -> str:
        """Expected intensity class over the scheduled window."""
        return self.scheduled.peak_intensity

    @property
    def degraded(self) -> bool:
        """True when some slot rested on a single source."""
        return self.scheduled.degraded

    @property
    def horizon_limited(self) -> bool:
        """True when the walk reaches past what the sources forecast."""
        return self.scheduled.horizon_limited

    @property
    def shift(self) -> timedelta | None:
        """How far the recommendation moves the walk, signed (negative = earlier)."""
        if self.recommended_start is None:
            return None
        return self.recommended_start - self.scheduled_start


def evaluation_slots(
    scheduled_start: datetime,
    duration: timedelta,
    earlier_margin: timedelta,
    later_margin: timedelta,
) -> tuple[datetime, ...]:
    """Every grid slot the search can possibly need — what the coordinator scores."""
    return slots_between(
        floor_slot(scheduled_start - earlier_margin),
        scheduled_start + later_margin + duration,
    )


def evaluate_window(consensus: Consensus, start: datetime, duration: timedelta) -> WindowVerdict:
    """Score one candidate window.

    Window risk is the worst slot and window confidence the weakest slot: a walk is
    only as dry as its wettest minute, and only as certain as its least certain one.
    A window is dry solely when every one of its slots is both covered and below the
    threshold — an uncovered slot is never counted as dry.
    """
    slots = slots_for_window(start, duration)
    scores = [consensus.at(slot) for slot in slots]
    covered = [score for score in scores if score is not None and score.has_data]
    end = start + duration

    if not covered:
        return WindowVerdict(
            start=start,
            end=end,
            dry=False,
            risk=0.0,
            confidence=0.0,
            peak_mm_h=0.0,
            covered_slots=0,
            total_slots=len(slots),
            degraded=False,
            horizon_limited=bool(slots),
        )

    horizon_limited = len(covered) < len(slots)
    confidence = min(score.confidence for score in covered)
    if horizon_limited:
        confidence = min(confidence, HORIZON_CONFIDENCE_CAP)

    return WindowVerdict(
        start=start,
        end=end,
        dry=not horizon_limited and not any(score.wet for score in covered),
        risk=max(score.risk for score in covered),
        confidence=confidence,
        peak_mm_h=max(score.intensity_mm_h for score in covered),
        covered_slots=len(covered),
        total_slots=len(slots),
        degraded=any(score.n_sources == 1 for score in covered),
        horizon_limited=horizon_limited,
    )


def candidate_starts(
    scheduled_start: datetime,
    earlier_margin: timedelta,
    later_margin: timedelta,
) -> tuple[datetime, ...]:
    """Alternative window starts on the grid, nearest first, earlier winning ties.

    Candidates sit on the 10-minute grid rather than at exact offsets from the walk
    time, so a walk scheduled at 07:15 is offered 07:10 and 07:20 — the times the
    forecast actually resolves.
    """
    base = floor_slot(scheduled_start)
    earliest = scheduled_start - earlier_margin
    latest = scheduled_start + later_margin

    starts: list[datetime] = []
    candidate = base
    while candidate >= earliest:
        starts.append(candidate)
        candidate -= SLOT
    candidate = base + SLOT
    while candidate <= latest:
        starts.append(candidate)
        candidate += SLOT

    starts = [start for start in starts if start != scheduled_start]
    starts.sort(key=lambda start: (abs(start - scheduled_start), start > scheduled_start))
    return tuple(starts)


def source_breakdown(
    consensus: Consensus, start: datetime, duration: timedelta
) -> tuple[SourceBreakdown, ...]:
    """Each source's own verdict on the scheduled window, alongside its status.

    This is what makes a recommendation explainable: "the radar says wet, both
    models say dry" is a different message from "everything says wet", even when
    the weighted vote lands in the same place.
    """
    slots = slots_for_window(start, duration)
    breakdown: list[SourceBreakdown] = []
    for status in consensus.statuses:
        values = [
            value
            for slot in slots
            if (value := consensus.aligned.get(status.source_id, {}).get(slot)) is not None
        ]
        peak = max(values) if values else None
        if peak is None:
            verdict = VERDICT_UNKNOWN
        elif peak >= consensus.threshold_mm_h:
            verdict = VERDICT_WET
        else:
            verdict = VERDICT_DRY
        breakdown.append(
            SourceBreakdown(
                source_id=status.source_id,
                state=status.state,
                verdict=verdict,
                contributed=status.contributed,
                weight=consensus.weights.get(status.source_id, 0.0),
                age_s=status.age_s,
                peak_mm_h=peak,
                detail=status.detail,
            )
        )
    return tuple(breakdown)


def recommend(
    consensus: Consensus,
    *,
    scheduled_start: datetime,
    duration: timedelta,
    earlier_margin: timedelta,
    later_margin: timedelta,
) -> Recommendation:
    """Evaluate the scheduled walk and, if needed, find the nearest dry window."""
    scheduled = evaluate_window(consensus, scheduled_start, duration)
    sources = source_breakdown(consensus, scheduled_start, duration)
    base = {
        "scheduled_start": scheduled_start,
        "duration_s": int(duration.total_seconds()),
        "scheduled": scheduled,
        "sources": sources,
    }

    if not scheduled.has_data:
        return Recommendation(direction=DIRECTION_UNKNOWN, **base)

    if scheduled.dry:
        return Recommendation(
            direction=DIRECTION_NONE,
            recommended_start=scheduled_start,
            recommended=scheduled,
            **base,
        )

    for start in candidate_starts(scheduled_start, earlier_margin, later_margin):
        candidate = evaluate_window(consensus, start, duration)
        if candidate.dry:
            return Recommendation(
                direction=(DIRECTION_EARLIER if start < scheduled_start else DIRECTION_LATER),
                recommended_start=start,
                recommended=candidate,
                **base,
            )

    return Recommendation(direction=DIRECTION_NO_DRY_WINDOW, **base)


def is_material_change(previous: Recommendation | None, current: Recommendation) -> bool:
    """Whether `current` differs enough from the last *notified* recommendation to re-notify.

    Any one of the four rules in docs/ARCHITECTURE.md § Material change suffices.
    The verdict rule is deliberately hysteretic: a scheduled-window risk drifting
    across 0.5 must reach 0.4 or 0.6 before it counts, so a forecast sitting on the
    fence cannot notify the user twice a cycle.
    """
    if previous is None:
        return True
    if current.direction != previous.direction:
        return True
    if (
        previous.recommended_start is not None
        and current.recommended_start is not None
        and abs(current.recommended_start - previous.recommended_start) >= MATERIAL_SHIFT
    ):
        return True
    if previous.scheduled.dry and current.risk >= HYSTERESIS_WET:
        return True
    if not previous.scheduled.dry and current.risk < HYSTERESIS_DRY:
        return True
    return INTENSITY_ORDER.index(current.peak_intensity) != INTENSITY_ORDER.index(
        previous.peak_intensity
    )


__all__ = [
    "DIRECTION_EARLIER",
    "DIRECTION_LATER",
    "DIRECTION_NONE",
    "DIRECTION_NO_DRY_WINDOW",
    "DIRECTION_UNKNOWN",
    "HYSTERESIS_DRY",
    "HYSTERESIS_WET",
    "MATERIAL_SHIFT",
    "VERDICT_DRY",
    "VERDICT_UNKNOWN",
    "VERDICT_WET",
    "Recommendation",
    "SourceBreakdown",
    "WindowVerdict",
    "candidate_starts",
    "evaluate_window",
    "evaluation_slots",
    "is_material_change",
    "recommend",
    "source_breakdown",
]
