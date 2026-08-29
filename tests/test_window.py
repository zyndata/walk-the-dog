"""Window evaluation, the search for a dry walk, and when to bother the user again.

The scenarios here are the ones the plan calls out — rain at the start of the
walk, rain at the end, everything dry, everything wet, sources disagreeing, a
stale source, a single source left, and a walk longer than anything the sources
forecast — expressed as patterns of wet 10-minute slots around a 07:00 walk.

Each source is given a 10-minute step so every slot can be set independently; the
engine is indifferent to a source's native step, which `test_grid.py` covers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.walk_the_dog.const import (
    INTENSITY_THRESHOLD_LIGHT,
    INTENSITY_THRESHOLD_MODERATE,
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
)
from custom_components.walk_the_dog.engine.consensus import build_consensus
from custom_components.walk_the_dog.engine.grid import SLOT
from custom_components.walk_the_dog.engine.window import (
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
from custom_components.walk_the_dog.sources.base import STATE_OK, UPDATE_INTERVAL_S

from .conftest import make_series, make_status

#: The walk, and the moment the notification decision is taken (T - earlier margin).
T = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
DURATION = timedelta(minutes=30)
EARLIER = timedelta(minutes=60)
LATER = timedelta(minutes=30)
NOW = T - EARLIER

#: Slot index 0 is 06:00, so the scheduled walk covers indices 6, 7 and 8.
SCHEDULED_INDEX = 6
SLOT_COUNT = 12
ALL_SOURCES = (SOURCE_LIBREWXR, SOURCE_KNMI, SOURCE_ICON_EU)

WET_MM_H = 1.0
DRY_MM_H = 0.0


def _pattern(*wet_indices: int, length: int = SLOT_COUNT) -> list[float]:
    """A per-slot mm/h series, raining in the named slots and dry everywhere else."""
    return [WET_MM_H if index in wet_indices else DRY_MM_H for index in range(length)]


def _consensus(
    per_source: dict[str, list[float]],
    *,
    duration: timedelta = DURATION,
    threshold: str = INTENSITY_THRESHOLD_LIGHT,
    now: datetime = NOW,
    statuses=None,
):
    """Score every slot the search can reach, from one series per named source."""
    series = [
        make_series(source_id, values, start=NOW, step_s=int(SLOT.total_seconds()))
        for source_id, values in per_source.items()
    ]
    if statuses is None:
        statuses = [make_status(source_id) for source_id in per_source]
    return build_consensus(
        series,
        statuses,
        slots=evaluation_slots(T, Search(duration, EARLIER, LATER)),
        threshold=threshold,
        now=now,
    )


def _agreed(*wet_indices: int, length: int = SLOT_COUNT, **kwargs):
    """A consensus in which all three sources say exactly the same thing."""
    values = _pattern(*wet_indices, length=length)
    return _consensus({source_id: list(values) for source_id in ALL_SOURCES}, **kwargs)


def _recommend(
    consensus,
    *,
    duration: timedelta = DURATION,
    later: timedelta = LATER,
    minimum: timedelta | None = None,
    **kwargs,
):
    """`minimum` left None is shortening switched off — the pre-phase-10 behaviour."""
    search = Search(duration, kwargs.pop("earlier", EARLIER), later, min_duration=minimum)
    return recommend(consensus, scheduled_start=T, search=search, **kwargs)


# --- evaluating a single window -------------------------------------------


def test_a_dry_window_is_dry_at_full_confidence() -> None:
    """Nothing wet anywhere, all three sources agreeing: the walk goes ahead."""
    verdict = evaluate_window(_agreed(), T, DURATION)

    assert verdict.dry
    assert verdict.verdict == VERDICT_DRY
    assert verdict.risk == 0.0
    assert verdict.confidence == pytest.approx(1.0)
    assert verdict.covered_slots == verdict.total_slots == 3
    assert not verdict.degraded
    assert not verdict.horizon_limited


def test_window_risk_is_its_worst_slot() -> None:
    """One wet slot in the middle makes the whole window wet — a walk is not an average."""
    verdict = evaluate_window(_agreed(7), T, DURATION)

    assert not verdict.dry
    assert verdict.verdict == VERDICT_WET
    assert verdict.risk == pytest.approx(1.0)


def test_window_confidence_is_its_weakest_slot() -> None:
    """One slot the sources argue over makes the verdict uncertain, dry or not."""
    consensus = _consensus(
        {
            SOURCE_LIBREWXR: _pattern(7),
            SOURCE_KNMI: _pattern(),
            SOURCE_ICON_EU: _pattern(),
        }
    )
    verdict = evaluate_window(consensus, T, DURATION)

    disputed = 1.0 / (1.0 + 0.9 + 0.8)
    assert verdict.dry
    assert verdict.risk == pytest.approx(disputed)
    assert verdict.confidence == pytest.approx(abs(2 * disputed - 1))


def test_peak_intensity_is_the_heaviest_slot_in_the_window() -> None:
    """The class shown to the user comes from the worst moment of the walk."""
    values = [0.0] * SLOT_COUNT
    values[7] = 5.0
    consensus = _consensus({source: list(values) for source in ALL_SOURCES})
    verdict = evaluate_window(consensus, T, DURATION)

    assert verdict.peak_mm_h == pytest.approx(5.0)
    assert verdict.peak_intensity == INTENSITY_THRESHOLD_MODERATE


def test_a_partly_covered_window_is_never_dry() -> None:
    """A slot no source reaches cannot be counted as dry, however calm the rest looks."""
    verdict = evaluate_window(_agreed(length=8), T, DURATION)

    assert not verdict.dry
    assert verdict.horizon_limited
    assert verdict.covered_slots == 2
    assert verdict.total_slots == 3
    assert verdict.confidence <= 0.5


def test_an_uncovered_window_has_no_data_at_all() -> None:
    """Beyond every source's horizon the answer is "unknown", not "fine"."""
    verdict = evaluate_window(_agreed(length=3), T, DURATION)

    assert not verdict.has_data
    assert verdict.verdict == VERDICT_UNKNOWN
    assert verdict.confidence == 0.0


