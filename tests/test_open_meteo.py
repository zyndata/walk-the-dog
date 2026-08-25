"""Open-Meteo adapter: two models from one request, cadence, budget, failures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.walk_the_dog.const import (
    INTENSITY_NONE,
    INTENSITY_THRESHOLD_HEAVY,
    INTENSITY_THRESHOLD_LIGHT,
    INTENSITY_THRESHOLD_MODERATE,
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    intensity_class,
)
from custom_components.walk_the_dog.sources.base import (
    STATE_FAILED,
    STATE_OK,
    SampleGeometry,
)
from custom_components.walk_the_dog.sources.open_meteo import (
    MIN_INTERVAL_S,
    MODEL_IDS,
    STEP_S,
    URL,
    OpenMeteoAdapter,
    parse_forecast,
)

from .conftest import load_fixture

UA = "walk_the_dog/test (+https://github.com/zyndata/walk-the-dog)"
NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


def test_model_ids_cover_both_recommended_models() -> None:
    """One request, both model families (docs/DATA_SOURCES.md § Ranked recommendation)."""
    assert MODEL_IDS == {
        SOURCE_ICON_EU: "icon_eu",
        SOURCE_KNMI: "knmi_harmonie_arome_europe",
    }


def test_parse_yields_one_series_per_model() -> None:
    """The five-coordinate response collapses into exactly two normalized series."""
    series = parse_forecast(load_fixture("open_meteo", "dry.json"), NOW)

    assert {s.source_id for s in series} == {SOURCE_ICON_EU, SOURCE_KNMI}
    for entry in series:
        assert entry.step_s == STEP_S
        assert len(entry.slots) == 12
        assert entry.slots == tuple(sorted(entry.slots))
        assert all(value == 0.0 for _, value in entry.slots)
        assert entry.fetched_at == NOW


def test_parse_takes_the_max_across_the_five_sample_points() -> None:
    """A smooth NWP field over five points: the conservative reading is the max.

    In the recorded heavy fixture KNMI puts 7.3 mm at the centre, 4.2 / 3.6 / 1.8 / 0.1
    at the edge points; the disc must report 7.3.
    """
    series = {s.source_id: s for s in parse_forecast(load_fixture("open_meteo", "heavy.json"), NOW)}

    knmi_peak = max(value for _, value in series[SOURCE_KNMI].slots)
    assert knmi_peak == pytest.approx(7.3)
    # 7.3 mm/h sits just under the 7.6 mm/h heavy boundary.
    assert intensity_class(knmi_peak) == INTENSITY_THRESHOLD_MODERATE
    assert intensity_class(7.6) == INTENSITY_THRESHOLD_HEAVY
    # ICON-EU disagrees completely — exactly the disagreement consensus exists for.
    assert max(value for _, value in series[SOURCE_ICON_EU].slots) == 0.0
    assert intensity_class(0.0) == INTENSITY_NONE


def test_hourly_millimetres_are_millimetres_per_hour() -> None:
    """On an hourly step, accumulation over the step equals the rate (no scaling)."""
    series = {s.source_id: s for s in parse_forecast(load_fixture("open_meteo", "wet.json"), NOW)}
    values = [value for _, value in series[SOURCE_KNMI].slots]

    assert max(values) == pytest.approx(0.2)
    assert intensity_class(max(values)) == INTENSITY_THRESHOLD_LIGHT


def test_a_model_missing_from_the_response_yields_no_series_for_it() -> None:
    """A partial response must never let the absent model read as "no rain"."""
    series = parse_forecast(
        {
            "latitude": 52.25,
            "longitude": 21.0,
            "hourly": {
                "time": [1787641200, 1787644800],
                "precipitation_icon_eu": [0.3, 0.0],
            },
        },
        NOW,
    )
    assert len(series) == 1
    assert series[0].source_id == SOURCE_ICON_EU
    assert series[0].slots[0][1] == pytest.approx(0.3)


async def test_a_missing_model_is_reported_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """One model out means one source down, not a fabricated dry forecast."""
    aioclient_mock.get(
        URL,
        json=[
            {
                "latitude": 52.25,
                "longitude": 21.0,
                "hourly": {"time": [1787641200], "precipitation_icon_eu": [0.0]},
            }
        ],
    )

    result = await OpenMeteoAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)

    assert not result.ok
    assert {s.source_id for s in result.series} == {SOURCE_ICON_EU}
    knmi = next(s for s in result.statuses if s.source_id == SOURCE_KNMI)
    assert knmi.state == STATE_FAILED
    assert not knmi.contributed


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hourly": {}},
        {"hourly": {"time": [1787641200]}},  # no precipitation at all
        [],
        "not json",
    ],
)
def test_parse_returns_nothing_for_unusable_payloads(payload: object) -> None:
    """A response we cannot read yields no series rather than a fabricated one."""
    assert parse_forecast(payload, NOW) == []


def test_parse_skips_null_values_and_clamps_negatives() -> None:
    """Missing hours are skipped; a negative accumulation is treated as zero."""
    series = parse_forecast(
        {
            "hourly": {
                "time": [1787641200, 1787644800, 1787648400],
                "precipitation_icon_eu": [None, -0.1, 1.2],
            }
        },
        NOW,
    )
    values = [value for _, value in series[0].slots]
    assert values == [0.0, pytest.approx(1.2)]


# --- fetching --------------------------------------------------------------------


async def test_fetch_makes_one_request_for_both_models(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """Five coordinates and two models cost a single HTTP request."""
    aioclient_mock.get(URL, json=load_fixture("open_meteo", "dry.json"))
    adapter = OpenMeteoAdapter(UA)

    result = await adapter.fetch(async_get_clientsession(hass), geometry, now)

    assert len(aioclient_mock.mock_calls) == 1
    assert result.ok
    assert {s.source_id for s in result.series} == {SOURCE_ICON_EU, SOURCE_KNMI}
    assert all(s.state == STATE_OK and s.contributed for s in result.statuses)


async def test_fetch_asks_for_five_coordinates_and_the_hourly_series(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """The request shape is the one docs/ARCHITECTURE.md budgets for."""
    aioclient_mock.get(URL, json=load_fixture("open_meteo", "dry.json"))

    await OpenMeteoAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)

    query = aioclient_mock.mock_calls[0][1].query
    assert len(query["latitude"].split(",")) == 5
    assert len(query["longitude"].split(",")) == 5
    assert query["hourly"] == "precipitation"
    assert query["models"] == "icon_eu,knmi_harmonie_arome_europe"
    assert query["timeformat"] == "unixtime"
    # The interpolated 15-minutely series is never requested — it is lossy for Poland.
    assert "minutely_15" not in query


async def test_open_meteo_is_fetched_every_thirty_minutes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """Its freshest model re-runs hourly, so a 10-minute cadence would be wasted."""
    aioclient_mock.get(URL, json=load_fixture("open_meteo", "dry.json"))
    adapter = OpenMeteoAdapter(UA)

    assert adapter.should_fetch(now)
    await adapter.fetch(async_get_clientsession(hass), geometry, now)

    assert not adapter.should_fetch(now + timedelta(minutes=10))
    assert not adapter.should_fetch(now + timedelta(minutes=20))
    assert adapter.should_fetch(now + timedelta(seconds=MIN_INTERVAL_S))


async def test_skipped_cycles_reuse_the_cached_series(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """A skipped fetch still gives the engine a full picture, with a growing age."""
    aioclient_mock.get(URL, json=load_fixture("open_meteo", "dry.json"))
    adapter = OpenMeteoAdapter(UA)
    await adapter.fetch(async_get_clientsession(hass), geometry, now)

    cached = adapter.cached(now + timedelta(minutes=20))

    assert len(aioclient_mock.mock_calls) == 1
    assert len(cached.series) == 2
    assert all(s.contributed for s in cached.statuses)
    assert all(s.age_s == 1200 for s in cached.statuses)


async def test_stays_inside_the_hourly_request_budget(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """At the designed cadence an active hour costs two requests, well under the cap."""
    aioclient_mock.get(URL, json=load_fixture("open_meteo", "dry.json"))
    adapter = OpenMeteoAdapter(UA)
    session = async_get_clientsession(hass)

    for minute in range(0, 60, 10):  # six cycles, one active hour
        moment = now + timedelta(minutes=minute)
        if adapter.should_fetch(moment):
            await adapter.fetch(session, geometry, moment)

    assert len(aioclient_mock.mock_calls) == 2


@pytest.mark.parametrize(
    "kwargs",
    [{"status": 500}, {"status": 429}, {"exc": TimeoutError()}, {"text": "<html>nope</html>"}],
)
async def test_failures_mark_both_sources_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
    kwargs: dict,
) -> None:
    """A provider failure takes both its models down together — that is what triggers failover."""
    aioclient_mock.get(URL, **kwargs)

    result = await OpenMeteoAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)

    assert not result.ok
    assert {s.source_id for s in result.statuses} == {SOURCE_ICON_EU, SOURCE_KNMI}
    assert all(s.state == STATE_FAILED for s in result.statuses)


async def test_a_failure_reuses_the_last_series_until_it_goes_stale(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """KNMI is hourly, so its cached series survives one bad cycle but not three hours."""
    aioclient_mock.get(URL, json=load_fixture("open_meteo", "dry.json"))
    adapter = OpenMeteoAdapter(UA)
    session = async_get_clientsession(hass)
    await adapter.fetch(session, geometry, now)

    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, status=503)

    soon = await adapter.fetch(session, geometry, now + timedelta(minutes=30))
    assert soon.series
    knmi = next(s for s in soon.statuses if s.source_id == SOURCE_KNMI)
    assert knmi.contributed

    later = adapter.cached(now + timedelta(hours=4))
    knmi_later = next(s for s in later.statuses if s.source_id == SOURCE_KNMI)
    assert not knmi_later.contributed
