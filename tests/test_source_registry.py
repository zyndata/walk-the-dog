"""The registry: one cycle across all adapters, and provider-level failover."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.walk_the_dog.const import (
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
    SOURCE_METNO,
)
from custom_components.walk_the_dog.sources import (
    FAILOVER_THRESHOLD,
    SourceRegistry,
    build_user_agent,
    met_norway,
    open_meteo,
)
from custom_components.walk_the_dog.sources.base import (
    STATE_DISABLED,
    SampleGeometry,
)
from custom_components.walk_the_dog.sources.librewxr import (
    BASE_URL,
    WEATHER_MAPS_PATH,
    DiscMask,
    parse_weather_maps,
)

from .conftest import load_bytes, load_fixture
from .test_librewxr import _tile_urls

INDEX_URL = BASE_URL + WEATHER_MAPS_PATH
UA = build_user_agent("0.1.0")


def _mock_librewxr(aioclient_mock: AiohttpClientMocker, geometry: SampleGeometry) -> None:
    index = load_fixture("librewxr", "weather-maps.json")
    aioclient_mock.get(INDEX_URL, json=index)
    mask = DiscMask(geometry)
    host, frames = parse_weather_maps(index)
    tile = load_bytes("librewxr", "tile_wet.png")
    for _, path in frames:
        for url in _tile_urls(host, path, mask):
            aioclient_mock.get(url, content=tile)


def test_user_agent_identifies_the_project_without_personal_details() -> None:
    """MET Norway requires contact info; the project URL is what we give it."""
    agent = build_user_agent("1.2.3")
    assert agent.startswith("walk_the_dog/1.2.3")
    assert "github.com/zyndata/walk-the-dog" in agent
    assert "@" not in agent


async def test_a_healthy_cycle_returns_three_series_and_keeps_metno_silent(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """The recommended trio votes; the correlated failover source stays dormant."""
    _mock_librewxr(aioclient_mock, geometry)
    aioclient_mock.get(open_meteo.URL, json=load_fixture("open_meteo", "dry.json"))
    aioclient_mock.get(met_norway.URL, json=load_fixture("met_norway", "compact.json"))
    registry = SourceRegistry(UA)

    series, statuses = await registry.async_fetch(async_get_clientsession(hass), geometry, now)

    assert {s.source_id for s in series} == {SOURCE_LIBREWXR, SOURCE_ICON_EU, SOURCE_KNMI}
    metno = next(s for s in statuses if s.source_id == SOURCE_METNO)
    assert metno.state == STATE_DISABLED
    assert not metno.contributed
    assert not registry.failover_active
    # Not one request went to MET Norway.
    assert all(met_norway.URL not in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_metno_wakes_after_two_consecutive_open_meteo_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """docs/DATA_SOURCES.md § Fallback strategy: two failures in a row, then failover."""
    _mock_librewxr(aioclient_mock, geometry)
    aioclient_mock.get(open_meteo.URL, status=500)
    aioclient_mock.get(met_norway.URL, json=load_fixture("met_norway", "compact.json"))
    registry = SourceRegistry(UA)
    session = async_get_clientsession(hass)

    await registry.async_fetch(session, geometry, now)
    assert not registry.failover_active, "one failure is not enough"

    # Wait out the adapter's backoff so the second cycle really re-attempts.
    await registry.async_fetch(session, geometry, now + timedelta(minutes=10))
    assert registry.failover_active

    series, statuses = await registry.async_fetch(session, geometry, now + timedelta(minutes=20))
    assert SOURCE_METNO in {s.source_id for s in series}
    metno = next(s for s in statuses if s.source_id == SOURCE_METNO)
    assert metno.contributed


async def test_metno_stands_down_after_two_open_meteo_successes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """Once the primary provider recovers, the correlated source goes quiet again."""
    _mock_librewxr(aioclient_mock, geometry)
    aioclient_mock.get(open_meteo.URL, status=500)
    aioclient_mock.get(met_norway.URL, json=load_fixture("met_norway", "compact.json"))
    registry = SourceRegistry(UA)
    session = async_get_clientsession(hass)

    for minute in (0, 10):
        await registry.async_fetch(session, geometry, now + timedelta(minutes=minute))
    assert registry.failover_active

    aioclient_mock.clear_requests()
    _mock_librewxr(aioclient_mock, geometry)
    aioclient_mock.get(open_meteo.URL, json=load_fixture("open_meteo", "dry.json"))
    aioclient_mock.get(met_norway.URL, json=load_fixture("met_norway", "compact.json"))

    await registry.async_fetch(session, geometry, now + timedelta(minutes=60))
    assert registry.failover_active, "one success is not enough to stand down"

    await registry.async_fetch(session, geometry, now + timedelta(minutes=120))
    assert not registry.failover_active


async def test_correlated_sources_never_vote_together(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """metno correlates 0.61 with knmi — the registry must never let both contribute."""
    _mock_librewxr(aioclient_mock, geometry)
    aioclient_mock.get(open_meteo.URL, status=500)
    aioclient_mock.get(met_norway.URL, json=load_fixture("met_norway", "compact.json"))
    registry = SourceRegistry(UA)
    session = async_get_clientsession(hass)

    for minute in (0, 10, 20):
        _, statuses = await registry.async_fetch(session, geometry, now + timedelta(minutes=minute))
        contributing = {s.source_id for s in statuses if s.contributed}
        assert not ({SOURCE_KNMI, SOURCE_METNO} <= contributing)
        assert not ({SOURCE_ICON_EU, SOURCE_METNO} <= contributing)


async def test_every_provider_failing_yields_no_series_and_no_exception(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """Zero sources is a legitimate outcome: the sensor goes unavailable, never guesses."""
    aioclient_mock.get(INDEX_URL, status=500)
    aioclient_mock.get(open_meteo.URL, status=500)
    aioclient_mock.get(met_norway.URL, status=500)
    registry = SourceRegistry(UA)

    series, statuses = await registry.async_fetch(async_get_clientsession(hass), geometry, now)

    assert series == []
    assert len(statuses) == 4
    assert not any(s.contributed for s in statuses)


async def test_attributions_cover_every_contributing_source(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """Each licence requires credit, and that data was modified — which it is."""
    _mock_librewxr(aioclient_mock, geometry)
    aioclient_mock.get(open_meteo.URL, json=load_fixture("open_meteo", "dry.json"))
    registry = SourceRegistry(UA)

    _, statuses = await registry.async_fetch(async_get_clientsession(hass), geometry, now)
    attributions = registry.attributions(statuses)

    assert len(attributions) == 3
    assert any("LibreWXR" in a for a in attributions)
    assert any("Open-Meteo" in a and "DWD" in a for a in attributions)
    assert any("Open-Meteo" in a and "KNMI" in a for a in attributions)
    assert all("modified" in a for a in attributions)


def test_failover_threshold_matches_the_documented_rule() -> None:
    """Two consecutive outcomes either way — no hair trigger, no long limbo."""
    assert FAILOVER_THRESHOLD == 2


@pytest.mark.parametrize("source_id", [SOURCE_LIBREWXR, SOURCE_ICON_EU, SOURCE_KNMI, SOURCE_METNO])
async def test_every_source_is_reported_every_cycle(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
    source_id: str,
) -> None:
    """The sensor must be able to explain all four sources, contributing or not."""
    _mock_librewxr(aioclient_mock, geometry)
    aioclient_mock.get(open_meteo.URL, json=load_fixture("open_meteo", "dry.json"))
    registry = SourceRegistry(UA)

    _, statuses = await registry.async_fetch(async_get_clientsession(hass), geometry, now)

    assert source_id in {s.source_id for s in statuses}
