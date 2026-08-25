"""LibreWXR OPERA radar nowcast adapter.

Fetches `weather-maps.json`, then one grayscale tile per not-yet-sampled frame,
and reduces each tile to a single mm/h value for the configured disc. The only
module allowed to use Pillow and image-related numpy
(docs/ARCHITECTURE.md § Frame sampling strategy).

Intensity calibration (pinned in phase 3 from the AGPL-3.0 LibreWXR source, see
STATE.md): the renderer encodes reflectivity as
`pixel = clamp((dBZ + 32) * 2, 0, 255)` with NODATA mapped to 0
(`librewxr.sources._helpers._dbz_float_to_uint8`), and colour scheme 0
("Black and White") maps pixel value `p` through row `p // 2` of
`librewxr/colors/color_table.csv`, whose row `i` is grey `#iiiiii` at
`dBZ = i - 32`. So the rendered grey level *is* `dBZ + 32`, and grey 0 is both
"no echo" and "no data" (fully transparent, alpha 0).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from aiohttp import ClientSession, ClientTimeout
from PIL import Image

from ..const import SOURCE_LIBREWXR
from .base import (
    CELL_KM,
    RELIABILITY,
    STATE_FAILED,
    STATE_OK,
    STATE_STALE,
    Backoff,
    FetchResult,
    RequestBudget,
    SampleGeometry,
    SourceSeries,
    SourceStatus,
    restate,
)

if TYPE_CHECKING:
    from ..cache import SampleCache

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.librewxr.net"
WEATHER_MAPS_PATH = "/public/weather-maps.json"

# Fixed tile parameters (docs/ARCHITECTURE.md § Frame sampling strategy).
ZOOM = 8
TILE_SIZE = 256
COLOR_SCHEME = 0  # grayscale ramp: grey level == dBZ + 32
SMOOTH = 0  # unsmoothed, so pixel values stay exactly on the palette
SNOW = 0  # rain LUT only; the snow LUT would reuse grey levels 128-255

STEP_S = 600  # 10-minute frames
NOWCAST_FRAMES = 6  # +10...+60 min

#: Self-imposed ceiling from the docs/DATA_SOURCES.md request budget.
MAX_REQUESTS_PER_HOUR = 20

#: Grey level below which the composite reports no echo at all.
GREY_NODATA = 0
DBZ_OFFSET = 32  # grey = dBZ + 32

#: Marshall-Palmer Z = 200 * R^1.6, inverted to R = (Z / 200) ** (1 / 1.6).
MP_A = 200.0
MP_B = 1.6

#: Spatial aggregation over the disc: robust against single-pixel radar speckle.
PERCENTILE = 90

_TIMEOUT_S = 20
_METERS_PER_PIXEL_EQUATOR = 156543.03392804097


def dbz_to_mm_per_h(dbz: float) -> float:
    """Marshall-Palmer reflectivity to rain rate (docs/DATA_SOURCES.md § Intensity mapping)."""
    return float((10.0 ** (dbz / 10.0) / MP_A) ** (1.0 / MP_B))


def grey_to_mm_per_h(grey: int) -> float:
    """Rendered grey level of colour scheme 0 to rain rate. Grey 0 means no echo."""
    if grey <= GREY_NODATA:
        return 0.0
    return dbz_to_mm_per_h(float(grey) - DBZ_OFFSET)


def _project(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Web Mercator global pixel coordinates at `zoom` (256 px tiles)."""
    scale = TILE_SIZE * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * scale
    return x, y


def _meters_per_pixel(lat: float, zoom: int) -> float:
    """Ground resolution of one pixel at this latitude — 376 m at 52 N, z=8."""
    return _METERS_PER_PIXEL_EQUATOR * math.cos(math.radians(lat)) / (2**zoom)


