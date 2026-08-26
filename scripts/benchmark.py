"""Measure what one update cycle actually costs: CPU, memory, requests, loop stalls.

Phase 8. `docs/ARCHITECTURE.md` § Resource budget states ceilings; this script is
what turns them from estimates into measurements. It drives the **real** adapters
and the **real** engine over the recorded fixtures in `tests/fixtures/`, so what it
times is the code that runs in a live Home Assistant — only the network is
substituted.

    python scripts/benchmark.py                      # both profiles, 16 cycles each
    python scripts/benchmark.py --profile bielsko --json out.json

Two profiles, because the cost of a cycle depends on where the user lives:

* **warszawa** — outside the CHMI composite: LibreWXR tiles plus Open-Meteo. The
  common case.
* **bielsko** — inside it: everything above plus a CHMI run (a 92 KB tar of six
  680x460 composites, and one observed frame) every five minutes. The worst case.

Each profile runs a sequence of cycles at the real cadence, starting cold. That is
deliberate: a cold cycle and a warm one cost very different amounts, and the way to
learn the difference is to let the cache fill the way it fills in service rather
than to construct a warm state by hand.

Three things are measured per cycle:

* **CPU time** — `time.process_time`, which excludes the (fake) network wait.
* **Peak RSS** over the cycle, sampled from `/proc/self/statm` by a background
  thread. Not `tracemalloc`: numpy and Pillow allocate outside the Python
  allocator, and those buffers are exactly the ones the memory budget is about.
* **The longest event-loop stall** — how long the loop went without being able to
  run a ready task. Sampling a radar frame is synchronous CPU work inside an async
  method, so this is the number that says whether it is polite enough to run in
  Home Assistant's event loop.

No Home Assistant import is needed to run this — see `_load_integration`.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import statistics
import sys
import tarfile
import threading
import time
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"

#: Public landmarks, never the maintainer's own coordinates: Warszawa is outside
#: the CHMI composite, Bielsko-Biala inside it (the same pair the test suite uses).
WARSZAWA = (52.2297, 21.0122)
BIELSKO = (49.8224, 19.0584)

RADIUS_KM = 5.0

#: The walk the cycles are about, and where the sequence starts: one full active
#: window is `earlier_margin + lead_time` before it, i.e. 90 minutes.
WALK_START = datetime(2026, 8, 26, 17, 0, tzinfo=UTC)
WINDOW_START = WALK_START - timedelta(minutes=90)


def _load_integration() -> types.ModuleType:
    """Import the integration's modules without executing its HA entry point.

    `custom_components/walk_the_dog/__init__.py` is the Home Assistant setup module
    and imports Home Assistant. Nothing measured here needs it — the adapters, the
    cache and the engine are all deliberately free of HA imports — so the package is
    registered under its own name with a synthetic module object, and the real
    `__init__.py` is never run. Relative imports inside the package resolve against
    it exactly as usual.

    The payoff is that the benchmark runs anywhere numpy, Pillow and aiohttp do:
    on Windows, and in a small constrained container that has no reason to carry a
    2 GB test image.
    """
    sys.path.insert(0, str(REPO_ROOT))
    namespace = types.ModuleType("custom_components")
    namespace.__path__ = [str(REPO_ROOT / "custom_components")]  # type: ignore[attr-defined]
    package = types.ModuleType("custom_components.walk_the_dog")
    package.__path__ = [str(REPO_ROOT / "custom_components" / "walk_the_dog")]  # type: ignore[attr-defined]
    sys.modules.setdefault("custom_components", namespace)
    sys.modules.setdefault("custom_components.walk_the_dog", package)
    return package


_load_integration()

cache_module = importlib.import_module("custom_components.walk_the_dog.cache")
engine_module = importlib.import_module("custom_components.walk_the_dog.engine")
sources_module = importlib.import_module("custom_components.walk_the_dog.sources")
base_module = importlib.import_module("custom_components.walk_the_dog.sources.base")

SampleCache = cache_module.SampleCache
SampleGeometry = base_module.SampleGeometry
SourceRegistry = sources_module.SourceRegistry
Search = engine_module.Search
build_consensus = engine_module.build_consensus
evaluation_slots = engine_module.evaluation_slots
recommend = engine_module.recommend

USER_AGENT = "walk_the_dog/benchmark (+https://github.com/zyndata/walk-the-dog)"

#: How long after a frame's own timestamp it can actually be read, per source.
#: **Measured** with `scripts/measure_publish_lag.py` on 2026-08-26 (see STATE.md,
#: phase 8): LibreWXR between 78 s and 158 s, CHMI 18 s on all but one run. The
#: values below are a little above each median, and the fixture feed honours them —
#: so a simulated cycle sees exactly the frames a real one would have seen at that
#: moment, and no more.
PUBLICATION_LAG_S = {"librewxr": 120, "chmi": 20}

#: What the coordinator uses, restated here so the benchmark does not need to
#: import it (`coordinator.py` is the one module that does need Home Assistant).
CYCLE = timedelta(minutes=10)
SPRINT = timedelta(minutes=5)
SEARCH = Search(
    duration=timedelta(minutes=30),
    earlier_margin=timedelta(minutes=60),
    later_margin=timedelta(minutes=30),
)
THRESHOLD = "light"


# --- the substituted network -------------------------------------------------------


class NotPublishedError(Exception):
    """What a fixture 404 looks like: a frame whose file is not on the server yet."""


class _FixtureResponse:
    """The half of `aiohttp`'s response API the adapters actually use."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.status = 200

    def raise_for_status(self) -> None:
        """Recorded fixtures always succeed; failure paths have their own tests."""

    async def read(self) -> bytes:
        """The recorded body."""
        return self._body

    async def json(self, content_type: str | None = None) -> Any:
        """The recorded body, parsed."""
        return json.loads(self._body)


