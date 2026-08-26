"""CHMI CZRAD radar nowcast adapter — regional, south-western Poland only.

A second, genuinely different radar network: the Czech Hydrometeorological
Institute's CZRAD composite (radars Brdy-Praha and Skalky), published as
`MAX_Z` — column-maximum reflectivity — plus `FCT_MAX_Z`, an extrapolation
nowcast of the same field at +10…+60 min in 10-minute steps. Both are
pre-rendered PNG composites this adapter only decodes and samples, which is what
this project requires of every source.

Everything here is verified against the live service and against CHMI's own
specification (`radar_description_en.pdf` on the same host), not inferred:

* **Endpoint** — `opendata.chmi.cz`, over HTTPS, documented, CC BY 4.0. This source
  was discovered through the Meteor Android app
  ([docs/SOURCE_meteor_androworks.md](../../../docs/SOURCE_meteor_androworks.md)),
  but Meteor's own frame endpoints serve nothing: its feed returns metadata with an
  empty body and every documented frame path answers 404 on every one of its hosts.
  CHMI is the real upstream, and the better one on every axis.
* **Geometry** — the frame is 680 x 460 px. CHMI publishes the extent of the whole
  image *and* of the data inside it; the two corners pin an EPSG:3857 map, and the
  data occupies a 598 x 378 rectangle whose top-left is (0, 82). Sampling uses
  CHMI's published numbers, so the projection is read rather than reverse-engineered.
* **Intensity** — the 15 echo levels are CHMI's published `scl-dbz-mmh` scale: a
  4 dBZ ladder from 4 to 60 dBZ, whose mm/h gridlines sit exactly where
  Marshall-Palmer `Z = 200 R^1.6` puts them. The same inversion the LibreWXR adapter
  uses therefore lands both radars on one scale — checked live over Bielsko-Biała,
  where the two independently encoded composites agreed on 12 dBZ to the step.

Regional by nature: the CZRAD domain is Czechia plus a margin. It reaches
Bielsko-Biała but stops short of Kraków, Warszawa and the whole north-east. Outside
the box the adapter reports `not_applicable` and never makes a request.

**And regional in strength, not only in extent.** A radar sees higher and coarser the
further out it looks, so this source's vote is weighted by how far the location is
from the nearest CHMI radar — see `range_factor`. Over Bielsko-Biała that matters a
lot: it is 167 km from Skalky and only 44 km from the Polish radar that feeds OPERA.

The second image source in the integration, so Pillow and image-related numpy
appear here as well as in `librewxr.py` (deviation recorded in STATE.md).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import tarfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from aiohttp import ClientSession, ClientTimeout
from PIL import Image

from ..const import SOURCE_CHMI, publish_settle_s
from .base import (
    CELL_KM,
    EARTH_RADIUS_KM,
    RELIABILITY,
    STATE_FAILED,
    STATE_NOT_APPLICABLE,
    STATE_OK,
    STATE_STALE,
    Backoff,
    FetchResult,
    RequestBudget,
    SampleGeometry,
    SourceSeries,
    SourceStatus,
    dbz_to_mm_per_h,
    restate,
)

if TYPE_CHECKING:
    from ..cache import SampleCache

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://opendata.chmi.cz/meteorology/weather/radar/composite"

#: Column-maximum reflectivity, one PNG per run.
OBSERVED_PATH = "/maxz/png/pacz2gmaps3.z_max3d.{stamp}.0.png"

#: The extrapolation forecast ships as ONE tar per run holding all six frames —
#: `ft60s10` is "forecast to 60 minutes, step 10". That is why a cycle costs two
#: requests rather than seven, and it is the single biggest reason to prefer this
#: endpoint over fetching frames one at a time.
FORECAST_PATH = "/fct_maxz/png/pacz2gmaps3.fct_z_max.{stamp}.ft60s10.tar"

#: **The unmasked products, deliberately.** CHMI also publishes `png_masked`
#: variants ("displayed considering precipitation on the earth's surface"), which
#: the Meteor app used. Those are rendered with blending, so their pixels are *not*
#: palette colours — sampling one over Bielsko-Biała produced #B1B1D0, whose nearest
#: palette neighbour is the white top of the ramp: 205 mm/h for light drizzle. The
#: unmasked frames carry exact palette colours and decode to an exact dBZ.
#: Do not switch to `png_masked` without solving that first.

#: `yyyyMMdd.HHmm`, always UTC, and always on a 5-minute boundary.
STAMP_FORMAT = "%Y%m%d.%H%M"
RUN_INTERVAL_MIN = 5

#: The run stamp is the *end* of the 5-minute interval it covers, and publication
#: lags it. Asking for a run that is not there yet costs a 404, so the newest
#: candidate is offset by this much before rounding.
#:
#: It is the same fact as the settle margin the coordinator times its wakeup by —
#: "how long after its stamp is a run readable" — so it is the same number, measured
#: in phase 8: 18 s on almost every run, 68 s on the worst seen. The 2 minutes this
#: used to be was a guess, and it cost every cycle up to a minute of freshness.
PUBLICATION_LAG = timedelta(seconds=publish_settle_s(SOURCE_CHMI))

#: How many 5-minute runs back to try before giving up. Three covers a late
#: publication or a short outage without turning a cycle into a retry storm.
MAX_RUN_ATTEMPTS = 3

STEP_S = 600  # the forecast's own 10-minute step, and the engine's grid

#: One fetch per published run. CHMI publishes every 5 minutes and the coordinator
#: may wake more often than that — on its own grid, on a sprint, and again whenever
#: a frame is due — so the cadence has to be the source's own, not the cycle's.
MIN_INTERVAL_S = 5 * 60
FORECAST_FRAMES = 6  # +10 ... +60 min, exactly what one tar holds

#: Self-imposed ceiling: 2 requests per cycle plus slack for stepping back over a
#: late run (docs/DATA_SOURCES.md § Request budget). Sized for CHMI's own 5-minute
#: publication rate, because the coordinator sprints to that cadence in the last
#: stretch before a walk sets off — 12 cycles an hour at 2 requests, plus slack.
MAX_REQUESTS_PER_HOUR = 30

# --- Frame geometry -------------------------------------------------------------
#
# From CHMI's radar_description_en.pdf, MAX_Z / FCT_MAX_Z in PNG:
#   geographical boundary of whole image: E 11.267-20.770 ; N 48.047-52.167
#   geographical boundary of data:        E 11.267-19.624 ; N 48.047-51.458
#   projection EPSG:3857, spatial resolution 1x1 km
# Applying the whole-image extent to the real 680x460 frame puts the data rectangle
# at exactly (0, 82)-(598, 460), which the tests assert.

FRAME_WIDTH = 680
FRAME_HEIGHT = 460

IMAGE_WEST_LON = 11.267
IMAGE_EAST_LON = 20.770
IMAGE_SOUTH_LAT = 48.047
IMAGE_NORTH_LAT = 52.167

DATA_WEST_LON = 11.267
DATA_EAST_LON = 19.624
DATA_SOUTH_LAT = 48.047
DATA_NORTH_LAT = 51.458

#: How far inside the *data* rectangle a location must sit to count as covered.
#: The edges of a radar composite are its weakest part — furthest from both radars,
#: and where the extrapolation has nothing upwind to work with.
COVERAGE_INSET_DEG = 0.3

#: Spatial aggregation over the disc — p90, as for LibreWXR: a max would fire on one
#: speckled cell, and at ~1 km per pixel a 5 km disc holds ~76 of them.
PERCENTILE = 90

# --- Range weighting ------------------------------------------------------------
#
# The composite's *grid* is a rectangle, but the radars' *sight* is not: a beam
# climbs and widens with range, so the far corners of the domain are measured from
# well above the weather. Weighting the vote by range is the honest way to say so.

#: The two CZRAD radars (chmi.cz): Skalky u Protivanova (antenna 767 m) and
#: Brdy-Praha (916 m). East of Skalky there is no second opinion — Brdy-Praha is
#: over 370 km from Bielsko-Biała and sees nothing there.
RADAR_SITES: tuple[tuple[float, float], ...] = (
    (49.501, 16.790),
    (49.658, 13.818),
)

#: Full weight out to here. At 120 km the 0.5° beam centre is ~2.7 km up, which
#: still intersects most precipitating layers.
RANGE_FULL_KM = 120.0

#: CHMI's own stated ceiling for *precipitation-intensity* estimation is
#: "approximately 150-200 km from the radar" (chmi.cz). At 200 km the beam centre is
#: ~4.9 km up — above the layer that produces the rain a walker actually feels.
RANGE_LIMIT_KM = 200.0

#: Weight multiplier at and beyond `RANGE_LIMIT_KM`. Half, not zero: the source is
#: still measuring something real, and a heavy echo aloft is still worth a vote —
#: it just must not outweigh a radar standing 44 km away.
RANGE_MIN_FACTOR = 0.5

# --- Intensity calibration ------------------------------------------------------

#: CHMI's published scale, index -> RGB
#: (https://opendata.chmi.cz/meteorology/weather/radar/scl/scl-dbz-mmh.png).
#: Index 0 is fully transparent and means "no echo or no data" — never
#: missing-that-counts, exactly like LibreWXR's grey 0.
PALETTE_RGB: tuple[tuple[int, int, int], ...] = (
    (0x00, 0x00, 0x00),  # 0  transparent / no echo
    (0x38, 0x00, 0x70),  # 1   4 dBZ
    (0x30, 0x00, 0xA8),  # 2   8 dBZ
    (0x00, 0x00, 0xFC),  # 3  12 dBZ
    (0x00, 0x6C, 0xC0),  # 4  16 dBZ
    (0x00, 0xA0, 0x00),  # 5  20 dBZ
    (0x00, 0xBC, 0x00),  # 6  24 dBZ
    (0x34, 0xD8, 0x00),  # 7  28 dBZ
    (0x9C, 0xDC, 0x00),  # 8  32 dBZ
    (0xE0, 0xDC, 0x00),  # 9  36 dBZ
    (0xFC, 0xB0, 0x00),  # 10 40 dBZ
    (0xFC, 0x84, 0x00),  # 11 44 dBZ
    (0xFC, 0x58, 0x00),  # 12 48 dBZ
    (0xFC, 0x00, 0x00),  # 13 52 dBZ
    (0xA0, 0x00, 0x00),  # 14 56 dBZ
    (0xFC, 0xFC, 0xFC),  # 15 60 dBZ
)

#: The ladder CHMI's own legend prints beside those colours: 4, 8, 12 … 60 dBZ.
#: Its mm/h gridlines (0.1, 1, 10, 100) fall at 7, 23, 39 and 55 dBZ, which is
#: exactly where `Z = 200 R^1.6` puts them — so the shared Marshall-Palmer
#: inversion in `base.py` is CHMI's own conversion, not an approximation of it.
DBZ_PER_LEVEL = 4.0
MAX_LEVEL = 15

#: Grey outline of the data domain drawn into the composite. It is cartography, not
#: precipitation, and is excluded from the sample rather than classified.
DOMAIN_OUTLINE_RGB = (0xC4, 0xC4, 0xC4)

#: A pixel that is neither transparent, nor the outline, nor an exact palette colour
#: is something this adapter does not understand. A few are tolerable; a disc full of
#: them means the product changed under us and the reading must not be trusted.
MAX_UNKNOWN_FRACTION = 0.2

_TIMEOUT_S = 30
_TAR_LIMIT_BYTES = 8 * 1024 * 1024


def level_to_mm_per_h(level: int) -> float:
    """Palette level to rain rate. Level 0 is no echo, never missing data."""
    if level <= 0:
        return 0.0
    return dbz_to_mm_per_h(min(level, MAX_LEVEL) * DBZ_PER_LEVEL)


def _mercator_y(lat: float) -> float:
    """Web Mercator northing (EPSG:3857), in the units the frame extent is linear in."""
    return math.asinh(math.tan(math.radians(lat)))


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    inner = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(inner))


def nearest_radar_km(latitude: float, longitude: float) -> float:
    """Distance to the closest CZRAD radar — how far this source is looking."""
    return min(_haversine_km((latitude, longitude), site) for site in RADAR_SITES)


def range_factor(latitude: float, longitude: float) -> float:
    """Weight multiplier for how far this location is from the nearest CHMI radar.

    A radar beam climbs and widens with range, so the same instrument is a different
    measurement at 40 km and at 170 km. This is the one place that fact enters the
    consensus, and it is why a location's `chmi` weight is not a constant.

    Full weight to `RANGE_FULL_KM`, then linear decay to `RANGE_MIN_FACTOR` at
    `RANGE_LIMIT_KM` — CHMI's own ceiling for intensity estimation. Bielsko-Biała
    sits at 167 km and is therefore weighted ~0.71.

    The justification is beam geometry and CHMI's stated limit, **not** a measured
    error curve: a live comparison against LibreWXR/OPERA over the whole domain found
    CZRAD reading lower nearly everywhere (18 points where OPERA saw rain and CZRAD
    saw none, against 2 the other way), but the miss rate did not rise cleanly with
    range, so it does not by itself pin the shape of this curve. See
    docs/DATA_SOURCES.md § CHMI.
    """
    distance = nearest_radar_km(latitude, longitude)
    if distance <= RANGE_FULL_KM:
        return 1.0
    if distance >= RANGE_LIMIT_KM:
        return RANGE_MIN_FACTOR
    span = RANGE_LIMIT_KM - RANGE_FULL_KM
    return 1.0 - (1.0 - RANGE_MIN_FACTOR) * (distance - RANGE_FULL_KM) / span


def reliability_at(latitude: float, longitude: float) -> float:
    """This source's consensus weight for a location: static weight x range factor."""
    return RELIABILITY[SOURCE_CHMI] * range_factor(latitude, longitude)


