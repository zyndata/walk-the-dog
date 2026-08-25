"""LibreWXR adapter: pinned calibration, disc sampling, budget and failure handling."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from PIL import Image
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.walk_the_dog.cache import SampleCache
from custom_components.walk_the_dog.const import (
    INTENSITY_NONE,
    INTENSITY_THRESHOLD_HEAVY,
    INTENSITY_THRESHOLD_LIGHT,
    INTENSITY_THRESHOLD_MODERATE,
    SOURCE_LIBREWXR,
    intensity_class,
)
from custom_components.walk_the_dog.sources.base import (
    STATE_FAILED,
    STATE_OK,
    SampleGeometry,
)
from custom_components.walk_the_dog.sources.librewxr import (
    BASE_URL,
    COLOR_SCHEME,
    MAX_REQUESTS_PER_HOUR,
    SMOOTH,
    SNOW,
    STEP_S,
    TILE_SIZE,
    WEATHER_MAPS_PATH,
    DiscMask,
    LibreWxrAdapter,
    grey_to_mm_per_h,
    parse_weather_maps,
)

from .conftest import load_bytes, load_fixture

INDEX_URL = BASE_URL + WEATHER_MAPS_PATH
UA = "walk_the_dog/test (+https://github.com/zyndata/walk-the-dog)"

# The frames the recorded weather-maps.json advertises.
NEWEST_PAST = datetime(2026, 8, 25, 6, 50, tzinfo=UTC)


# --- Calibration -----------------------------------------------------------------
#
# Pinned in phase 3 from the AGPL-3.0 LibreWXR source rather than guessed:
#   * `librewxr.sources._helpers._dbz_float_to_uint8` encodes
#     `pixel = clamp((dBZ + 32) * 2, 0, 255)`, NODATA -> 0;
#   * `librewxr.colors.schemes` renders colour scheme 0 through row `pixel // 2` of
#     `color_table.csv`, whose row `i` holds grey `#iiiiii` at `dBZ = i - 32`.
# So the rendered grey level equals `dBZ + 32`. This table locks that down together
# with the Marshall-Palmer conversion and the class boundaries of
# docs/DATA_SOURCES.md § Intensity mapping.

CALIBRATION: list[tuple[int, int, float, str]] = [
    # grey, dBZ, mm/h, class
    (0, -32, 0.0, INTENSITY_NONE),  # transparent: no echo or no data
    (38, 6, 0.086468, INTENSITY_NONE),  # just below the 7.0 dBZ light boundary
    (39, 7, 0.099852, INTENSITY_NONE),  # the boundary itself rounds just under 0.1
    (42, 10, 0.153765, INTENSITY_THRESHOLD_LIGHT),  # lowest level OPERA actually reports
    (53, 21, 0.748783, INTENSITY_THRESHOLD_LIGHT),
    (61, 29, 2.367861, INTENSITY_THRESHOLD_LIGHT),  # just below 29.4 dBZ
    (62, 30, 2.734364, INTENSITY_THRESHOLD_MODERATE),  # just above it
    (69, 37, 7.487835, INTENSITY_THRESHOLD_MODERATE),  # just below 37.1 dBZ
    (70, 38, 8.646817, INTENSITY_THRESHOLD_HEAVY),  # just above it
    (80, 48, 36.463324, INTENSITY_THRESHOLD_HEAVY),
]


@pytest.mark.parametrize(("grey", "dbz", "mm_per_h", "expected_class"), CALIBRATION)
def test_grey_level_calibration(grey: int, dbz: int, mm_per_h: float, expected_class: str) -> None:
    """Grey level -> dBZ -> mm/h -> class, locked against the LibreWXR palette."""
    assert grey - 32 == dbz
    assert grey_to_mm_per_h(grey) == pytest.approx(mm_per_h, abs=1e-6)
    assert intensity_class(grey_to_mm_per_h(grey)) == expected_class


def test_class_boundaries_match_the_data_sources_table() -> None:
    """docs/DATA_SOURCES.md maps the mm/h classes onto 7.0 / 29.4 / 37.1 dBZ."""
    assert intensity_class(grey_to_mm_per_h(32 + 7)) == INTENSITY_NONE
    assert intensity_class(grey_to_mm_per_h(32 + 8)) == INTENSITY_THRESHOLD_LIGHT
    assert intensity_class(grey_to_mm_per_h(32 + 29)) == INTENSITY_THRESHOLD_LIGHT
    assert intensity_class(grey_to_mm_per_h(32 + 30)) == INTENSITY_THRESHOLD_MODERATE
    assert intensity_class(grey_to_mm_per_h(32 + 37)) == INTENSITY_THRESHOLD_MODERATE
    assert intensity_class(grey_to_mm_per_h(32 + 38)) == INTENSITY_THRESHOLD_HEAVY


# --- weather-maps.json parsing ---------------------------------------------------


def test_parse_weather_maps_takes_the_newest_past_frame_and_all_nowcasts() -> None:
    """Seven frames: the current observation plus +10...+60 min."""
    host, frames = parse_weather_maps(load_fixture("librewxr", "weather-maps.json"))

    assert host == BASE_URL
    assert len(frames) == 7
    assert frames[0][0] == NEWEST_PAST
    assert [f[0] for f in frames] == [NEWEST_PAST + timedelta(minutes=10 * i) for i in range(7)]
    assert all(path.startswith("/v2/radar/") for _, path in frames)


def test_parse_weather_maps_rejects_junk() -> None:
    """Malformed indexes raise rather than silently yielding an empty forecast."""
    for payload in ([], {"radar": {}}, {"radar": {"past": [], "nowcast": []}}, "nope"):
        with pytest.raises(ValueError):
            parse_weather_maps(payload)


def test_parse_weather_maps_skips_unusable_frame_entries() -> None:
    """A frame missing its path or time is dropped, not crashed on."""
    _, frames = parse_weather_maps(
        {
            "host": BASE_URL,
            "radar": {
                "past": [{"time": 1787640600, "path": "/v2/radar/1787640600"}],
                "nowcast": [
                    {"time": 1787641200},  # no path
                    {"path": "/v2/radar/x"},  # no time
                    {"time": 1787641800, "path": "/v2/radar/1787641800"},
                ],
            },
        }
    )
    assert [path for _, path in frames] == ["/v2/radar/1787640600", "/v2/radar/1787641800"]


# --- disc geometry ---------------------------------------------------------------


def test_disc_mask_covers_one_tile_and_the_expected_pixel_count() -> None:
    """A 5 km disc at 52 N is ~13 px across one z=8 tile (376 m per pixel)."""
    mask = DiscMask(SampleGeometry(52.2297, 21.0122, 5.0))

    assert mask.tile_count == 1
    assert mask.radius_px == pytest.approx(13.3, abs=0.3)
    tile_x, tile_y, _, pixels = mask.tiles[0]
    assert (tile_x, tile_y) == (142, 84)
    # A disc of r px holds about pi*r^2 pixels.
    assert pixels.sum() == pytest.approx(3.14159 * mask.radius_px**2, rel=0.1)


def test_disc_mask_spans_several_tiles_at_a_tile_corner() -> None:
    """At a tile boundary the disc costs up to four tiles — the budgeted worst case."""
    # Longitude of the boundary between z=8 tiles 142 and 143, at the matching latitude.
    corner_lon = 143 / 256 * 360 - 180
    mask = DiscMask(SampleGeometry(52.2297, corner_lon, 15.0))

    assert 2 <= mask.tile_count <= 4
    assert sum(int(pixels.sum()) for _, _, _, pixels in mask.tiles) == pytest.approx(
        3.14159 * mask.radius_px**2, rel=0.1
    )


def test_disc_mask_never_ends_up_empty() -> None:
    """Even a sub-pixel radius samples the centre pixel rather than nothing."""
    mask = DiscMask(SampleGeometry(52.2297, 21.0122, 0.05))
    assert mask.tile_count == 1
    assert mask.tiles[0][3].sum() >= 1


# --- fetching --------------------------------------------------------------------


def _tile_urls(host: str, path: str, mask: DiscMask) -> list[str]:
    return [
        f"{host}{path}/{TILE_SIZE}/{mask.zoom}/{tx}/{ty}/{COLOR_SCHEME}/{SMOOTH}_{SNOW}.png"
        for tx, ty, _, _ in mask.tiles
    ]


def _mock_frames(
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    tile: bytes,
) -> tuple[dict, DiscMask]:
    index = load_fixture("librewxr", "weather-maps.json")
    aioclient_mock.get(INDEX_URL, json=index)
    mask = DiscMask(geometry)
    host, frames = parse_weather_maps(index)
    for _, path in frames:
        for url in _tile_urls(host, path, mask):
            aioclient_mock.get(url, content=tile)
    return index, mask


async def test_fetch_returns_a_normalized_seven_slot_series(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """A full cycle yields one series covering now ... +60 min on the common scale."""
    _mock_frames(aioclient_mock, geometry, load_bytes("librewxr", "tile_wet.png"))
    adapter = LibreWxrAdapter(UA)

    result = await adapter.fetch(async_get_clientsession(hass), geometry, now)

    assert result.ok
    series = result.series[0]
    assert series.source_id == SOURCE_LIBREWXR
    assert series.step_s == STEP_S
    assert len(series.slots) == 7
    assert series.issued_at == NEWEST_PAST
    assert series.fetched_at == now
    assert series.cell_km == 2.0
    assert series.reliability == 1.0
    assert all(value >= 0.0 for _, value in series.slots)
    # 1 index request + 1 tile per frame.
    assert len(aioclient_mock.mock_calls) == 8


async def test_fetch_sends_an_identifying_user_agent(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """LibreWXR rejects the default Python agent with HTTP 403 (measured in phase 0)."""
    _mock_frames(aioclient_mock, geometry, load_bytes("librewxr", "tile_dry.png"))

    await LibreWxrAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)

    for call in aioclient_mock.mock_calls:
        assert call[3]["User-Agent"] == UA


async def test_wet_tile_maps_to_the_expected_intensity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    wet_geometry: SampleGeometry,
    now: datetime,
) -> None:
    """End to end on a recorded frame: pixels -> grey -> dBZ -> mm/h -> class.

    The recorded tile covers this disc with continuous precipitation; its 90th
    percentile grey level is 64, i.e. 32 dBZ, i.e. 3.65 mm/h — moderate rain.
    """
    _mock_frames(aioclient_mock, wet_geometry, load_bytes("librewxr", "tile_wet.png"))

    result = await LibreWxrAdapter(UA).fetch(async_get_clientsession(hass), wet_geometry, now)

    value = result.series[0].slots[0][1]
    assert value == pytest.approx(grey_to_mm_per_h(64), abs=1e-6)
    assert value == pytest.approx(3.646332, abs=1e-6)
    assert intensity_class(value) == INTENSITY_THRESHOLD_MODERATE


async def test_dry_tile_reads_as_no_rain(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """The Warszawa frame has 54 lit pixels in 65 536; the disc verdict is still dry."""
    _mock_frames(aioclient_mock, geometry, load_bytes("librewxr", "tile_dry.png"))

    result = await LibreWxrAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)

    value = result.series[0].slots[0][1]
    assert value == 0.0
    assert intensity_class(value) == INTENSITY_NONE


async def test_p90_ignores_isolated_speckle(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """A handful of hot pixels must not raise the disc's verdict; a full disc must."""
    mask = DiscMask(geometry)
    _, _, (ry0, _, rx0, _), pixels = mask.tiles[0]

    def tile_with(count: int) -> bytes:
        grey = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint8)
        rows, cols = np.nonzero(pixels)
        for row, col in list(zip(rows, cols, strict=True))[:count]:
            grey[ry0 + row, rx0 + col] = 70  # 38 dBZ, heavy
        rgba = np.dstack([grey, grey, grey, np.where(grey > 0, 255, 0).astype(np.uint8)])
        buffer = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buffer, format="PNG")
        return buffer.getvalue()

    total = int(pixels.sum())

    _mock_frames(aioclient_mock, geometry, tile_with(total // 20))  # 5% of the disc
    speckled = await LibreWxrAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)
    assert speckled.series[0].slots[0][1] == 0.0

    aioclient_mock.clear_requests()
    _mock_frames(aioclient_mock, geometry, tile_with(total))  # the whole disc
    soaked = await LibreWxrAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)
    assert intensity_class(soaked.series[0].slots[0][1]) == INTENSITY_THRESHOLD_HEAVY


