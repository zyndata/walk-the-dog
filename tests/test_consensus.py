"""The weighted vote: risk, confidence, freshness decay and who gets to vote at all.

These are the numbers the whole integration argues from, so they are asserted
against the formulas in docs/ARCHITECTURE.md § Consensus scoring by hand — not
against whatever the implementation happens to produce.
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
    SOURCE_METNO,
)
from custom_components.walk_the_dog.engine.consensus import (
    MIN_FRESHNESS,
    build_consensus,
    freshness,
    source_weight,
)
from custom_components.walk_the_dog.engine.grid import SLOT, slots_between
from custom_components.walk_the_dog.sources.base import (
    STATE_FAILED,
    STATE_OK,
    STATE_OUT_OF_RANGE,
    STATE_STALE,
    UPDATE_INTERVAL_S,
)

from .conftest import make_series, make_status

T = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
SLOTS = slots_between(T, T + 3 * SLOT)

#: Reliability weights, repeated here so a change to the table breaks a test.
W_LIBREWXR = 1.00
W_KNMI = 0.90
W_ICON = 0.80


def _consensus(series, statuses=None, *, threshold=INTENSITY_THRESHOLD_LIGHT, now=T, slots=SLOTS):
    """Score `slots` from the given series, defaulting every source's status to ok."""
    if statuses is None:
        statuses = [make_status(item.source_id) for item in series]
    return build_consensus(series, statuses, slots=slots, threshold=threshold, now=now)


def _radar(values, **kwargs):
    return make_series(SOURCE_LIBREWXR, values, start=T, **kwargs)


def _hourly(source_id, value, **kwargs):
    return make_series(source_id, [value], start=T, **kwargs)


# --- freshness ------------------------------------------------------------


@pytest.mark.parametrize("source_id", [SOURCE_LIBREWXR, SOURCE_KNMI, SOURCE_ICON_EU, SOURCE_METNO])
def test_freshness_is_full_until_the_publication_interval(source_id: str) -> None:
    """Data is not penalised for being as old as the source's own cadence."""
    assert freshness(source_id, 0) == 1.0
    assert freshness(source_id, UPDATE_INTERVAL_S[source_id]) == 1.0


def test_freshness_decays_linearly_to_the_floor() -> None:
    """Halfway to the stale edge the weight is halfway to 0.5."""
    interval = UPDATE_INTERVAL_S[SOURCE_LIBREWXR]

    assert freshness(SOURCE_LIBREWXR, 2 * interval) == pytest.approx(0.75)
    assert freshness(SOURCE_LIBREWXR, 3 * interval) == pytest.approx(MIN_FRESHNESS)


def test_freshness_is_zero_past_the_stale_edge() -> None:
    """Stale data is dropped, never merely down-weighted (docs/DATA_SOURCES.md)."""
    assert freshness(SOURCE_LIBREWXR, 3 * UPDATE_INTERVAL_S[SOURCE_LIBREWXR] + 1) == 0.0


def test_source_weight_is_reliability_times_freshness() -> None:
    """`w_i` is exactly the product the architecture defines."""
    series = _radar([0.0], issued_at=T - timedelta(minutes=20))

    assert source_weight(series, T) == pytest.approx(W_LIBREWXR * 0.75)


# --- the vote -------------------------------------------------------------


def test_all_dry_is_risk_zero_at_full_confidence() -> None:
    """Three sources agreeing on a dry walk is the strongest statement possible."""
    consensus = _consensus(
        [_radar([0.0, 0.0, 0.0]), _hourly(SOURCE_KNMI, 0.0), _hourly(SOURCE_ICON_EU, 0.0)]
    )

    for slot in SLOTS:
        score = consensus.at(slot)
        assert score.risk == 0.0
        assert score.confidence == pytest.approx(1.0)
        assert not score.wet
        assert score.n_sources == 3


def test_all_wet_is_risk_one_at_full_confidence() -> None:
    """Unanimity is just as certain in the other direction."""
    consensus = _consensus(
        [_radar([1.2, 1.2, 1.2]), _hourly(SOURCE_KNMI, 0.8), _hourly(SOURCE_ICON_EU, 2.0)]
    )

    score = consensus.at(T)
    assert score.risk == pytest.approx(1.0)
    assert score.confidence == pytest.approx(1.0)
    assert score.wet


def test_disagreement_is_a_weighted_fraction_not_a_head_count() -> None:
    """Radar alone predicting rain loses to both models: 1.0 of 2.7 is under half."""
    consensus = _consensus(
        [_radar([1.5, 1.5, 1.5]), _hourly(SOURCE_KNMI, 0.0), _hourly(SOURCE_ICON_EU, 0.0)]
    )

    score = consensus.at(T)
    expected = W_LIBREWXR / (W_LIBREWXR + W_KNMI + W_ICON)
    assert score.risk == pytest.approx(expected)
    assert not score.wet
    assert score.confidence == pytest.approx(abs(2 * expected - 1))


def test_the_two_models_outvote_the_radar() -> None:
    """1.7 of 2.7 is a majority, so the walk counts as wet even if radar says dry."""
    consensus = _consensus(
        [_radar([0.0, 0.0, 0.0]), _hourly(SOURCE_KNMI, 1.1), _hourly(SOURCE_ICON_EU, 0.9)]
    )

    score = consensus.at(T)
    assert score.risk == pytest.approx((W_KNMI + W_ICON) / (W_LIBREWXR + W_KNMI + W_ICON))
    assert score.wet