def covers(latitude: float, longitude: float, *, inset_deg: float = COVERAGE_INSET_DEG) -> bool:
    """Whether a point sits inside the composite's *data* rectangle, edges trimmed."""
    return (
        DATA_SOUTH_LAT + inset_deg <= latitude <= DATA_NORTH_LAT - inset_deg
        and DATA_WEST_LON + inset_deg <= longitude <= DATA_EAST_LON - inset_deg
    )


def covers_geometry(geometry: SampleGeometry) -> bool:
    """Whether the whole sampled disc — not just its centre — is inside the box.

    The data rectangle is smaller than the image, and outside it every pixel is
    transparent. A disc hanging over the edge would sample that margin as "no echo"
    and quietly drag the percentile down, which is worse than not sampling at all.
    """
    return all(covers(lat, lon) for lat, lon in geometry.sample_points())


def to_pixel(latitude: float, longitude: float) -> tuple[float, float]:
    """Project lat/lon to frame pixels through CHMI's published image extent."""
    north = _mercator_y(IMAGE_NORTH_LAT)
    span = north - _mercator_y(IMAGE_SOUTH_LAT)
    return (
        (longitude - IMAGE_WEST_LON) / (IMAGE_EAST_LON - IMAGE_WEST_LON) * FRAME_WIDTH,
        (north - _mercator_y(latitude)) / span * FRAME_HEIGHT,
    )