async def test_cached_frames_are_not_refetched(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """A warm cache costs one index request plus only the newly published frames."""
    cache = SampleCache(geometry.key)
    adapter = LibreWxrAdapter(UA, cache=cache)
    _mock_frames(aioclient_mock, geometry, load_bytes("librewxr", "tile_wet.png"))
    session = async_get_clientsession(hass)

    await adapter.fetch(session, geometry, now)
    assert len(cache) == 7
    first_pass = len(aioclient_mock.mock_calls)

    await adapter.fetch(session, geometry, now)
    # Only the index was fetched the second time round.
    assert len(aioclient_mock.mock_calls) - first_pass == 1


async def test_fetch_stops_before_exceeding_the_hourly_budget(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """The self-imposed 20 requests/hour ceiling is never crossed, cold cache or not."""
    _mock_frames(aioclient_mock, geometry, load_bytes("librewxr", "tile_wet.png"))
    adapter = LibreWxrAdapter(UA)  # no cache: every cycle re-samples all 7 frames
    session = async_get_clientsession(hass)

    for minute in range(0, 60, 10):
        await adapter.fetch(session, geometry, now + timedelta(minutes=minute))

    assert len(aioclient_mock.mock_calls) <= MAX_REQUESTS_PER_HOUR


async def test_budget_recovers_in_the_next_hour(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """The cap rolls, so the following walk window starts with a full allowance."""
    _mock_frames(aioclient_mock, geometry, load_bytes("librewxr", "tile_wet.png"))
    adapter = LibreWxrAdapter(UA)
    session = async_get_clientsession(hass)

    for minute in range(0, 60, 10):
        await adapter.fetch(session, geometry, now + timedelta(minutes=minute))
    exhausted = len(aioclient_mock.mock_calls)

    result = await adapter.fetch(session, geometry, now + timedelta(hours=2))
    assert len(aioclient_mock.mock_calls) > exhausted
    assert result.series


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"status": 500}, "server error"),
        ({"status": 403}, "rejected user agent"),
        ({"exc": TimeoutError()}, "timeout"),
        ({"text": "not json at all"}, "malformed body"),
    ],
)
async def test_provider_failures_are_reported_not_raised(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
    kwargs: dict,
    reason: str,
) -> None:
    """Any provider-side problem degrades the source; it never breaks the cycle."""
    aioclient_mock.get(INDEX_URL, **kwargs)

    result = await LibreWxrAdapter(UA).fetch(async_get_clientsession(hass), geometry, now)

    assert not result.ok, reason
    assert result.series == ()
    assert result.statuses[0].state == STATE_FAILED
    assert result.statuses[0].source_id == SOURCE_LIBREWXR