def test_threshold_decides_what_counts_as_a_wet_vote() -> None:
    """The same forecast is dry at the moderate threshold and wet at the light one."""
    series = [_radar([1.0, 1.0, 1.0]), _hourly(SOURCE_KNMI, 1.0), _hourly(SOURCE_ICON_EU, 1.0)]

    assert _consensus(series, threshold=INTENSITY_THRESHOLD_LIGHT).at(T).wet
    assert not _consensus(series, threshold=INTENSITY_THRESHOLD_MODERATE).at(T).wet


def test_expected_intensity_is_the_weighted_mean() -> None:
    """Displayed intensity averages what the sources say; it never drives the vote."""
    consensus = _consensus(
        [_radar([6.0, 6.0, 6.0]), _hourly(SOURCE_KNMI, 1.0), _hourly(SOURCE_ICON_EU, 1.0)]
    )

    score = consensus.at(T)
    expected = (W_LIBREWXR * 6.0 + W_KNMI + W_ICON) / (W_LIBREWXR + W_KNMI + W_ICON)
    assert score.intensity_mm_h == pytest.approx(expected)
    assert score.intensity == INTENSITY_THRESHOLD_MODERATE


# --- degradation ----------------------------------------------------------


def test_two_sources_cap_confidence_at_zero_point_eight() -> None:
    """Losing a source costs certainty even when the survivors agree completely."""
    consensus = _consensus([_radar([0.0, 0.0, 0.0]), _hourly(SOURCE_KNMI, 0.0)])

    score = consensus.at(T)
    assert score.n_sources == 2
    assert score.confidence == pytest.approx(0.8)


def test_a_single_source_can_never_be_reported_as_certain() -> None:
    """The minimum viable source count is 1 — with confidence capped at 0.5 (phase 0)."""
    consensus = _consensus([_radar([0.0, 0.0, 0.0])])

    score = consensus.at(T)
    assert score.n_sources == 1
    assert score.confidence == pytest.approx(0.5)


def test_a_slot_no_source_reaches_has_no_data() -> None:
    """An uncovered slot is unknown, not dry: zero risk here means nothing."""
    consensus = _consensus([_radar([0.0])])

    assert consensus.at(T).has_data
    assert not consensus.at(T + SLOT).has_data
    assert consensus.at(T + SLOT).confidence == 0.0


def test_a_stale_source_is_dropped_from_the_vote() -> None:
    """A source that stopped publishing stops voting instead of freezing its opinion."""
    stale_at = T - timedelta(seconds=3 * UPDATE_INTERVAL_S[SOURCE_LIBREWXR] + 60)
    consensus = _consensus(
        [
            _radar([9.0, 9.0, 9.0], issued_at=stale_at),
            _hourly(SOURCE_KNMI, 0.0),
            _hourly(SOURCE_ICON_EU, 0.0),
        ]
    )

    score = consensus.at(T)
    assert score.contributors == (SOURCE_ICON_EU, SOURCE_KNMI)
    assert score.risk == 0.0
    assert _status(consensus, SOURCE_LIBREWXR).state == STATE_STALE
    assert not _status(consensus, SOURCE_LIBREWXR).contributed


def test_an_ageing_source_still_votes_but_weighs_less() -> None:
    """Between the interval and the stale edge, influence fades rather than switching off."""
    consensus = _consensus(
        [
            _radar([2.0, 2.0, 2.0], issued_at=T - timedelta(minutes=20)),
            _hourly(SOURCE_KNMI, 0.0),
        ]
    )

    aged = W_LIBREWXR * 0.75
    assert consensus.weights[SOURCE_LIBREWXR] == pytest.approx(aged)
    assert consensus.at(T).risk == pytest.approx(aged / (aged + W_KNMI))


# --- statuses -------------------------------------------------------------


def test_a_fresh_source_beyond_its_horizon_is_out_of_range_not_stale() -> None:
    """Being silent about a distant slot is not the same failure as being old."""
    late = slots_between(T + timedelta(hours=2), T + timedelta(hours=2) + SLOT)
    covering = make_series(SOURCE_KNMI, [0.0, 0.0, 0.0], start=T)
    consensus = _consensus([_radar([0.0]), covering], slots=late)

    assert _status(consensus, SOURCE_LIBREWXR).state == STATE_OUT_OF_RANGE
    assert not _status(consensus, SOURCE_LIBREWXR).contributed
    assert _status(consensus, SOURCE_KNMI).state == STATE_OK


def test_a_failed_adapter_keeps_its_reported_status() -> None:
    """The engine never rewrites why a provider was unreachable."""
    consensus = _consensus(
        [_radar([0.0, 0.0, 0.0])],
        statuses=[
            make_status(SOURCE_LIBREWXR),
            make_status(SOURCE_KNMI, STATE_FAILED, detail="timeout"),
        ],
    )

    failed = _status(consensus, SOURCE_KNMI)
    assert failed.state == STATE_FAILED
    assert failed.detail == "timeout"
    assert not failed.contributed
    assert consensus.contributing == (SOURCE_LIBREWXR,)


def test_status_age_is_measured_against_the_passed_clock() -> None:
    """`now` is a parameter everywhere — the engine never reads a clock of its own."""
    consensus = _consensus([_radar([0.0, 0.0, 0.0], issued_at=T - timedelta(minutes=7))])

    assert _status(consensus, SOURCE_LIBREWXR).age_s == 7 * 60


def test_no_sources_at_all_scores_nothing() -> None:
    """Zero contributors is a real state: every slot is unknown, none is dry."""
    consensus = _consensus([])

    assert consensus.contributing == ()
    assert all(not consensus.at(slot).has_data for slot in SLOTS)


def _status(consensus, source_id):
    """The refined status the engine assigned to one source."""
    return next(status for status in consensus.statuses if status.source_id == source_id)