def test_a_single_source_window_is_flagged_degraded() -> None:
    """One source left is still a usable answer — but the user is told it is thin."""
    verdict = evaluate_window(_consensus({SOURCE_LIBREWXR: _pattern()}), T, DURATION)

    assert verdict.dry
    assert verdict.degraded
    assert verdict.confidence == pytest.approx(0.5)


def test_a_stale_source_leaves_the_survivors_to_decide() -> None:
    """A stale radar frame neither votes nor drags confidence down with a phantom opinion."""
    stale_at = NOW - timedelta(seconds=3 * UPDATE_INTERVAL_S[SOURCE_LIBREWXR] + 60)
    series = [
        make_series(
            SOURCE_LIBREWXR,
            _pattern(*range(SLOT_COUNT)),
            start=NOW,
            step_s=int(SLOT.total_seconds()),
            issued_at=stale_at,
        ),
        make_series(SOURCE_KNMI, _pattern(), start=NOW, step_s=int(SLOT.total_seconds())),
        make_series(SOURCE_ICON_EU, _pattern(), start=NOW, step_s=int(SLOT.total_seconds())),
    ]
    consensus = build_consensus(
        series,
        [make_status(item.source_id) for item in series],
        slots=evaluation_slots(T, Search(DURATION, EARLIER, LATER)),
        threshold=INTENSITY_THRESHOLD_LIGHT,
        now=NOW,
    )

    verdict = evaluate_window(consensus, T, DURATION)
    assert verdict.dry
    assert verdict.confidence == pytest.approx(0.8)


# --- searching for a dry window -------------------------------------------


def test_a_dry_walk_needs_no_recommendation() -> None:
    """Nothing to suggest: go at the time you planned."""
    result = _recommend(_agreed())

    assert result.direction == DIRECTION_NONE
    assert result.recommended_start == T
    assert result.shift == timedelta(0)


def test_rain_at_the_start_of_the_walk_pushes_it_later() -> None:
    """A shower over the first slot rules out going earlier — every earlier window includes it."""
    result = _recommend(_agreed(SCHEDULED_INDEX))

    assert result.direction == DIRECTION_LATER
    assert result.recommended_start == T + SLOT
    assert result.recommended.dry


def test_rain_at_the_end_of_the_walk_pulls_it_earlier() -> None:
    """Ten minutes earlier clears the shower, and earlier is what the dog gets."""
    result = _recommend(_agreed(SCHEDULED_INDEX + 2))

    assert result.direction == DIRECTION_EARLIER
    assert result.recommended_start == T - SLOT
    assert result.shift == -SLOT


