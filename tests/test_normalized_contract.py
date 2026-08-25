"""Every adapter must return the *same* normalized structure from its fixtures.

This is the phase 3 acceptance criterion that keeps the engine (phase 4) free of
per-provider special cases: whatever the wire format was — paletted PNG tiles,
a five-coordinate multi-model JSON list, or a single-point GeoJSON-ish document —
what comes out is a `SourceSeries` with UTC slot starts and mm/h on one scale.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.walk_the_dog.const import (
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
    SOURCE_METNO,
    intensity_class,
)
from custom_components.walk_the_dog.sources import build_user_agent, met_norway, open_meteo
from custom_components.walk_the_dog.sources.base import (
    CELL_KM,
    RELIABILITY,
    SampleGeometry,
    SourceSeries,
)
from custom_components.walk_the_dog.sources.librewxr import (
    BASE_URL,
    WEATHER_MAPS_PATH,
    DiscMask,
    LibreWxrAdapter,
    parse_weather_maps,
)
from custom_components.walk_the_dog.sources.met_norway import MetNorwayAdapter
from custom_components.walk_the_dog.sources.open_meteo import OpenMeteoAdapter

from .conftest import load_bytes, load_fixture
from .test_librewxr import _tile_urls

UA = build_user_agent("0.1.0")
INDEX_URL = BASE_URL + WEATHER_MAPS_PATH
VALID_CLASSES = {"none", "light", "moderate", "heavy"}


async def _all_series(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> dict[str, SourceSeries]:
    """Drive all three adapters against the recorded fixtures."""
    index = load_fixture("librewxr", "weather-maps.json")
    aioclient_mock.get(INDEX_URL, json=index)
    mask = DiscMask(geometry)
    host, frames = parse_weather_maps(index)
    tile = load_bytes("librewxr", "tile_wet.png")
    for _, path in frames:
        for url in _tile_urls(host, path, mask):
            aioclient_mock.get(url, content=tile)
    aioclient_mock.get(open_meteo.URL, json=load_fixture("open_meteo", "heavy.json"))
    aioclient_mock.get(met_norway.URL, json=load_fixture("met_norway", "compact.json"))

    session = async_get_clientsession(hass)
    metno = MetNorwayAdapter(UA)
    metno.enabled = True

    series: dict[str, SourceSeries] = {}
    for adapter in (LibreWxrAdapter(UA), OpenMeteoAdapter(UA), metno):
        result = await adapter.fetch(session, geometry, now)
        for entry in result.series:
            series[entry.source_id] = entry
    return series


async def test_all_four_sources_produce_a_series(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """One adapter per provider, four normalized series in total."""
    series = await _all_series(hass, aioclient_mock, geometry, now)

    assert set(series) == {SOURCE_LIBREWXR, SOURCE_ICON_EU, SOURCE_KNMI, SOURCE_METNO}


@pytest.mark.parametrize("source_id", [SOURCE_LIBREWXR, SOURCE_ICON_EU, SOURCE_KNMI, SOURCE_METNO])
async def test_series_satisfies_the_normalized_contract(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
    source_id: str,
) -> None:
    """The exact structure docs/ARCHITECTURE.md § Data flow specifies."""
    entry = (await _all_series(hass, aioclient_mock, geometry, now))[source_id]

    assert entry.source_id == source_id
    assert entry.step_s in (600, 3600)
    assert entry.cell_km == CELL_KM[source_id]
    assert entry.reliability == RELIABILITY[source_id]
    assert entry.fetched_at == now

    # Timestamps: aware UTC, sorted, evenly spaced by the declared step.
    assert entry.issued_at.tzinfo is not None
    assert entry.issued_at.utcoffset() == timedelta(0)
    assert entry.slots
    times = [slot for slot, _ in entry.slots]
    assert times == sorted(times)
    assert all(t.tzinfo is not None and t.utcoffset() == timedelta(0) for t in times)
    assert all(
        (later - earlier).total_seconds() == entry.step_s for earlier, later in pairwise(times)
    )

    # Intensities: finite, non-negative mm/h that classify on the common scale.
    values = [value for _, value in entry.slots]
    assert all(isinstance(value, float) for value in values)
    assert all(value >= 0.0 for value in values)
    assert all(value < 1000.0 for value in values)
    assert all(intensity_class(value) in VALID_CLASSES for value in values)


async def test_the_tile_source_is_the_only_sub_hourly_one(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """Phase 0: no free source gives Poland native sub-hourly NWP, so steps are mixed.

    The engine has to align a 10-minute series against hourly ones — this pins the
    fact rather than leaving phase 4 to assume a single common step.
    """
    series = await _all_series(hass, aioclient_mock, geometry, now)

    assert series[SOURCE_LIBREWXR].step_s == 600
    assert series[SOURCE_ICON_EU].step_s == 3600
    assert series[SOURCE_KNMI].step_s == 3600
    assert series[SOURCE_METNO].step_s == 3600


async def test_the_radar_source_covers_the_next_hour_precisely(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """LibreWXR carries the *when*: seven slots from now to +60 minutes."""
    entry = (await _all_series(hass, aioclient_mock, geometry, now))[SOURCE_LIBREWXR]

    assert len(entry.slots) == 7
    span = entry.slots[-1][0] - entry.slots[0][0]
    assert span == timedelta(minutes=60)
    assert entry.horizon_end == entry.slots[-1][0] + timedelta(minutes=10)


async def test_the_nwp_sources_reach_beyond_the_search_window(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """A walk plus a 1 h earlier and 30 min later margin must fit inside the horizon."""
    series = await _all_series(hass, aioclient_mock, geometry, now)

    for source_id in (SOURCE_ICON_EU, SOURCE_KNMI, SOURCE_METNO):
        entry = series[source_id]
        assert entry.horizon_end is not None
        assert entry.horizon_end - entry.slots[0][0] >= timedelta(hours=12)