async def test_a_failed_cycle_reuses_the_previous_frames_while_they_are_fresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """One bad cycle must not blank the sensor — but the data keeps ageing."""
    _mock_frames(aioclient_mock, geometry, load_bytes("librewxr", "tile_wet.png"))
    adapter = LibreWxrAdapter(UA)
    session = async_get_clientsession(hass)
    await adapter.fetch(session, geometry, now)

    aioclient_mock.clear_requests()
    aioclient_mock.get(INDEX_URL, status=503)

    soon = await adapter.fetch(session, geometry, now + timedelta(minutes=10))
    assert soon.series
    assert soon.statuses[0].state == STATE_OK
    assert soon.statuses[0].contributed

    # Beyond 3x the 10-minute cadence the cached frames are stale and dropped.
    much_later = adapter.cached(now + timedelta(hours=1))
    assert not much_later.statuses[0].contributed


async def test_backoff_holds_off_a_failing_provider(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """After a failure the adapter waits a minute before spending another request."""
    aioclient_mock.get(INDEX_URL, status=500)
    adapter = LibreWxrAdapter(UA)

    await adapter.fetch(async_get_clientsession(hass), geometry, now)

    assert not adapter.should_fetch(now + timedelta(seconds=30))
    assert adapter.should_fetch(now + timedelta(seconds=61))


async def test_cached_before_any_fetch_reports_failure(now: datetime) -> None:
    """With nothing fetched yet the source is honestly unavailable, not silently dry."""
    result = LibreWxrAdapter(UA).cached(now)
    assert result.series == ()
    assert result.statuses[0].state == STATE_FAILED