def test_earlier_beats_later_at_equal_distance() -> None:
    """When both directions clear the rain at 30 minutes, the earlier walk wins."""
    result = _recommend(_agreed(SCHEDULED_INDEX, SCHEDULED_INDEX + 1, SCHEDULED_INDEX + 2))

    assert result.direction == DIRECTION_EARLIER
    assert result.recommended_start == T - 3 * SLOT


def test_rain_everywhere_admits_no_dry_window() -> None:
    """When there is no dry walk to be had, the integration says so instead of guessing."""
    result = _recommend(_agreed(*range(SLOT_COUNT)))

    assert result.direction == DIRECTION_NO_DRY_WINDOW
    assert result.recommended_start is None
    assert result.shift is None
    assert result.risk == pytest.approx(1.0)


def test_the_search_stays_inside_the_configured_margins() -> None:
    """A dry window the user said they would not accept is not offered."""
    result = _recommend(
        _agreed(5, 6, 7, 8), earlier=timedelta(minutes=10), later=timedelta(minutes=10)
    )

    assert result.direction == DIRECTION_NO_DRY_WINDOW


def test_a_walk_running_past_the_horizon_is_moved_into_covered_ground() -> None:
    """A two-hour walk outruns the nowcast, so the one window the sources cover wins."""
    long_walk = timedelta(hours=2)
    result = _recommend(_agreed(duration=long_walk), duration=long_walk)

    assert result.horizon_limited
    assert result.scheduled.covered_slots < result.scheduled.total_slots
    assert result.direction == DIRECTION_EARLIER
    assert result.recommended_start == NOW


def test_a_walk_longer_than_every_candidate_window_admits_no_dry_window() -> None:
    """When nothing the sources say can cover a whole walk, no window is offered as dry."""
    long_walk = timedelta(hours=3)
    result = _recommend(_agreed(duration=long_walk), duration=long_walk)

    assert result.horizon_limited
    assert result.direction == DIRECTION_NO_DRY_WINDOW
    assert result.recommended_start is None


def test_a_walk_the_sources_do_not_reach_is_unknown() -> None:
    """No data for the walk at all means no recommendation and no search."""
    result = _recommend(_agreed(length=3))

    assert result.direction == DIRECTION_UNKNOWN
    assert result.recommended_start is None
    assert result.scheduled.verdict == VERDICT_UNKNOWN


def test_a_partly_covered_walk_still_gets_a_recommendation() -> None:
    """Forecast running out mid-walk is a reason to go earlier, not to give up."""
    result = _recommend(_agreed(length=8))

    assert result.scheduled.horizon_limited
    assert result.direction == DIRECTION_EARLIER
    assert result.recommended_start == T - SLOT


# --- candidate generation --------------------------------------------------


def test_candidates_run_outwards_from_the_walk_earlier_first() -> None:
    """Nearest first, and at every tie the earlier option comes first."""
    starts = candidate_starts(T, timedelta(minutes=20), timedelta(minutes=20))

    assert starts == (T - SLOT, T + SLOT, T - 2 * SLOT, T + 2 * SLOT)


def test_candidates_never_repeat_the_scheduled_start() -> None:
    """The scheduled window is evaluated separately; the search only offers alternatives."""
    assert T not in candidate_starts(T, EARLIER, LATER)


def test_candidates_snap_an_off_grid_walk_onto_the_grid() -> None:
    """A 07:15 walk is offered 07:10 and 07:20 — the times the forecast resolves."""
    off_grid = T + timedelta(minutes=15)
    starts = candidate_starts(off_grid, timedelta(minutes=20), timedelta(minutes=20))

    assert starts[:2] == (T + SLOT, T + 2 * SLOT)
    assert all(start.minute % 10 == 0 for start in starts)


def test_no_candidates_when_both_margins_are_zero() -> None:
    """A user who will not move the walk gets a verdict, never a suggestion."""
    assert candidate_starts(T, timedelta(0), timedelta(0)) == ()


# --- explaining the result -------------------------------------------------


