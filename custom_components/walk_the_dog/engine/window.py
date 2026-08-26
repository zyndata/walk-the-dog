"""Window evaluation, recommendation search and material change. PURE.

Implements docs/ARCHITECTURE.md § Walk-window evaluation & recommendation search:
score the scheduled walk window, and when it is not dry look outwards on the
10-minute grid for the nearest window that is — earlier beating later at equal
distance, because the dog waits less and nearer-term forecasts are better.

The search is bounded by the present as well as by the margins: a window that has
already begun is not advice, it is history, so `recommend` takes `now` and never
offers a start before it. `is_actionable` is the matching rule for the caller —
whether there is still time to do what the recommendation says.

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
    NOWCAST_SOURCES,
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


@dataclass(frozen=True, slots=True)
class Search:
    """How long the walk is, and how far the user will let it move.

    The three travel together everywhere the search goes — the slot grid to score,
    the candidate starts, the window each candidate covers — so they are one value
    rather than three parameters restated at every call.
    """

    duration: timedelta
    earlier_margin: timedelta
    later_margin: timedelta


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
    #: True when a radar nowcast reaches every slot of the window. False means the
    #: verdict rests on hourly models, which know *whether* far better than *when*.
    nowcast_covered: bool = False

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

    @property
    def recommended_end(self) -> datetime | None:
        """When the suggested walk would get home — the other half of the advice."""
        if self.recommended_start is None:
            return None
        return self.recommended_start + timedelta(seconds=self.duration_s)

    @property
    def provisional(self) -> bool:
        """True when no radar reaches the window being recommended.

        The hourly models see hours ahead and the radars only one, so a walk moved
        further out than the nowcast is a model-only answer — sound about *whether*
        it will rain, imprecise about *when*. It is not wrong, it is early: the
        coordinator keeps watching, and the radar confirms or corrects it as the
        hour approaches (docs/ARCHITECTURE.md § Coordinator scheduling).
        """
        window = self.recommended if self.recommended is not None else self.scheduled
        return not window.nowcast_covered


def evaluation_slots(scheduled_start: datetime, search: Search) -> tuple[datetime, ...]:
    """Every grid slot the search can possibly need — what the coordinator scores."""
    return slots_between(
        floor_slot(scheduled_start - search.earlier_margin),
        scheduled_start + search.later_margin + search.duration,
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
    # A radar has to reach *every* slot before the window counts as nowcast-backed:
    # one uncovered minute is exactly the minute the rain could start in.
    nowcast_covered = bool(slots) and all(
        score is not None and NOWCAST_SOURCES.intersection(score.contributors) for score in scores
    )

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
            nowcast_covered=False,
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
        nowcast_covered=nowcast_covered,
    )


def candidate_starts(
    scheduled_start: datetime,
    earlier_margin: timedelta,
    later_margin: timedelta,
    *,
    not_before: datetime | None = None,
) -> tuple[datetime, ...]:
    """Alternative window starts on the grid, nearest first, earlier winning ties.

    Candidates sit on the 10-minute grid rather than at exact offsets from the walk
    time, so a walk scheduled at 07:15 is offered 07:10 and 07:20 — the times the
    forecast actually resolves.

    `not_before` drops the ones that have already passed. Without it the search is
    happy to answer "set off at 21:20" at 22:31, because nothing in the margins
    knows what time it is; that is what this parameter exists to prevent.
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
    if not_before is not None:
        starts = [start for start in starts if start >= not_before]
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
    search: Search,
    now: datetime | None = None,
) -> Recommendation:
    """Evaluate the scheduled walk and, if needed, find the nearest dry window.

    `now` bounds the search to windows the user could still set off for. Omitting
    it searches the whole margin, past included, which only a test evaluating the
    geometry in isolation should want.
    """
    scheduled = evaluate_window(consensus, scheduled_start, search.duration)
    sources = source_breakdown(consensus, scheduled_start, search.duration)
    base = {
        "scheduled_start": scheduled_start,
        "duration_s": int(search.duration.total_seconds()),
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

    candidates = candidate_starts(
        scheduled_start, search.earlier_margin, search.later_margin, not_before=now
    )
    for start in candidates:
        candidate = evaluate_window(consensus, start, search.duration)
        if candidate.dry:
            return Recommendation(
                direction=(DIRECTION_EARLIER if start < scheduled_start else DIRECTION_LATER),
                recommended_start=start,
                recommended=candidate,
                **base,
            )

    return Recommendation(direction=DIRECTION_NO_DRY_WINDOW, **base)


def is_actionable(recommendation: Recommendation, now: datetime) -> bool:
    """Whether there is still time to do what the recommendation says.

    The engine answers about a walk, not about a moment, so the same recommendation
    stays true long after it stops being useful. Two things can expire:

    * a recommendation with a target — "set off at 21:20" — expires when 21:20 does;
    * `no_dry_window`, which has no target, expires when the walk itself starts:
      once the user is out with the dog, "take a raincoat" is no longer a decision.

    `none` and `unknown` have nothing to act on either way and are filtered by the
    caller before this is asked (`notifier.ALERT_DIRECTIONS`).
    """
    target = recommendation.recommended_start
    if target is not None:
        return now <= target
    return now < recommendation.scheduled_start


def superseded_by_the_clock(
    previous: Recommendation | None, current: Recommendation, now: datetime
) -> bool:
    """True when the only thing that changed since the last alert is the time.

    A walk told at 04:00 to set off at 04:30 has, at 04:40, no dry window left —
    not because the weather moved but because 04:30 did. The direction flips from
    `earlier` to `no_dry_window` and `is_material_change` rightly calls that a
    different answer, yet the forecast behind it is the one the user already has.
    Sending it again is nagging someone for not taking advice they declined.

    A window that turns wet while it is still ahead is a different matter, and is
    not caught here: `previous.recommended_start` has to have passed.
    """
    return (
        previous is not None
        and current.direction == DIRECTION_NO_DRY_WINDOW
        and previous.recommended_start is not None
        and previous.recommended_start < now
    )


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
    "Search",
    "SourceBreakdown",
    "WindowVerdict",
    "candidate_starts",
    "evaluate_window",
    "evaluation_slots",
    "is_actionable",
    "is_material_change",
    "recommend",
    "source_breakdown",
    "superseded_by_the_clock",
]
