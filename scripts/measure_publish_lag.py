"""Measure how long after its nominal stamp a frame actually becomes fetchable.

Phase 8. `PUBLISH_SETTLE_S` — the grace the coordinator leaves between a frame's
stamp and asking for it — was an estimate; this script is what replaces it with a
measurement. It watches the two fast sources for a while and reports, per source,
the lag between a frame's own timestamp and the moment it could first be read.

It makes **live** requests, so it is never part of the test suite: run it by hand
when the number needs re-checking. The volume is deliberately small — one small
JSON poll and one HEAD every `--interval` seconds, and the HEAD is only sent while
a run is actually expected.

    python scripts/measure_publish_lag.py --minutes 50 --out lag.json

Nothing here reads the user's configuration: the LibreWXR index carries no
location, and the CHMI probe asks only whether a run file exists.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: Mirrors of the two adapter constants this script needs. They are copied rather
#: than imported because importing the integration pulls in Home Assistant, numpy
#: and Pillow, and this tool has to be runnable with a bare Python on either OS —
#: `tests/test_performance.py` fails if a copy ever drifts from its original.
LIBREWXR_INDEX_URL = "https://api.librewxr.net/public/weather-maps.json"
CHMI_FORECAST_URL = (
    "https://opendata.chmi.cz/meteorology/weather/radar/composite/fct_maxz/png/"
    "pacz2gmaps3.fct_z_max.{stamp}.ft60s10.tar"
)
RUN_INTERVAL_MIN = 5

#: Give up on a run that has not appeared within this long and start watching the
#: next one — otherwise one skipped publication would stall the measurement.
CHMI_GIVE_UP = timedelta(minutes=5)

USER_AGENT = "walk_the_dog/measure (+https://github.com/zyndata/walk-the-dog)"
TIMEOUT_S = 20
HTTP_OK = 200


def _get(url: str, *, method: str = "GET") -> tuple[int, bytes]:
    """One request, returning `(status, body)`; a 404 is an answer, not an error."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, b""


@dataclass
class Observations:
    """Lags seen for one source, in seconds."""

    name: str
    lags_s: list[float] = field(default_factory=list)
    #: What the source stamps a frame with, for the report's audit trail.
    stamps: list[str] = field(default_factory=list)

    def record(self, stamp: datetime, seen_at: float) -> None:
        """Note that a frame stamped `stamp` was first readable at `seen_at`."""
        lag = seen_at - stamp.timestamp()
        self.lags_s.append(lag)
        self.stamps.append(stamp.isoformat())
        print(f"  {self.name}: frame {stamp:%H:%M} readable after {lag:6.1f} s", flush=True)

    def summary(self) -> dict[str, Any]:
        """Median, worst and count — the three numbers the decision needs."""
        if not self.lags_s:
            return {"source": self.name, "frames": 0}
        ordered = sorted(self.lags_s)
        return {
            "source": self.name,
            "frames": len(ordered),
            "min_s": round(ordered[0], 1),
            "median_s": round(statistics.median(ordered), 1),
            "max_s": round(ordered[-1], 1),
            "stamps": self.stamps,
        }


def _librewxr_newest(body: bytes) -> datetime | None:
    """Newest past frame time from the index, or None when it cannot be read."""
    index = json.loads(body)
    past = (index.get("radar") or {}).get("past") or []
    times = [int(frame["time"]) for frame in past if isinstance(frame.get("time"), int | float)]
    if not times:
        return None
    return datetime.fromtimestamp(max(times), tz=UTC)


def _forecast_url(stamp: datetime) -> str:
    """URL of the forecast archive for one CHMI run — the file we wait to appear."""
    return CHMI_FORECAST_URL.format(stamp=stamp.astimezone(UTC).strftime("%Y%m%d.%H%M"))


def _next_chmi_stamp(now: datetime) -> datetime:
    """The next run stamp on CHMI's fixed 5-minute grid."""
    floor = now.replace(second=0, microsecond=0)
    floor -= timedelta(minutes=floor.minute % RUN_INTERVAL_MIN)
    return floor + timedelta(minutes=RUN_INTERVAL_MIN)


def measure(minutes: int, interval_s: int) -> dict[str, Any]:
    """Watch both fast sources for `minutes`, returning the per-source summary."""
    librewxr = Observations("librewxr")
    chmi = Observations("chmi")

    started = time.time()
    deadline = started + minutes * 60
    # Whatever is already published when we start says nothing about *when* it was
    # published, so the first observation of each source only establishes a baseline.
    seen_librewxr: datetime | None = None
    awaited_chmi: datetime | None = None

    print(
        f"Watching for {minutes} min, polling every {interval_s} s "
        f"(started {datetime.now(UTC):%H:%M:%S} UTC)",
        flush=True,
    )
    while time.time() < deadline:
        loop_started = time.time()
        now = datetime.now(UTC)

        status, body = _get(LIBREWXR_INDEX_URL)
        if status == HTTP_OK:
            newest = _librewxr_newest(body)
            if newest is not None and newest != seen_librewxr:
                if seen_librewxr is not None:
                    librewxr.record(newest, time.time())
                seen_librewxr = newest

        # CHMI publishes on a fixed grid, so the probe is aimed at one stamp at a
        # time and only sent once that stamp has passed — a HEAD asking for a run
        # that cannot exist yet would be a wasted request.
        if awaited_chmi is None:
            awaited_chmi = _next_chmi_stamp(now)
        elif now >= awaited_chmi:
            status, _ = _get(_forecast_url(awaited_chmi), method="HEAD")
            if status == HTTP_OK:
                chmi.record(awaited_chmi, time.time())
                awaited_chmi = _next_chmi_stamp(now)
            elif now - awaited_chmi > CHMI_GIVE_UP:
                print(f"  chmi: run {awaited_chmi:%H:%M} never appeared", flush=True)
                awaited_chmi = _next_chmi_stamp(now)

        elapsed = time.time() - loop_started
        time.sleep(max(0.0, interval_s - elapsed))

    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "duration_min": minutes,
        "poll_interval_s": interval_s,
        "sources": [librewxr.summary(), chmi.summary()],
    }


def main() -> int:
    """Run the measurement and print (optionally save) the summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=50, help="how long to watch")
    parser.add_argument("--interval", type=int, default=20, help="seconds between polls")
    parser.add_argument("--out", type=Path, default=None, help="write the summary as JSON here")
    args = parser.parse_args()

    summary = measure(args.minutes, args.interval)
    text = json.dumps(summary, indent=2)
    print(text, flush=True)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