def test_the_breakdown_names_each_source_verdict() -> None:
    """A lone dissenting radar is a different message from every source agreeing."""
    consensus = _consensus(
        {
            SOURCE_LIBREWXR: _pattern(*range(SLOT_COUNT)),
            SOURCE_KNMI: _pattern(),
            SOURCE_ICON_EU: _pattern(),
        }
    )
    breakdown = {item.source_id: item for item in source_breakdown(consensus, T, DURATION)}

    assert breakdown[SOURCE_LIBREWXR].verdict == VERDICT_WET
    assert breakdown[SOURCE_LIBREWXR].peak_mm_h == pytest.approx(WET_MM_H)
    assert breakdown[SOURCE_LIBREWXR].weight == pytest.approx(1.0)
    assert breakdown[SOURCE_KNMI].verdict == VERDICT_DRY
    assert breakdown[SOURCE_ICON_EU].verdict == VERDICT_DRY
    assert all(item.state == STATE_OK and item.contributed for item in breakdown.values())


def test_the_breakdown_reports_a_source_that_said_nothing() -> None:
    """A source whose horizon stops short of the walk gets no verdict rather than a dry one.

    Its cycle status stays `ok` — it did contribute to earlier slots. The window
    verdict and the source status answer two different questions, and the sensor
    shows both.
    """
    consensus = _consensus(
        {
            SOURCE_LIBREWXR: _pattern(length=3),
            SOURCE_KNMI: _pattern(),
            SOURCE_ICON_EU: _pattern(),
        }
    )
    breakdown = {item.source_id: item for item in source_breakdown(consensus, T, DURATION)}

    assert breakdown[SOURCE_LIBREWXR].verdict == VERDICT_UNKNOWN
    assert breakdown[SOURCE_LIBREWXR].state == STATE_OK
    assert breakdown[SOURCE_LIBREWXR].peak_mm_h is None
    assert breakdown[SOURCE_LIBREWXR].peak_intensity is None


def test_the_recommendation_carries_the_breakdown() -> None:
    """Everything the sensor needs to explain itself travels with the recommendation."""
    result = _recommend(_agreed())

    assert {item.source_id for item in result.sources} == set(ALL_SOURCES)
    assert result.duration_s == int(DURATION.total_seconds())


# --- material change -------------------------------------------------------


def _rec(
    direction: str,
    *,
    dry: bool,
    risk: float,
    peak: float = 0.0,
    recommended: datetime | None = None,
    duration_s: int | None = None,
) -> Recommendation:
    """A hand-built recommendation, so each material-change rule can be isolated."""
    verdict = WindowVerdict(
        start=T,
        end=T + DURATION,
        dry=dry,
        risk=risk,
        confidence=1.0,
        peak_mm_h=peak,
        covered_slots=3,
        total_slots=3,
        degraded=False,
        horizon_limited=False,
    )
    return Recommendation(
        direction=direction,
        scheduled_start=T,
        duration_s=int(DURATION.total_seconds()),
        scheduled=verdict,
        recommended_start=recommended,
        recommended_duration_s=duration_s,
    )


def test_the_first_recommendation_is_always_material() -> None:
    """There is nothing to compare against, so the user hears about it."""
    assert is_material_change(None, _rec(DIRECTION_NONE, dry=True, risk=0.0))


def test_an_unchanged_recommendation_is_not_material() -> None:
    """Repeating the same advice every ten minutes is how a useful alert becomes noise."""
    previous = _rec(DIRECTION_EARLIER, dry=False, risk=0.9, peak=1.0, recommended=T - SLOT)
    current = _rec(DIRECTION_EARLIER, dry=False, risk=0.9, peak=1.0, recommended=T - SLOT)

    assert not is_material_change(previous, current)


def test_a_changed_direction_is_material() -> None:
    """Being told to go earlier after being told to go later is worth a second buzz."""
    previous = _rec(DIRECTION_EARLIER, dry=False, risk=0.9, peak=1.0, recommended=T - SLOT)
    current = _rec(DIRECTION_LATER, dry=False, risk=0.9, peak=1.0, recommended=T + SLOT)

    assert is_material_change(previous, current)


def test_a_recommended_time_moving_twenty_minutes_is_material() -> None:
    """Two slots is the point at which the plan actually changes for the user."""
    previous = _rec(DIRECTION_EARLIER, dry=False, risk=0.9, peak=1.0, recommended=T - SLOT)
    moved = _rec(DIRECTION_EARLIER, dry=False, risk=0.9, peak=1.0, recommended=T - 3 * SLOT)
    nudged = _rec(DIRECTION_EARLIER, dry=False, risk=0.9, peak=1.0, recommended=T - 2 * SLOT)

    assert is_material_change(previous, moved)
    assert not is_material_change(previous, nudged)


