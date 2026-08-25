"""Per-slot risk and confidence from the sources' normalized series. PURE.

The weighted vote from docs/ARCHITECTURE.md § Consensus scoring, verbatim:

    w_i(t)        = reliability_i * freshness_i
    vote_i(t)     = 1 if intensity_i(t) >= threshold else 0
    risk(t)       = sum(w_i * vote_i(t)) / sum(w_i)
    agreement(t)  = abs(2 * risk(t) - 1)
    confidence(t) = agreement(t) * cap(n_t)

`risk` is a weighted fraction of sources predicting rain, not a probability of
rain: two disagreeing sources give 0.5 whatever they each think the chance is.
Confidence is what carries the disagreement, and the source-count cap is what
carries "we only heard from one of them".

No I/O, no homeassistant imports, no clock reads: `now` is always a parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..const import INTENSITY_MM_H, intensity_class
from ..sources.base import (
    STALE_FACTOR,
    STATE_OK,
    STATE_OUT_OF_RANGE,
    STATE_STALE,
    UPDATE_INTERVAL_S,
    SourceStatus,
)
from .grid import align

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

    from ..sources.base import SourceSeries

#: Risk at or above which a slot counts as wet — a strict majority by weight.
WET_RISK = 0.5

#: Confidence ceiling by number of contributing sources (docs/DATA_SOURCES.md
#: § Fallback strategy: a single source can never be reported as certain).
SOURCE_COUNT_CAP: dict[int, float] = {0: 0.0, 1: 0.5, 2: 0.8}
FULL_CAP = 1.0

#: Freshness floor, reached at STALE_FACTOR x the publication interval; past that
#: the series is stale and dropped entirely rather than down-weighted further.
MIN_FRESHNESS = 0.5


def freshness(source_id: str, age_s: int) -> float:
    """Weight multiplier for age: 1.0 while fresh, 0.5 at the stale edge, 0 beyond it."""
    interval = UPDATE_INTERVAL_S[source_id]
    if age_s <= interval:
        return 1.0
    if age_s > STALE_FACTOR * interval:
        return 0.0
    decayed = (age_s - interval) / ((STALE_FACTOR - 1) * interval)
    return 1.0 - (1.0 - MIN_FRESHNESS) * decayed


def source_weight(series: SourceSeries, now: datetime) -> float:
    """`w_i` for this cycle: static reliability decayed by how old the data is."""
    return series.reliability * freshness(series.source_id, series.age_s(now))


@dataclass(frozen=True)
class SlotScore:
    """What the sources jointly say about one 10-minute slot."""

    start: datetime
    risk: float
    confidence: float
    intensity_mm_h: float
    contributors: tuple[str, ...]

    @property
    def n_sources(self) -> int:
        """How many sources voted on this slot."""
        return len(self.contributors)

    @property
    def has_data(self) -> bool:
        """False when no source reaches this slot — never the same as "no rain"."""
        return bool(self.contributors)

    @property
    def wet(self) -> bool:
        """True when the weighted majority expects rain at or above the threshold."""
        return self.has_data and self.risk >= WET_RISK

    @property
    def intensity(self) -> str:
        """Expected intensity class — for display only, never for the vote."""
        return intensity_class(self.intensity_mm_h)


@dataclass(frozen=True)
class Consensus:
    """Scored slots plus everything the window layer needs to explain them."""

    slots: tuple[SlotScore, ...]
    statuses: tuple[SourceStatus, ...]
    weights: Mapping[str, float]
    aligned: Mapping[str, Mapping[datetime, float]]
    threshold_mm_h: float
    _index: dict[datetime, SlotScore] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Index slots by start so window evaluation is a lookup, not a scan."""
        self._index.update({slot.start: slot for slot in self.slots})

    def at(self, start: datetime) -> SlotScore | None:
        """The score for the grid slot starting at `start`, if it was scored."""
        return self._index.get(start)

    @property
    def contributing(self) -> tuple[str, ...]:
        """Source ids that voted on at least one scored slot."""
        return tuple(status.source_id for status in self.statuses if status.contributed)