class _FixtureRequest:
    """`session.get(...)` result: an async context manager, as aiohttp returns."""

    def __init__(self, session: FixtureSession, url: str) -> None:
        self._session = session
        self._url = url

    async def __aenter__(self) -> _FixtureResponse:
        body = self._session.serve(self._url)
        return _FixtureResponse(body)

    async def __aexit__(self, *_exc: object) -> None:
        return None


class FixtureSession:
    """Serves the recorded fixtures, and counts what was asked for.

    Two of them are rebuilt per cycle rather than replayed verbatim: the LibreWXR
    index and the CHMI forecast archive both carry timestamps that decide *which*
    frames are new, and replaying a frozen copy would make every cycle after the
    first a total cache hit — measuring a situation that never happens.
    """

    def __init__(
        self,
        now_provider: Callable[[], datetime] | None = None,
        lag_s: dict[str, int] | None = None,
    ) -> None:
        #: Every request, stamped with the simulated time it was made at — which is
        #: what lets `tests/test_performance.py` count a rolling hour.
        self.log: list[tuple[datetime, str]] = []
        self.bytes_read = 0
        self.now = WINDOW_START
        self._now = now_provider or (lambda: self.now)
        self._lag_s = dict(PUBLICATION_LAG_S if lag_s is None else lag_s)
        self._tile = (FIXTURES / "librewxr" / "tile_wet.png").read_bytes()
        self._open_meteo = (FIXTURES / "open_meteo" / "wet.json").read_bytes()
        self._metno = (FIXTURES / "met_norway" / "compact.json").read_bytes()
        self._observed = (FIXTURES / "chmi" / "observed.png").read_bytes()
        self._forecast_frames = _recorded_forecast_frames()
        self._index_template = json.loads(
            (FIXTURES / "librewxr" / "weather-maps.json").read_text(encoding="utf-8")
        )

    # `session.get` is a plain function in aiohttp too — it returns the context
    # manager rather than being awaited itself.
    def get(self, url: str, **kwargs: Any) -> _FixtureRequest:
        """Answer one request from the fixtures."""
        params = kwargs.get("params")
        full = url if not params else f"{url}?{'&'.join(sorted(params))}"
        return _FixtureRequest(self, full)

    def serve(self, url: str) -> bytes:
        """Route a URL to its fixture, recording the request."""
        self.log.append((self._now(), url))
        body = self._body_for(url)
        self.bytes_read += len(body)
        return body

    def _body_for(self, url: str) -> bytes:
        if "weather-maps.json" in url:
            return self._index_for(self._now())
        if "/v2/radar/" in url:
            return self._tile
        if "open-meteo" in url:
            return self._open_meteo
        if "api.met.no" in url:
            return self._metno
        if "fct_z_max" in url:
            run = _run_of(url)
            self._require_published(run, "chmi")
            return _forecast_tar(self._forecast_frames, run)
        if "z_max3d" in url:
            self._require_published(_observed_run_of(url), "chmi")
            return self._observed
        raise AssertionError(f"no fixture for {url}")

    def _require_published(self, stamp: datetime, source: str) -> None:
        """Refuse a run that is not on the server yet, the way a 404 would."""
        available = stamp + timedelta(seconds=self._lag_s.get(source, 0))
        if self._now() < available:
            raise NotPublishedError(f"{stamp:%H:%M} is published at {available:%H:%M:%S}")

    def _index_for(self, now: datetime) -> bytes:
        """A `weather-maps.json` whose frames are current for `now`.

        Frame paths are `/v2/radar/<epoch of the frame's valid time>`, exactly as
        the recorded index has them, so successive cycles overlap the way the real
        feed overlaps: one frame at the far end of the nowcast is genuinely new and
        the rest are already in the cache.
        """
        # Only frames that have actually been published: the newest stamp is one
        # whose publication lag has already elapsed.
        newest = int((now.timestamp() - self._lag_s.get("librewxr", 0)) // 600) * 600
        index = dict(self._index_template)
        index["radar"] = {
            "past": [
                {"time": newest - offset * 600, "path": f"/v2/radar/{newest - offset * 600}"}
                for offset in reversed(range(12))
            ],
            "nowcast": [
                {"time": newest + step * 600, "path": f"/v2/radar/{newest + step * 600}"}
                for step in range(1, 7)
            ],
        }
        return json.dumps(index).encode("utf-8")

    def counts(self) -> dict[str, int]:
        """Requests per source, for the request-budget half of the report."""
        counts = {"librewxr": 0, "chmi": 0, "open_meteo": 0, "metno": 0}
        for _when, url in self.log:
            if "librewxr" in url or "/v2/radar/" in url:
                counts["librewxr"] += 1
            elif "chmi" in url:
                counts["chmi"] += 1
            elif "open-meteo" in url:
                counts["open_meteo"] += 1
            elif "met.no" in url:
                counts["metno"] += 1
        return counts


def _recorded_forecast_frames() -> list[bytes]:
    """The six composite PNGs inside the recorded CHMI archive."""
    raw = (FIXTURES / "chmi" / "forecast.tar").read_bytes()
    frames = []
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if not member.isfile() or not member.name.endswith(".png"):
                continue
            payload = archive.extractfile(member)
            if payload is not None:
                frames.append(payload.read())
    return frames


def _run_of(url: str) -> datetime:
    """The run stamp a CHMI forecast URL is asking for."""
    stamp = url.rsplit("fct_z_max.", 1)[1].split(".ft60s10", maxsplit=1)[0]
    return datetime.strptime(stamp, "%Y%m%d.%H%M").replace(tzinfo=UTC)


def _observed_run_of(url: str) -> datetime:
    """The run stamp a CHMI observed-frame URL is asking for."""
    stamp = url.rsplit("z_max3d.", 1)[1].rsplit(".", 2)[0]
    return datetime.strptime(stamp, "%Y%m%d.%H%M").replace(tzinfo=UTC)


def _forecast_tar(frames: list[bytes], run: datetime) -> bytes:
    """Rebuild the recorded archive with member names stamped for `run`.

    The real bytes of the real composites, under the names the run being asked for
    would carry — so the adapter reads a genuinely new set of frames every five
    minutes, which is what actually happens and what the cache cannot help with.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for step, payload in enumerate(frames, start=1):
            target = run + timedelta(minutes=10 * step)
            name = (
                f"{run:%Y%m%d.%H%M}/pacz2gmaps3.fct_z_max."
                f"{target:%Y%m%d.%H%M}.ft{10 * step:02d}.png"
            )
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


# --- probes ------------------------------------------------------------------------


class RssSampler:
    """Peak resident memory over a window, sampled from `/proc/self/statm`.

    A thread rather than a decorator around the allocator: the buffers that matter
    are Pillow's decode target and numpy's arrays, and neither goes through the
    Python allocator that `tracemalloc` can see.
    """

    INTERVAL_S = 0.002

    def __init__(self) -> None:
        self._statm = Path("/proc/self/statm")
        self.available = self._statm.exists()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_kb = 0

    def rss_kb(self) -> int:
        """Current resident set size in KiB, or 0 where /proc is not available."""
        if not self.available:
            return 0
        fields = self._statm.read_text(encoding="ascii").split()
        return int(fields[1]) * 4  # pages of 4 KiB

    def __enter__(self) -> RssSampler:
        """Start sampling."""
        self.peak_kb = self.rss_kb()
        if self.available:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop sampling."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak_kb = max(self.peak_kb, self.rss_kb())
            time.sleep(self.INTERVAL_S)


class LoopStallProbe:
    """The longest the event loop went without running a ready task.

    A task that asks to be woken every 5 ms and records how late it actually was.
    Synchronous work inside a coroutine — decoding a radar frame, say — shows up
    here as a stall of its own length, which is precisely the "no blocking the
    event loop" question stated as a number.
    """

    TICK_S = 0.005

    def __init__(self) -> None:
        self.max_stall_s = 0.0
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> LoopStallProbe:
        """Start ticking."""
        self.max_stall_s = 0.0
        self._running = True
        self._task = asyncio.get_running_loop().create_task(self._tick())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Stop ticking."""
        self._running = False
        if self._task is not None:
            await self._task
            self._task = None

    async def _tick(self) -> None:
        while self._running:
            before = time.perf_counter()
            await asyncio.sleep(self.TICK_S)
            self.max_stall_s = max(self.max_stall_s, time.perf_counter() - before - self.TICK_S)


# --- the cycles --------------------------------------------------------------------


@dataclass
class CycleResult:
    """What one cycle cost."""

    index: int
    now: datetime
    wall_ms: float
    cpu_ms: float
    stall_ms: float
    rss_peak_kb: int
    rss_delta_kb: int
    requests: int
    bytes_read: int


@dataclass
class ProfileResult:
    """Every cycle of one profile, plus what was left behind afterwards."""

    name: str
    cycles: list[CycleResult] = field(default_factory=list)
    request_counts: dict[str, int] = field(default_factory=dict)
    cache_entries: int = 0
    cache_bytes: int = 0
    rss_start_kb: int = 0
    rss_end_kb: int = 0

    def summary(self) -> dict[str, Any]:
        """Cold cycle, warm cycles and totals — the shape the report prints."""
        cold = self.cycles[0]
        warm = self.cycles[1:]
        return {
            "profile": self.name,
            "cycles": len(self.cycles),
            "cold": _cycle_summary([cold]),
            "warm": _cycle_summary(warm),
            "requests_total": sum(c.requests for c in self.cycles),
            "requests_by_source": self.request_counts,
            "bytes_total": sum(c.bytes_read for c in self.cycles),
            "cache_entries": self.cache_entries,
            "cache_bytes": self.cache_bytes,
            "rss_start_kb": self.rss_start_kb,
            "rss_end_kb": self.rss_end_kb,
            "rss_growth_kb": self.rss_end_kb - self.rss_start_kb,
        }


def _cycle_summary(cycles: list[CycleResult]) -> dict[str, Any]:
    """Median and worst case over a set of cycles."""
    if not cycles:
        return {}
    return {
        "count": len(cycles),
        "cpu_ms_median": round(statistics.median(c.cpu_ms for c in cycles), 1),
        "cpu_ms_max": round(max(c.cpu_ms for c in cycles), 1),
        "wall_ms_median": round(statistics.median(c.wall_ms for c in cycles), 1),
        "stall_ms_max": round(max(c.stall_ms for c in cycles), 1),
        "rss_delta_kb_max": max(c.rss_delta_kb for c in cycles),
        "requests_median": statistics.median(c.requests for c in cycles),
        "requests_max": max(c.requests for c in cycles),
    }


async def _warm_up() -> None:
    """Decode one frame of each kind before the clock starts.

    Pillow builds its codec tables and numpy its dispatch caches on first use, and
    that one-off cost lands wherever it happens to fall — in service, on the first
    cycle after a Home Assistant restart, not on every cold window. Paying it here
    keeps "cold" meaning "empty cache" rather than "empty cache plus a library
    starting up"; `--no-warmup` measures the other case.
    """
    geometry = SampleGeometry(latitude=BIELSKO[0], longitude=BIELSKO[1], radius_km=RADIUS_KM)
    session = FixtureSession()
    registry = SourceRegistry(USER_AGENT)
    await registry.async_fetch(session, geometry, WINDOW_START)


async def run_profile(name: str, location: tuple[float, float], cycles: int) -> ProfileResult:
    """Run one profile's sequence of cycles, starting cold."""
    geometry = SampleGeometry(latitude=location[0], longitude=location[1], radius_km=RADIUS_KM)
    cache = SampleCache(geometry.key)
    registry = SourceRegistry(USER_AGENT, cache=cache)
    session = FixtureSession()
    sampler = RssSampler()
    result = ProfileResult(name=name, rss_start_kb=sampler.rss_kb())

    sprint_from = WALK_START - timedelta(minutes=20)
    now = WINDOW_START
    for index in range(cycles):
        session.now = now
        before_requests = len(session.log)
        before_bytes = session.bytes_read
        baseline_kb = sampler.rss_kb()

        with sampler:
            async with LoopStallProbe() as probe:
                wall_started = time.perf_counter()
                cpu_started = time.process_time()
                await one_cycle(registry, session, geometry, cache, now)
                wall_ms = (time.perf_counter() - wall_started) * 1000
                cpu_ms = (time.process_time() - cpu_started) * 1000
                await asyncio.sleep(0)  # let the probe record the final stretch

        result.cycles.append(
            CycleResult(
                index=index,
                now=now,
                wall_ms=wall_ms,
                cpu_ms=cpu_ms,
                stall_ms=probe.max_stall_s * 1000,
                rss_peak_kb=sampler.peak_kb,
                rss_delta_kb=max(0, sampler.peak_kb - baseline_kb),
                requests=len(session.log) - before_requests,
                bytes_read=session.bytes_read - before_bytes,
            )
        )
        # The last twenty minutes before setting off run at the sprint cadence,
        # and only where a source publishes fast enough to make it worth it.
        sprinting = name == "bielsko" and sprint_from <= now < WALK_START
        now += SPRINT if sprinting else CYCLE

    result.request_counts = session.counts()
    result.cache_entries = len(cache)
    result.cache_bytes = len(json.dumps(cache.as_dict()).encode("utf-8"))
    result.rss_end_kb = sampler.rss_kb()
    return result


async def one_cycle(
    registry: Any,
    session: FixtureSession,
    geometry: Any,
    cache: Any,
    now: datetime,
) -> Any:
    """Exactly what `WalkCoordinator._cycle` does, minus Home Assistant.

    Kept in step with `coordinator.py` by hand: it is four calls, and importing the
    coordinator would drag in Home Assistant for no measurement gain.
    """
    series, statuses = await registry.async_fetch(session, geometry, now)
    cache.evict_expired(now)
    consensus = build_consensus(
        series,
        statuses,
        slots=evaluation_slots(WALK_START, SEARCH),
        threshold=THRESHOLD,
        now=now,
    )
    return recommend(consensus, scheduled_start=WALK_START, search=SEARCH, now=now)


# --- reporting ---------------------------------------------------------------------


def _report(results: list[ProfileResult], *, warmed_up: bool) -> str:
    """The human-readable table."""
    lines = [
        f"Python {sys.version.split()[0]} on {sys.platform}"
        + ("" if warmed_up else "  (no warm-up: cycle 0 carries library start-up)"),
        f"walk start {WALK_START:%Y-%m-%d %H:%M} UTC, window opens {WINDOW_START:%H:%M}, "
        f"radius {RADIUS_KM:.0f} km",
        "",
    ]
    for result in results:
        summary = result.summary()
        cold, warm = summary["cold"], summary["warm"]
        lines += [
            f"--- {result.name} ---",
            f"  cycles              {summary['cycles']}",
            f"  cold cycle          cpu {cold['cpu_ms_median']:.1f} ms, "
            f"peak RSS +{cold['rss_delta_kb_max']} KiB, "
            f"stall {cold['stall_ms_max']:.1f} ms, {cold['requests_max']} requests",
            f"  warm cycle (median) cpu {warm['cpu_ms_median']:.1f} ms, "
            f"{warm['requests_median']:.0f} requests",
            f"  warm cycle (worst)  cpu {warm['cpu_ms_max']:.1f} ms, "
            f"peak RSS +{warm['rss_delta_kb_max']} KiB, "
            f"stall {warm['stall_ms_max']:.1f} ms, {warm['requests_max']} requests",
            f"  requests            {summary['requests_total']} total "
            f"({', '.join(f'{k} {v}' for k, v in summary['requests_by_source'].items() if v)}), "
            f"{summary['bytes_total'] / 1024:.0f} KiB",
            f"  cache               {summary['cache_entries']} entries, "
            f"{summary['cache_bytes']} bytes persisted",
            f"  RSS                 {summary['rss_start_kb']} -> {summary['rss_end_kb']} KiB "
            f"({summary['rss_growth_kb']:+d} KiB over the run)",
            "",
        ]
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> int:
    profiles = {"warszawa": WARSZAWA, "bielsko": BIELSKO}
    chosen = list(profiles) if args.profile == "both" else [args.profile]
    if args.warmup:
        await _warm_up()
    results = [await run_profile(name, profiles[name], args.cycles) for name in chosen]

    print(_report(results, warmed_up=args.warmup))
    if args.json is not None:
        payload = {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "warmed_up": args.warmup,
            "profiles": [result.summary() for result in results],
        }
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


def main() -> int:
    """Parse arguments and run the benchmark."""
    parser = argparse.ArgumentParser(description="Measure the cost of an update cycle.")
    parser.add_argument("--profile", choices=("warszawa", "bielsko", "both"), default="both")
    parser.add_argument("--cycles", type=int, default=16, help="cycles per profile")
    parser.add_argument("--json", type=Path, default=None, help="write the summary as JSON here")
    parser.add_argument(
        "--no-warmup",
        dest="warmup",
        action="store_false",
        help="include one-off library start-up cost in the first cycle measured",
    )
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