def test_a_wet_verdict_relaxing_needs_to_clear_the_hysteresis_band() -> None:
    """Risk drifting back across 0.5 must reach 0.4 before the good news is announced."""
    previous = _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=0.9, peak=1.0)

    relaxed = _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=0.45, peak=1.0)
    cleared = _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=0.35, peak=1.0)

    assert not is_material_change(previous, relaxed)
    assert is_material_change(previous, cleared)


def test_a_dry_verdict_souring_needs_to_clear_the_hysteresis_band() -> None:
    """The mirror rule: 0.6 before the walk is called wet again. Backs up the direction rule."""
    previous = _rec(DIRECTION_NONE, dry=True, risk=0.2, recommended=T)

    assert not is_material_change(
        previous, _rec(DIRECTION_NONE, dry=True, risk=0.55, recommended=T)
    )
    assert is_material_change(previous, _rec(DIRECTION_NONE, dry=True, risk=0.65, recommended=T))


def test_a_change_of_intensity_class_is_material() -> None:
    """Light rain turning moderate changes what the user takes with them."""
    previous = _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=0.9, peak=0.5)

    assert is_material_change(
        previous, _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=0.9, peak=3.0)
    )
    assert not is_material_change(
        previous, _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=0.9, peak=1.2)
    )


# --- the clock ------------------------------------------------------------


def test_candidates_that_have_already_started_are_dropped() -> None:
    """A window is only a candidate while it can still be set off for."""
    starts = candidate_starts(T, EARLIER, LATER, not_before=T)

    assert starts
    assert all(start >= T for start in starts)


def test_the_search_offers_only_windows_that_have_not_started() -> None:
    """One forecast, two answers — which one depends on what time it is.

    Rain covers the whole scheduled walk and nothing else. An hour beforehand the
    nearest dry window is behind the walk; by the walk's own start that window has
    gone, and the only honest answer is the one still ahead.
    """
    consensus = _agreed(SCHEDULED_INDEX, SCHEDULED_INDEX + 1, SCHEDULED_INDEX + 2)

    ahead_of_time = _recommend(consensus, now=NOW)
    at_the_walk = _recommend(consensus, now=T)

    assert ahead_of_time.direction == DIRECTION_EARLIER
    assert ahead_of_time.recommended_start == T - 3 * SLOT
    assert at_the_walk.direction == DIRECTION_LATER
    assert at_the_walk.recommended_start == T + 3 * SLOT


def test_a_walk_with_nothing_left_ahead_of_it_has_no_dry_window() -> None:
    """Past the last candidate the search has nothing to offer, and says so."""
    consensus = _agreed(SCHEDULED_INDEX, SCHEDULED_INDEX + 1, SCHEDULED_INDEX + 2)

    assert _recommend(consensus, now=T + LATER + SLOT).direction == DIRECTION_NO_DRY_WINDOW


def test_the_recommendation_carries_the_time_the_walk_would_end() -> None:
    """Half an answer is when to set off; the other half is when you get back."""
    recommendation = _recommend(_agreed(SCHEDULED_INDEX), now=NOW)

    assert recommendation.recommended_end == recommendation.recommended_start + DURATION


# --- how far the radar reaches --------------------------------------------


def test_a_window_every_radar_covers_is_nowcast_backed() -> None:
    """All three sources here reach it, and one of them is a radar."""
    assert evaluate_window(_agreed(), T, DURATION).nowcast_covered


def test_a_window_past_the_radar_horizon_rests_on_the_models() -> None:
    """The radar stops after six slots; the models carry the rest, and it is recorded."""
    consensus = _consensus(
        {
            SOURCE_LIBREWXR: _pattern(length=6),
            SOURCE_KNMI: _pattern(),
            SOURCE_ICON_EU: _pattern(),
        }
    )

    within_reach = evaluate_window(consensus, T - 3 * SLOT, DURATION)
    beyond_reach = evaluate_window(consensus, T, DURATION)

    assert within_reach.nowcast_covered
    assert beyond_reach.dry
    assert not beyond_reach.nowcast_covered