def build_consensus(
    series: Iterable[SourceSeries],
    statuses: Iterable[SourceStatus],
    *,
    slots: Sequence[datetime],
    threshold: str,
    now: datetime,
) -> Consensus:
    """Score every grid slot in `slots` from the sources' series.

    `threshold` is the user's intensity class; a source votes "wet" for a slot when
    its intensity there reaches that class's mm/h lower bound (docs/CONFIG.md).
    Stale series are dropped before the vote, so a source that stopped publishing
    stops influencing the result instead of freezing its last opinion in place.
    """
    threshold_mm_h = INTENSITY_MM_H[threshold]
    by_id = {s.source_id: s for s in series}
    status_by_id = {status.source_id: status for status in statuses}

    weights: dict[str, float] = {}
    aligned: dict[str, Mapping[datetime, float]] = {}
    for source_id, source in sorted(by_id.items()):
        weight = source_weight(source, now)
        weights[source_id] = weight
        if weight > 0.0:
            aligned[source_id] = align(source, slots)

    scored = tuple(_score_slot(slot, aligned, weights, threshold_mm_h) for slot in slots)
    voted = {
        source_id for source_id, values in aligned.items() if any(slot in values for slot in slots)
    }

    return Consensus(
        slots=scored,
        statuses=_refine_statuses(status_by_id, by_id, weights, voted, now),
        weights=weights,
        aligned=aligned,
        threshold_mm_h=threshold_mm_h,
    )


def _score_slot(
    slot: datetime,
    aligned: Mapping[str, Mapping[datetime, float]],
    weights: Mapping[str, float],
    threshold_mm_h: float,
) -> SlotScore:
    """Weighted vote over the sources that reach this slot."""
    contributors = tuple(source_id for source_id in sorted(aligned) if slot in aligned[source_id])
    if not contributors:
        return SlotScore(slot, risk=0.0, confidence=0.0, intensity_mm_h=0.0, contributors=())

    total = sum(weights[source_id] for source_id in contributors)
    wet = sum(
        weights[source_id]
        for source_id in contributors
        if aligned[source_id][slot] >= threshold_mm_h
    )
    mean = sum(weights[source_id] * aligned[source_id][slot] for source_id in contributors) / total
    risk = wet / total
    cap = SOURCE_COUNT_CAP.get(len(contributors), FULL_CAP)
    return SlotScore(
        start=slot,
        risk=risk,
        confidence=abs(2.0 * risk - 1.0) * cap,
        intensity_mm_h=mean,
        contributors=contributors,
    )


def _refine_statuses(
    status_by_id: Mapping[str, SourceStatus],
    series_by_id: Mapping[str, SourceSeries],
    weights: Mapping[str, float],
    voted: set[str],
    now: datetime,
) -> tuple[SourceStatus, ...]:
    """Restate each adapter's status in terms of what it actually contributed.

    `out_of_range` is the engine's to assign (docs/ARCHITECTURE.md § Data flow):
    a source can be perfectly fresh and still have nothing to say about a walk
    beyond its horizon, which is a different thing from being stale or failed.
    """
    refined: list[SourceStatus] = []
    for source_id in sorted(set(status_by_id) | set(series_by_id)):
        reported = status_by_id.get(source_id)
        source = series_by_id.get(source_id)
        if source is None:
            refined.append(
                SourceStatus(
                    source_id,
                    reported.state if reported else STATE_OUT_OF_RANGE,
                    age_s=reported.age_s if reported else None,
                    contributed=False,
                    detail=reported.detail if reported else None,
                )
            )
            continue
        if weights.get(source_id, 0.0) <= 0.0:
            state = STATE_STALE
        elif source_id in voted:
            state = STATE_OK
        else:
            state = STATE_OUT_OF_RANGE
        refined.append(
            SourceStatus(
                source_id,
                state,
                age_s=source.age_s(now),
                contributed=state == STATE_OK,
                detail=reported.detail if reported else None,
            )
        )
    return tuple(refined)