class FrameWindow(NamedTuple):
    """The rectangle of frame pixels the disc touches, and the disc mask inside it."""

    x0: int
    y0: int
    x1: int
    y1: int
    mask: np.ndarray

    @property
    def box(self) -> tuple[int, int, int, int]:
        """Crop box, in frame coordinates — the frame has no header margin to skip."""
        return (self.x0, self.y0, self.x1, self.y1)


def frame_window(geometry: SampleGeometry) -> FrameWindow:
    """Project the sampled disc onto frame pixels.

    Computed once per geometry and reused for every frame, so per-frame work is just
    "decode, crop, classify".
    """
    cx, cy = to_pixel(geometry.latitude, geometry.longitude)
    # Radius measured from the disc's own north and east edge points, so the
    # projection's local scale is read off the map rather than assumed.
    north_lat, _ = geometry.offset(geometry.radius_km, 0.0)
    _, east_lon = geometry.offset(geometry.radius_km, 90.0)
    radius_x = abs(to_pixel(geometry.latitude, east_lon)[0] - cx)
    radius_y = abs(to_pixel(north_lat, geometry.longitude)[1] - cy)
    radius_px = max(radius_x, radius_y, 0.5)

    x0 = max(0, math.floor(cx - radius_px))
    x1 = min(FRAME_WIDTH, math.ceil(cx + radius_px))
    y0 = max(0, math.floor(cy - radius_px))
    y1 = min(FRAME_HEIGHT, math.ceil(cy + radius_px))
    x1 = max(x1, x0 + 1)
    y1 = max(y1, y0 + 1)

    gx = np.arange(x0, x1, dtype=np.float64) + 0.5
    gy = np.arange(y0, y1, dtype=np.float64) + 0.5
    mask = ((gy[:, None] - cy) ** 2 + (gx[None, :] - cx) ** 2) <= radius_px**2
    if not mask.any():
        # Disc smaller than a pixel: sample the one containing the centre.
        mask[mask.shape[0] // 2, mask.shape[1] // 2] = True
    return FrameWindow(x0, y0, x1, y1, mask)


def run_candidates(now: datetime, attempts: int = MAX_RUN_ATTEMPTS) -> list[datetime]:
    """Run stamps to try, newest first.

    There is no feed to ask — the run times are a fixed 5-minute grid, so they are
    computed rather than discovered. `PUBLICATION_LAG` keeps the newest candidate
    one that has actually been published.
    """
    latest = now - PUBLICATION_LAG
    latest = latest.replace(second=0, microsecond=0)
    latest -= timedelta(minutes=latest.minute % RUN_INTERVAL_MIN)
    return [latest - timedelta(minutes=RUN_INTERVAL_MIN * n) for n in range(attempts)]


def observed_url(stamp: datetime) -> str:
    """URL of the observed MAX_Z composite for a run."""
    return BASE_URL + OBSERVED_PATH.format(stamp=_stamp(stamp))


def forecast_url(stamp: datetime) -> str:
    """URL of the forecast tar for a run — all six frames in one file."""
    return BASE_URL + FORECAST_PATH.format(stamp=_stamp(stamp))


def _stamp(moment: datetime) -> str:
    """`yyyyMMdd.HHmm` in UTC, the only date format CHMI's filenames use."""
    return moment.astimezone(UTC).strftime(STAMP_FORMAT)


class ChmiAdapter:
    """Adapter for the CHMI CZRAD radar nowcast."""

    source_ids = (SOURCE_CHMI,)

    def __init__(self, user_agent: str, cache: SampleCache | None = None) -> None:
        self._user_agent = user_agent
        self._cache = cache
        self._budget = RequestBudget(limit=MAX_REQUESTS_PER_HOUR)
        self._backoff = Backoff()
        self._window: FrameWindow | None = None
        self._window_key: str | None = None
        self._reliability: float | None = None
        self._last: FetchResult | None = None
        self._last_fetch_at: datetime | None = None
        #: None until a geometry has been seen; then a fixed yes/no for that location.
        self._applicable: bool | None = None
        self._applicable_key: str | None = None

    # --- adapter protocol ---------------------------------------------------

    def applicable(self, geometry: SampleGeometry) -> bool:
        """Whether this location is inside the composite at all. Cached per geometry."""
        if self._applicable is None or self._applicable_key != geometry.key:
            self._applicable = covers_geometry(geometry)
            self._applicable_key = geometry.key
            if not self._applicable:
                _LOGGER.debug("CHMI does not cover %s; it will stay silent", geometry.key)
        return self._applicable

    @property
    def budget(self) -> RequestBudget:
        """This adapter's rolling hourly request budget, for the registry to total."""
        return self._budget

    def should_fetch(self, now: datetime) -> bool:
        """Once per published run, and never while a backoff is armed.

        The registry checks coverage before calling this, so a location outside the
        box never reaches here and costs nothing at all.

        The last clause lets a publication-aligned cycle actually fetch: a run
        stamped 12:10 is on the server 18 s later (measured, phase 8), and asking
        "is there a run I do not have" is what turns that into data the same minute
        instead of at the next five-minute cycle.
        """
        if not self._backoff.ready(now):
            return False
        if self._last_fetch_at is None:
            return True
        elapsed = now - self._last_fetch_at
        if elapsed >= timedelta(seconds=MIN_INTERVAL_S):
            return True
        # As in LibreWXR: an aligned cycle may only ever pull a fetch earlier by the
        # settle margin, which shifts the phase without raising the rate.
        settle = timedelta(seconds=publish_settle_s(SOURCE_CHMI))
        return self._next_run_due(now) and elapsed >= timedelta(seconds=MIN_INTERVAL_S) - settle

    def _next_run_due(self, now: datetime) -> bool:
        """Whether a run newer than the one held should be published by now."""
        if self._last is None or not self._last.series:
            return False
        issued = self._last.series[0].issued_at
        return now >= issued + timedelta(seconds=MIN_INTERVAL_S + publish_settle_s(SOURCE_CHMI))

    def cached(self, now: datetime) -> FetchResult:
        """Re-present the last successful result, re-stated as stale once too old."""
        if self._last is None:
            return FetchResult(
                statuses=(SourceStatus(SOURCE_CHMI, STATE_FAILED, detail="no data yet"),)
            )
        return restate(self._last, now)

    def not_applicable(self) -> FetchResult:
        """The result for a location the composite does not reach."""
        return FetchResult(
            statuses=(
                SourceStatus(
                    SOURCE_CHMI,
                    STATE_NOT_APPLICABLE,
                    detail="outside the CHMI radar composite",
                ),
            )
        )

    async def fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        """Fetch the newest run's forecast tar and its observed frame, and sample both."""
        if not self.applicable(geometry):
            return self.not_applicable()
        try:
            result = await self._fetch(session, geometry, now)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # provider-side failures must never break the cycle
            _LOGGER.debug("CHMI fetch failed: %s", err)
            self._backoff.record_failure(now)
            return self._failed(now, str(err))
        self._backoff.record_success()
        self._last = result
        self._last_fetch_at = now
        return result

    # --- implementation -----------------------------------------------------

    def _failed(self, now: datetime, detail: str) -> FetchResult:
        """Failure result that still carries the previous series while it is usable."""
        if self._last is not None and self._last.series:
            series = self._last.series[0]
            if not series.is_stale(now):
                return FetchResult(
                    series=self._last.series,
                    statuses=(
                        SourceStatus(
                            SOURCE_CHMI,
                            STATE_OK,
                            age_s=series.age_s(now),
                            contributed=True,
                            detail=f"reusing cached frames: {detail}",
                        ),
                    ),
                )
        return FetchResult(statuses=(SourceStatus(SOURCE_CHMI, STATE_FAILED, detail=detail),))

    def _frame_window(self, geometry: SampleGeometry) -> FrameWindow:
        if self._window is None or self._window_key != geometry.key:
            self._window = frame_window(geometry)
            self._window_key = geometry.key
            self._reliability = reliability_at(geometry.latitude, geometry.longitude)
            _LOGGER.debug(
                "CHMI is %.0f km from its nearest radar here; weight %.2f",
                nearest_radar_km(geometry.latitude, geometry.longitude),
                self._reliability,
            )
        return self._window

    async def _fetch(
        self, session: ClientSession, geometry: SampleGeometry, now: datetime
    ) -> FetchResult:
        window = self._frame_window(geometry)
        reliability = self._reliability
        assert reliability is not None  # set alongside the window, above

        run, tar_bytes = await self._newest_run(session, now)
        slots = dict(_sample_forecast(tar_bytes, window))
        del tar_bytes

        # The observation extends the series backwards by one step. It is optional:
        # a run whose forecast arrived but whose observed frame did not is still a
        # perfectly good +10…+60 nowcast, and losing it is not worth failing over.
        observed_slot = run
        if observed_slot + timedelta(seconds=STEP_S) == min(slots, default=None):
            value = await self._observed(session, run, window, now)
            if value is not None:
                slots[observed_slot] = value

        if not slots:
            raise RuntimeError("no frames could be sampled")

        series = SourceSeries(
            source_id=SOURCE_CHMI,
            issued_at=run,
            fetched_at=now,
            step_s=STEP_S,
            slots=tuple(sorted(slots.items())),
            cell_km=CELL_KM[SOURCE_CHMI],
            # Not the static weight: this source is measured from a fixed pair of
            # radars, so how much its vote is worth depends on where the user is.
            reliability=reliability,
        )
        stale = series.is_stale(now)
        return FetchResult(
            series=(series,),
            statuses=(
                SourceStatus(
                    SOURCE_CHMI,
                    STATE_STALE if stale else STATE_OK,
                    age_s=series.age_s(now),
                    contributed=not stale,
                    detail=(
                        f"{nearest_radar_km(geometry.latitude, geometry.longitude):.0f} km "
                        f"from the nearest CHMI radar"
                    ),
                ),
            ),
        )

    async def _newest_run(self, session: ClientSession, now: datetime) -> tuple[datetime, bytes]:
        """The newest published run, found by walking the 5-minute grid backwards."""
        last_error = "no run attempted"
        for candidate in run_candidates(now):
            if not self._budget.consume(now):
                raise RuntimeError("hourly request budget exhausted")
            try:
                return candidate, await self._get(session, forecast_url(candidate))
            except asyncio.CancelledError:
                raise
            except Exception as err:  # a run that is not published yet is a 404
                last_error = f"{_stamp(candidate)}: {err}"
                _LOGGER.debug("CHMI run %s unavailable: %s", _stamp(candidate), err)
        raise RuntimeError(f"no published run found ({last_error})")

    async def _observed(
        self, session: ClientSession, run: datetime, window: FrameWindow, now: datetime
    ) -> float | None:
        """Sample the observed frame for a run, or None if it cannot be had."""
        url = observed_url(run)
        cached = self._cache.get(url) if self._cache else None
        if cached is not None:
            return cached
        if not self._budget.consume(now):
            _LOGGER.debug("CHMI budget reached; skipping the observed frame")
            return None
        try:
            raw = await self._get(session, url)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("CHMI observed frame %s unavailable: %s", _stamp(run), err)
            return None
        value = _sample(raw, window)
        del raw
        if self._cache is not None:
            self._cache.set(url, run, value)
        return value

    async def _get(self, session: ClientSession, url: str) -> bytes:
        async with session.get(
            url,
            headers={"User-Agent": self._user_agent, "Accept-Encoding": "gzip"},
            timeout=ClientTimeout(total=_TIMEOUT_S),
        ) as response:
            response.raise_for_status()
            return await response.read()


def _sample_forecast(raw: bytes, window: FrameWindow) -> list[tuple[datetime, float]]:
    """Sample every forecast frame in one run's tar.

    Members are named `{run}/pacz2gmaps3.fct_z_max.{target}.{lead}.png`, so the
    target time comes out of the filename and the frames need no ordering assumption.
    """
    if len(raw) > _TAR_LIMIT_BYTES:
        raise ValueError(f"forecast archive is {len(raw)} bytes, refusing to expand it")

    slots: list[tuple[datetime, float]] = []
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith(".png"):
                continue
            target = _target_of(member.name)
            if target is None:
                continue
            payload = archive.extractfile(member)
            if payload is None:
                continue
            slots.append((target, _sample(payload.read(), window)))

    if not slots:
        raise ValueError("forecast archive held no usable frames")
    slots.sort()
    return slots[:FORECAST_FRAMES]


#: `pacz2gmaps3 . fct_z_max . yyyyMMdd . HHmm . lead . png`
_FORECAST_NAME_PARTS = 6


def _target_of(name: str) -> datetime | None:
    """Target time from a forecast frame's filename, or None if it is not one."""
    parts = name.rsplit("/", 1)[-1].split(".")
    if len(parts) != _FORECAST_NAME_PARTS or parts[1] != "fct_z_max":
        return None
    try:
        return datetime.strptime(f"{parts[2]}.{parts[3]}", STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _sample(raw: bytes, window: FrameWindow) -> float:
    """Reduce one composite PNG to a single mm/h value over the disc.

    Only the disc's own rectangle is converted and held — a few hundred pixels —
    rather than the whole 680x460 composite, so a frame costs the PNG decode buffer
    and nothing else. The decoded buffer is released before the value is returned.
    """
    with Image.open(io.BytesIO(raw)) as image:
        if (image.width, image.height) != (FRAME_WIDTH, FRAME_HEIGHT):
            raise ValueError(
                f"frame is {image.width}x{image.height}, expected {FRAME_WIDTH}x{FRAME_HEIGHT}"
            )
        patch = np.asarray(image.crop(window.box).convert("RGBA"))

    levels = _levels(patch)
    del patch
    inside = levels[window.mask]
    usable = inside[inside >= 0]
    unknown = int(inside.size - usable.size)
    if usable.size == 0 or unknown > MAX_UNKNOWN_FRACTION * inside.size:
        raise ValueError(f"{unknown} of {inside.size} sampled pixels are not on CHMI's palette")
    # `nearest` keeps the result an actually observed palette level, so it maps back
    # to a real reflectivity step instead of interpolating between two of them.
    level = int(np.percentile(usable, PERCENTILE, method="nearest"))
    del levels, inside, usable
    return level_to_mm_per_h(level)


def _levels(rgba: np.ndarray) -> np.ndarray:
    """Classify pixels to palette levels. Transparent is 0; anything unknown is -1.

    Matching is **exact**, not nearest-colour, and that is the point. CHMI's
    `png_masked` variants render precipitation with blending, so their pixels are
    off-palette; under nearest-colour a blended pale violet resolves to the white
    top of the ramp and light drizzle reads as 205 mm/h. Refusing to guess is what
    turns that into a loud failure instead of a confident wrong answer.
    """
    palette = np.asarray(PALETTE_RGB, dtype=np.int32)
    rgb = rgba[..., :3].astype(np.int32)
    # int32, not int16: a single channel can differ by 252, and 252 squared already
    # overflows int16 — which would silently make the furthest colour the nearest.
    distance = ((rgb[..., None, :] - palette[None, None, :, :]) ** 2).sum(axis=-1)
    levels = distance.argmin(axis=-1).astype(np.int16)
    exact = distance.min(axis=-1) == 0

    opaque = rgba[..., 3] > 0
    outline = (rgb == np.asarray(DOMAIN_OUTLINE_RGB, dtype=np.int32)).all(axis=-1)
    levels = np.where(opaque, levels, 0)  # transparent == no echo
    return np.where(opaque & (~exact | outline), -1, levels).astype(np.int16)


__all__ = [
    "FORECAST_FRAMES",
    "MAX_REQUESTS_PER_HOUR",
    "RADAR_SITES",
    "ChmiAdapter",
    "FrameWindow",
    "covers",
    "covers_geometry",
    "forecast_url",
    "frame_window",
    "level_to_mm_per_h",
    "nearest_radar_km",
    "observed_url",
    "range_factor",
    "reliability_at",
    "run_candidates",
    "to_pixel",
]