def test_a_recommendation_no_radar_reaches_is_provisional() -> None:
    """A walk moved past the radar's hour is an early answer, not a final one.

    Rain runs from 06:00 to 07:30 and clears after it. The radar publishes six
    slots and stops at 06:50, so the only dry window — 07:30 — is one no radar has
    looked at yet: exactly the "wait until 14:00, decided at 12:00" case.
    """
    models = _pattern(*range(SCHEDULED_INDEX + 3))
    consensus = _consensus(
        {
            SOURCE_LIBREWXR: _pattern(length=6),
            SOURCE_KNMI: list(models),
            SOURCE_ICON_EU: list(models),
        }
    )

    recommendation = _recommend(consensus, now=NOW)

    assert recommendation.direction == DIRECTION_LATER
    assert recommendation.recommended_start == T + 3 * SLOT
    assert recommendation.provisional


def test_a_recommendation_the_radar_covers_is_not_provisional() -> None:
    """The counterpart: when a radar reaches the suggested window, nothing is hedged."""
    recommendation = _recommend(_agreed(SCHEDULED_INDEX), now=NOW)

    assert recommendation.direction == DIRECTION_LATER
    assert recommendation.recommended_start == T + SLOT
    assert not recommendation.provisional


# --- advice that has run out of time --------------------------------------


def test_advice_expires_when_the_time_it_names_does() -> None:
    """A suggestion to set off at 07:10 stops being advice at 07:11."""
    recommendation = _rec(DIRECTION_LATER, dry=False, risk=1.0, recommended=T + SLOT)

    assert is_actionable(recommendation, T)
    assert is_actionable(recommendation, T + SLOT)
    assert not is_actionable(recommendation, T + SLOT + timedelta(minutes=1))


def test_no_dry_window_expires_when_the_walk_begins() -> None:
    """It has no time of its own to expire, so the walk's own start is the deadline."""
    recommendation = _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=1.0)

    assert is_actionable(recommendation, T - SLOT)
    assert not is_actionable(recommendation, T)


def test_a_direction_that_changed_only_because_time_passed_is_not_news() -> None:
    """The user declined "go at 06:30"; that it is now 07:00 is not a new forecast."""
    previous = _rec(DIRECTION_EARLIER, dry=False, risk=1.0, recommended=T - 3 * SLOT)
    current = _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=1.0)

    assert is_material_change(previous, current)
    assert superseded_by_the_clock(previous, current, T)


def test_a_window_souring_while_it_is_still_ahead_is_news() -> None:
    """The mirror case: the suggestion has not passed, so this really is a change."""
    previous = _rec(DIRECTION_LATER, dry=False, risk=1.0, recommended=T + 3 * SLOT)
    current = _rec(DIRECTION_NO_DRY_WINDOW, dry=False, risk=1.0)

    assert not superseded_by_the_clock(previous, current, T)


# --- shortening the walk to fit a shorter dry window -----------------------

#: The walk of the acceptance criterion: 20 minutes, so a single dry slot is the
#: only thing that can be offered and the arithmetic stays readable.
SHORT_WALK = timedelta(minutes=20)
TEN = timedelta(minutes=10)
TWENTY = timedelta(minutes=20)

#: Every slot in the search range except 07:10 — no two consecutive slots are dry,
#: so nothing of the full length fits anywhere in the margins.
ONE_GAP = tuple(index for index in range(SLOT_COUNT) if index != SCHEDULED_INDEX + 1)

#: The same, with the one dry slot at the scheduled start itself.
GAP_AT_THE_START = tuple(index for index in range(SLOT_COUNT) if index != SCHEDULED_INDEX)


def test_a_ten_minute_clearing_is_offered_when_the_minimum_allows_it() -> None:
    """The phase's whole point: a shorter walk in the dry is a real answer."""
    result = _recommend(_agreed(*ONE_GAP), duration=SHORT_WALK, minimum=TEN)

    assert result.direction == DIRECTION_SHORTER
    assert result.recommended_start == T + SLOT
    assert result.recommended_duration_s == 600
    assert result.recommended_end == T + 2 * SLOT
    assert result.duration_s == int(SHORT_WALK.total_seconds())


def test_the_same_clearing_is_no_dry_window_with_shortening_off() -> None:
    """Switched off, the engine answers exactly as it did before phase 10."""
    result = _recommend(_agreed(*ONE_GAP), duration=SHORT_WALK)

    assert result.direction == DIRECTION_NO_DRY_WINDOW
    assert result.recommended_start is None
    assert result.recommended_duration_s is None