class DiscMask:
    """Which pixels of which tiles fall inside the sampled disc.

    Computed once per geometry and reused for every frame, so per-frame work is
    just "decode one tile, take the masked pixels".
    """

    def __init__(self, geometry: SampleGeometry, zoom: int = ZOOM) -> None:
        self.zoom = zoom
        cx, cy = _project(geometry.latitude, geometry.longitude, zoom)
        radius_px = geometry.radius_km * 1000.0 / _meters_per_pixel(geometry.latitude, zoom)
        self.radius_px = radius_px

        x0 = math.floor(cx - radius_px)
        x1 = math.ceil(cx + radius_px)
        y0 = math.floor(cy - radius_px)
        y1 = math.ceil(cy + radius_px)

        self.tiles: list[tuple[int, int, tuple[int, int, int, int], np.ndarray]] = []
        for tx in range(x0 // TILE_SIZE, x1 // TILE_SIZE + 1):
            for ty in range(y0 // TILE_SIZE, y1 // TILE_SIZE + 1):
                # Sub-rectangle of this tile that the disc's bounding box touches.
                rx0 = max(x0, tx * TILE_SIZE) - tx * TILE_SIZE
                rx1 = min(x1, (tx + 1) * TILE_SIZE) - tx * TILE_SIZE
                ry0 = max(y0, ty * TILE_SIZE) - ty * TILE_SIZE
                ry1 = min(y1, (ty + 1) * TILE_SIZE) - ty * TILE_SIZE
                if rx1 <= rx0 or ry1 <= ry0:
                    continue
                # Pixel centres inside the sub-rectangle, in global pixel space.
                gx = tx * TILE_SIZE + np.arange(rx0, rx1, dtype=np.float64) + 0.5
                gy = ty * TILE_SIZE + np.arange(ry0, ry1, dtype=np.float64) + 0.5
                mask = ((gy[:, None] - cy) ** 2 + (gx[None, :] - cx) ** 2) <= radius_px**2
                if mask.any():
                    self.tiles.append((tx, ty, (ry0, ry1, rx0, rx1), mask))

        if not self.tiles:
            # Radius smaller than one pixel: fall back to the single centre pixel.
            tx, ty = int(cx) // TILE_SIZE, int(cy) // TILE_SIZE
            px, py = int(cx) % TILE_SIZE, int(cy) % TILE_SIZE
            self.tiles = [(tx, ty, (py, py + 1, px, px + 1), np.ones((1, 1), dtype=bool))]

    @property
    def tile_count(self) -> int:
        """How many tiles a single frame costs."""
        return len(self.tiles)


class LibreWxrAdapter:
    """Adapter for the LibreWXR radar nowcast."""

    source_ids = (SOURCE_LIBREWXR,)

    def __init__(self, user_agent: str, cache: SampleCache | None = None) -> None:
        self._user_agent = user_agent
        self._cache = cache
        self._budget = RequestBudget(limit=MAX_REQUESTS_PER_HOUR)
        self._backoff = Backoff()
        self._mask: DiscMask | None = None
        self._mask_key: str | None = None
        self._last: FetchResult | None = None

    # --- adapter protocol ---------------------------------------------------

    def should_fetch(self, now: datetime) -> bool:
        """LibreWXR is fetched every cycle; only a backoff can hold it back."""
        return self._backoff.ready(now)

    def cached(self, now: datetime) -> FetchResult:
        """Re-present the last successful result, re-stated as stale once too old."""
        if self._last is None:
            return FetchResult(
                statuses=(SourceStatus(SOURCE_LIBREWXR, STATE_FAILED, detail="no data yet"),)
            )
        return restate(self._last, now)

    async def fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        """Fetch the frame index and sample every frame not already in the cache."""
        try:
            result = await self._fetch(session, geometry, now)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # provider-side failures must never break the cycle
            _LOGGER.debug("LibreWXR fetch failed: %s", err)
            self._backoff.record_failure(now)
            return self._failed(now, str(err))
        self._backoff.record_success()
        self._last = result
        return result

    # --- implementation -----------------------------------------------------

    def _failed(self, now: datetime, detail: str) -> FetchResult:
        """Failure result that still carries the previous series if it is usable."""
        if self._last is not None and self._last.series:
            series = self._last.series[0]
            if not series.is_stale(now):
                return FetchResult(
                    series=self._last.series,
                    statuses=(
                        SourceStatus(
                            SOURCE_LIBREWXR,
                            STATE_OK,
                            age_s=series.age_s(now),
                            contributed=True,
                            detail=f"reusing cached frames: {detail}",
                        ),
                    ),
                )
        return FetchResult(statuses=(SourceStatus(SOURCE_LIBREWXR, STATE_FAILED, detail=detail),))

    def _disc_mask(self, geometry: SampleGeometry) -> DiscMask:
        if self._mask is None or self._mask_key != geometry.key:
            self._mask = DiscMask(geometry)
            self._mask_key = geometry.key
        return self._mask

    async def _fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        if not self._budget.consume(now):
            raise RuntimeError("hourly request budget exhausted")
        index = await self._get_json(session, BASE_URL + WEATHER_MAPS_PATH)
        host, frames = parse_weather_maps(index)

        mask = self._disc_mask(geometry)
        issued_at = frames[0][0]  # newest past frame == the current observation
        slots: list[tuple[datetime, float]] = []

        for slot_time, path in frames:
            cached = self._cache.get(path) if self._cache else None
            if cached is not None:
                slots.append((slot_time, cached))
                continue
            if self._budget.remaining(now) < mask.tile_count:
                # Out of budget: stop sampling rather than exceed it. Whatever was
                # already collected still forms a usable (shorter) series.
                _LOGGER.debug("LibreWXR budget reached; %d frames sampled", len(slots))
                break
            value = await self._sample_frame(session, host, path, mask, now)
            if self._cache is not None:
                self._cache.set(path, slot_time, value)
            slots.append((slot_time, value))

        if not slots:
            raise RuntimeError("no frames could be sampled")

        series = SourceSeries(
            source_id=SOURCE_LIBREWXR,
            issued_at=issued_at,
            fetched_at=now,
            step_s=STEP_S,
            slots=tuple(slots),
            cell_km=CELL_KM[SOURCE_LIBREWXR],
            reliability=RELIABILITY[SOURCE_LIBREWXR],
        )
        stale = series.is_stale(now)
        return FetchResult(
            series=(series,),
            statuses=(
                SourceStatus(
                    SOURCE_LIBREWXR,
                    STATE_STALE if stale else STATE_OK,
                    age_s=series.age_s(now),
                    contributed=not stale,
                ),
            ),
        )

    async def _sample_frame(
        self,
        session: ClientSession,
        host: str,
        path: str,
        mask: DiscMask,
        now: datetime,
    ) -> float:
        """Reduce one frame to a single mm/h value over the disc.

        Tiles are fetched and decoded one at a time and released immediately, so
        peak transient memory is one decoded 256x256 tile regardless of frame count.
        """
        collected: list[np.ndarray] = []
        for tile_x, tile_y, (ry0, ry1, rx0, rx1), tile_mask in mask.tiles:
            url = (
                f"{host}{path}/{TILE_SIZE}/{mask.zoom}/{tile_x}/{tile_y}"
                f"/{COLOR_SCHEME}/{SMOOTH}_{SNOW}.png"
            )
            if not self._budget.consume(now):
                raise RuntimeError("hourly request budget exhausted")
            raw = await self._get_bytes(session, url)
            grey = _decode_grey(raw)
            collected.append(grey[ry0:ry1, rx0:rx1][tile_mask])
            del grey, raw

        values = np.concatenate(collected) if len(collected) > 1 else collected[0]
        # `nearest` keeps the result an actually observed grey level, so the value
        # maps back to a real dBZ step instead of interpolating between palette codes.
        grey_p90 = int(np.percentile(values, PERCENTILE, method="nearest"))
        del values, collected
        return grey_to_mm_per_h(grey_p90)

    async def _get_json(self, session: ClientSession, url: str) -> Any:
        async with session.get(
            url, headers=self._headers(), timeout=ClientTimeout(total=_TIMEOUT_S)
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _get_bytes(self, session: ClientSession, url: str) -> bytes:
        async with session.get(
            url, headers=self._headers(), timeout=ClientTimeout(total=_TIMEOUT_S)
        ) as response:
            response.raise_for_status()
            return await response.read()

    def _headers(self) -> dict[str, str]:
        # An identifying User-Agent is mandatory: the default Python one is
        # rejected with HTTP 403 (measured in phase 0).
        return {"User-Agent": self._user_agent, "Accept-Encoding": "gzip"}


def _decode_grey(raw: bytes) -> np.ndarray:
    """Decode a scheme-0 tile to a uint8 grey array; transparent pixels become 0.

    Scheme 0 is a pure grey ramp, so the red channel *is* the grey level — reading
    it directly avoids the rounding that a luminance conversion would introduce.
    """
    with Image.open(io.BytesIO(raw)) as image:
        rgba = np.asarray(image.convert("RGBA"))
    grey = np.where(rgba[..., 3] > 0, rgba[..., 0], 0).astype(np.uint8)
    del rgba
    return grey


def parse_weather_maps(index: Any) -> tuple[str, list[tuple[datetime, str]]]:
    """Extract the tile host and the frames to sample from `weather-maps.json`.

    Frames are the newest `past` frame (the current observation) followed by every
    `nowcast` frame, oldest first — 7 frames covering now ... +60 min.
    """
    if not isinstance(index, dict):
        raise ValueError("weather-maps.json is not an object")
    host = index.get("host") or BASE_URL
    radar = index.get("radar")
    if not isinstance(radar, dict):
        raise ValueError("weather-maps.json has no radar section")

    past = [f for f in radar.get("past") or [] if _is_frame(f)]
    nowcast = [f for f in radar.get("nowcast") or [] if _is_frame(f)]
    if not past and not nowcast:
        raise ValueError("weather-maps.json contains no usable frames")

    chosen = ([max(past, key=lambda f: f["time"])] if past else []) + sorted(
        nowcast, key=lambda f: f["time"]
    )[:NOWCAST_FRAMES]
    frames = [(datetime.fromtimestamp(int(f["time"]), tz=UTC), str(f["path"])) for f in chosen]
    return str(host), frames


def _is_frame(frame: Any) -> bool:
    return (
        isinstance(frame, dict)
        and isinstance(frame.get("time"), int | float)
        and isinstance(frame.get("path"), str)
        and bool(frame["path"])
    )


__all__ = [
    "DiscMask",
    "LibreWxrAdapter",
    "dbz_to_mm_per_h",
    "grey_to_mm_per_h",
    "parse_weather_maps",
]
