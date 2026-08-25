"""The shared adapter plumbing: geometry, budgets, backoff, staleness."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.walk_the_dog.const import (
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
    SOURCE_METNO,
)
from custom_components.walk_the_dog.sources.base import (
    ATTRIBUTION,
    CELL_KM,
    RELIABILITY,
    STALE_FACTOR,
    STATE_OK,
    STATE_STALE,
    UPDATE_INTERVAL_S,
    Backoff,
    FetchResult,
    RequestBudget,
    SampleGeometry,
    SourceSeries,
    SourceStatus,
    restate,
)

NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
ALL_SOURCES = (SOURCE_LIBREWXR, SOURCE_KNMI, SOURCE_ICON_EU, SOURCE_METNO)


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    radius = 6371.0088
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(h))


@pytest.mark.parametrize("source_id", ALL_SOURCES)
def test_every_source_has_complete_metadata(source_id: str) -> None:
    """Weight, cadence, cell size and attribution exist for each recommended source."""
    assert 0.0 < RELIABILITY[source_id] <= 1.0
    assert UPDATE_INTERVAL_S[source_id] > 0
    assert CELL_KM[source_id] > 0
    assert ATTRIBUTION[source_id]


def test_reliability_matches_the_architecture_table() -> None:
    """docs/ARCHITECTURE.md § Consensus scoring fixes these weights."""
    assert RELIABILITY == {
        SOURCE_LIBREWXR: 1.00,
        SOURCE_KNMI: 0.90,
        SOURCE_ICON_EU: 0.80,
        SOURCE_METNO: 0.70,
    }


def test_sample_points_are_the_centre_plus_four_edge_points() -> None:
    """Five coordinates: centre, then N/E/S/W exactly one radius away."""
    geometry = SampleGeometry(52.2297, 21.0122, 5.0)
    points = geometry.sample_points()

    assert len(points) == 5
    assert points[0] == (52.2297, 21.0122)
    for edge in points[1:]:
        assert _haversine_km(points[0], edge) == pytest.approx(5.0, abs=0.02)

    north, east, south, west = points[1:]
    assert north[0] > points[0][0] and south[0] < points[0][0]
    assert east[1] > points[0][1] and west[1] < points[0][1]


def test_geometry_key_changes_with_location_and_radius() -> None:
    """The cache invalidation key must distinguish every geometry change."""
    base = SampleGeometry(52.2297, 21.0122, 5.0)
    assert base.key == SampleGeometry(52.2297, 21.0122, 5.0).key
    assert base.key != SampleGeometry(52.2298, 21.0122, 5.0).key
    assert base.key != SampleGeometry(52.2297, 21.0122, 6.0).key


def _series(source_id: str, issued_at: datetime) -> SourceSeries:
    return SourceSeries(
        source_id=source_id,
        issued_at=issued_at,
        fetched_at=issued_at,
        step_s=3600,
        slots=((issued_at, 0.0),),
        cell_km=CELL_KM[source_id],
        reliability=RELIABILITY[source_id],
    )


@pytest.mark.parametrize("source_id", ALL_SOURCES)
def test_series_goes_stale_at_three_update_intervals(source_id: str) -> None:
    """docs/DATA_SOURCES.md § Fallback strategy: stale beyond 3x the interval."""
    interval = UPDATE_INTERVAL_S[source_id]
    fresh = _series(source_id, NOW - timedelta(seconds=STALE_FACTOR * interval))
    stale = _series(source_id, NOW - timedelta(seconds=STALE_FACTOR * interval + 1))

    assert not fresh.is_stale(NOW)
    assert stale.is_stale(NOW)


def test_horizon_end_is_the_end_of_the_last_slot() -> None:
    """A ten-minute frame covers the ten minutes after its timestamp."""
    series = SourceSeries(
        source_id=SOURCE_LIBREWXR,
        issued_at=NOW,
        fetched_at=NOW,
        step_s=600,
        slots=((NOW, 0.0), (NOW + timedelta(minutes=10), 1.0)),
        cell_km=2.0,
        reliability=1.0,
    )
    assert series.horizon_end == NOW + timedelta(minutes=20)


def test_restate_drops_a_series_that_aged_into_staleness() -> None:
    """Cached data is never re-presented as fresh — its age keeps running."""
    issued = NOW - timedelta(minutes=5)
    result = FetchResult(
        series=(_series(SOURCE_LIBREWXR, issued),),
        statuses=(SourceStatus(SOURCE_LIBREWXR, STATE_OK, age_s=300, contributed=True),),
    )

    still_fresh = restate(result, NOW)
    assert still_fresh.statuses[0].state == STATE_OK
    assert still_fresh.statuses[0].contributed

    much_later = restate(result, NOW + timedelta(hours=1))
    assert much_later.statuses[0].state == STATE_STALE
    assert not much_later.statuses[0].contributed
    assert much_later.statuses[0].age_s == 3900


def test_fetch_result_ok_requires_every_source_to_be_ok() -> None:
    """Open-Meteo speaks for two sources; one failure means the adapter failed."""
    ok = SourceStatus(SOURCE_ICON_EU, STATE_OK, contributed=True)
    stale = SourceStatus(SOURCE_KNMI, STATE_STALE)

    assert FetchResult(statuses=(ok,)).ok
    assert not FetchResult(statuses=(ok, stale)).ok
    assert not FetchResult().ok


def test_request_budget_caps_a_rolling_hour() -> None:
    """The budget in docs/DATA_SOURCES.md is per active hour, so it rolls."""
    budget = RequestBudget(limit=3)
    start = NOW

    assert all(budget.consume(start) for _ in range(3))
    assert budget.remaining(start) == 0
    assert not budget.consume(start)

    # Still inside the hour: no new allowance.
    assert not budget.consume(start + timedelta(minutes=59))
    # The first three requests have now aged out.
    assert budget.consume(start + timedelta(minutes=61))


def test_backoff_spaces_retries_across_cycles_and_resets_on_success() -> None:
    """1, 2 then 4 minutes between attempts — applied between cycles, never as sleeps."""
    backoff = Backoff()
    assert backoff.ready(NOW)

    backoff.record_failure(NOW)
    assert not backoff.ready(NOW + timedelta(seconds=59))
    assert backoff.ready(NOW + timedelta(seconds=60))

    backoff.record_failure(NOW)
    assert backoff.next_attempt_at == NOW + timedelta(seconds=120)
    backoff.record_failure(NOW)
    assert backoff.next_attempt_at == NOW + timedelta(seconds=240)
    # Capped at the longest delay rather than growing without bound.
    backoff.record_failure(NOW)
    assert backoff.next_attempt_at == NOW + timedelta(seconds=240)

    backoff.record_success()
    assert backoff.ready(NOW)
    assert backoff.failures == 0