def test_a_full_length_window_always_beats_a_shortened_one() -> None:
    """The dog would rather walk its whole half-hour later than half of it now."""
    result = _recommend(_agreed(0, 1, 2, 4, 5, 6, 7, 8), minimum=TEN)

    assert result.direction == DIRECTION_LATER
    assert result.recommended_start == T + 3 * SLOT
    assert result.recommended_duration_s is None


def test_a_clearing_shorter_than_the_minimum_is_refused() -> None:
    """Ten dry minutes is not an answer for someone who said twenty is the least."""
    result = _recommend(_agreed(*ONE_GAP), minimum=TWENTY)

    assert result.direction == DIRECTION_NO_DRY_WINDOW


def test_the_longest_shortened_window_wins_over_the_nearest() -> None:
    """Twenty dry minutes half an hour away beats ten dry minutes right now."""
    wet = tuple(index for index in range(SLOT_COUNT) if index not in {6, 9, 10})

    result = _recommend(_agreed(*wet), minimum=TEN)

    assert result.direction == DIRECTION_SHORTER
    assert result.recommended_start == T + 3 * SLOT
    assert result.recommended_duration_s == 1200


def test_a_shortened_walk_may_start_at_the_scheduled_time() -> None:
    """ "Go now, come back sooner" is the most ordinary form this advice takes."""
    result = _recommend(_agreed(*GAP_AT_THE_START), minimum=TEN)

    assert result.direction == DIRECTION_SHORTER
    assert result.recommended_start == T
    assert result.shift == timedelta(0)
    assert result.recommended_duration_s == 600


def test_a_gap_only_the_models_see_is_not_a_gap() -> None:
    """Ten minutes of clearing inside an hourly forecast is rounding, not weather."""
    values = _pattern(*ONE_GAP)
    models = _consensus(
        {SOURCE_KNMI: list(values), SOURCE_ICON_EU: list(values)}, duration=SHORT_WALK
    )

    result = _recommend(models, duration=SHORT_WALK, minimum=TEN)

    assert result.direction == DIRECTION_NO_DRY_WINDOW


def test_a_shortened_recommendation_is_never_provisional() -> None:
    """It is radar-backed by construction, so there is nothing left to confirm."""
    result = _recommend(_agreed(*ONE_GAP), duration=SHORT_WALK, minimum=TEN)

    assert result.direction == DIRECTION_SHORTER
    assert not result.provisional


def test_a_shortened_start_that_has_passed_is_not_offered() -> None:
    """The clock bounds this search too — advice for 07:00 is useless at 07:05."""
    result = _recommend(_agreed(*GAP_AT_THE_START), minimum=TEN, now=T + timedelta(minutes=5))

    assert result.direction == DIRECTION_NO_DRY_WINDOW


@pytest.mark.parametrize(
    ("duration", "minimum", "expected"),
    [
        # Whole slots only, and strictly shorter than the walk itself.
        (timedelta(minutes=30), TEN, (TWENTY, TEN)),
        # A 25-minute walk is offered 20 or 10; a "15" would score the same slots
        # as the 20 and promise a precision the grid does not have.
        (timedelta(minutes=25), TEN, (TWENTY, TEN)),
        # One slot long already: there is nothing shorter to offer.
        (TEN, TEN, ()),
        # A minimum between slots rounds up — 15 minutes means "at least 15".
        (timedelta(minutes=30), timedelta(minutes=15), (TWENTY,)),
        # Off.
        (timedelta(minutes=30), None, ()),
    ],
)
def test_shortened_lengths_step_by_whole_slots(
    duration: timedelta, minimum: timedelta | None, expected: tuple[timedelta, ...]
) -> None:
    """The grid decides which lengths are distinguishable, so it decides the offers."""
    assert shorter_durations(Search(duration, EARLIER, LATER, min_duration=minimum)) == expected


def test_a_changed_walk_length_is_a_material_change() -> None:
    """Twenty dry minutes cut to ten is different advice with every time unchanged."""
    previous = _rec(DIRECTION_SHORTER, dry=False, risk=0.9, recommended=T, duration_s=1200)
    same = _rec(DIRECTION_SHORTER, dry=False, risk=0.9, recommended=T, duration_s=1200)
    shorter = _rec(DIRECTION_SHORTER, dry=False, risk=0.9, recommended=T, duration_s=600)

    assert not is_material_change(previous, same)
    assert is_material_change(previous, shorter)
