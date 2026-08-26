"""What a day of running this integration actually costs a provider.

Phase 8. `docs/DATA_SOURCES.md` § Request budget and `docs/ARCHITECTURE.md`
§ Resource budget both promise numbers — requests per active hour, requests per
day, nothing at all in between. These tests hold the whole machine to those
promises: a full simulated day of four walks, driven through the real coordinator,
the real adapters and their real budgets, with only the network replaced by the
recorded fixtures.

The fixture-serving session is `scripts/benchmark.py`'s, deliberately: the same
substitution measured the per-cycle cost, and one of the two would drift if they
were written twice. What the benchmark cannot check — because it does not run Home
Assistant — is *when* the coordinator decides to fetch, and that is what a day is
for.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.walk_the_dog.const import (
    CONF_EARLIER_MARGIN_MIN,
    CONF_FIRE_EVENT,
    CONF_INTENSITY_THRESHOLD,
    CONF_LATER_MARGIN_MIN,
    CONF_LOCATION,
    CONF_RADIUS_KM,
    CONF_SCHEDULE,
    CONF_SCHEDULE_MODE,
    CONF_WALK_DURATION_MIN,
    DOMAIN,
    INTENSITY_THRESHOLD_LIGHT,
    SCHEDULE_MODE_DAILY,
)
from custom_components.walk_the_dog.sources import chmi, librewxr, open_meteo
from custom_components.walk_the_dog.sources.base import SampleGeometry

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_script(name: str) -> Any:
    """Import a file from `scripts/`, which is a folder of tools, not a package."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"walk_the_dog_scripts.{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


benchmark = _load_script("benchmark")
publish_lag = _load_script("measure_publish_lag")

#: Four walks — the busiest schedule the request budget was sized for.
WALK_TIMES = ("07:00", "12:00", "17:00", "21:00")

DAY_START = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)

#: How far either side of a scheduled walk a request may legitimately fall:
#: `earlier_margin + lead_time` before it, and `later_margin + duration` after.
WINDOW_BEFORE = timedelta(minutes=90)
WINDOW_AFTER = timedelta(minutes=60)

#: The ceilings from docs/DATA_SOURCES.md § Request budget, restated per profile.
BUDGET = {
    "warszawa": {"per_day": 200, "per_hour": 28},
    "bielsko": {"per_day": 380, "per_hour": 58},
}


def _entry(location: tuple[float, float]) -> MockConfigEntry:
    """A config entry with four daily walks at the given location."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Walk the dog",
        data={CONF_LOCATION: {"latitude": location[0], "longitude": location[1]}},
        options={
            CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY,
            CONF_SCHEDULE: {"all": list(WALK_TIMES)},
            CONF_RADIUS_KM: 5.0,
            CONF_INTENSITY_THRESHOLD: INTENSITY_THRESHOLD_LIGHT,
            CONF_EARLIER_MARGIN_MIN: 60,
            CONF_LATER_MARGIN_MIN: 30,
            CONF_WALK_DURATION_MIN: 30,
            CONF_FIRE_EVENT: False,
        },
        version=1,
    )


@dataclass
class Day:
    """What a simulated day did: every request, and every cycle that ran."""

    session: Any
    cycles: list[datetime] = field(default_factory=list)

    @property
    def log(self) -> list[tuple[datetime, str]]:
        """The requests, as the fixture session recorded them."""
        return self.session.log


async def _simulate_day(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, location: tuple[float, float]
) -> Any:
    """Run one whole day minute by minute, returning the session that served it.

    A minute is the finest the coordinator ever asks for — its wakeups land on the
    ten-minute grid, on the five-minute sprint, and on a publication-aligned moment
    a minute past a frame's stamp — so stepping in minutes reproduces the real
    sequence of cycles rather than an approximation of it.
    """
    await hass.config.async_set_time_zone("UTC")
    entry = _entry(location)
    entry.add_to_hass(hass)
    session = benchmark.FixtureSession(now_provider=lambda: _utcnow(hass))
    day = Day(session=session)

    with patch(
        "custom_components.walk_the_dog.coordinator.async_get_clientsession",
        return_value=session,
    ):
        freezer.move_to(DAY_START)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # Every published update is one cycle — including the cheap ones that made
        # no request, which is what the cycle half of the resource budget counts.
        entry.runtime_data.async_add_listener(lambda: day.cycles.append(_utcnow(hass)))

        moment = DAY_START
        while moment < DAY_END:
            moment += timedelta(minutes=1)
            freezer.move_to(moment)
            async_fire_time_changed(hass, moment)
            await hass.async_block_till_done()

    return day


def _utcnow(hass: HomeAssistant) -> datetime:
    """The frozen clock, read the way the integration reads it."""
    from homeassistant.util import dt as dt_util  # noqa: PLC0415

    return dt_util.utcnow()


def _source_of(url: str) -> str:
    """Which adapter a URL belongs to — the same routing the session counts by."""
    if "librewxr" in url or "/v2/radar/" in url:
        return "librewxr"
    if "chmi" in url:
        return "chmi"
    if "open-meteo" in url:
        return "open_meteo"
    return "metno"


def _busiest_hour(times: list[datetime]) -> int:
    """Most requests in any rolling 60-minute window."""
    ordered = sorted(times)
    busiest = 0
    for index, start in enumerate(ordered):
        cutoff = start + timedelta(hours=1)
        count = 0
        for later in ordered[index:]:
            if later >= cutoff:
                break
            count += 1
        busiest = max(busiest, count)
    return busiest


@pytest.mark.parametrize(
    ("profile", "location"),
    [("warszawa", benchmark.WARSZAWA), ("bielsko", benchmark.BIELSKO)],
)
async def test_a_simulated_day_stays_inside_the_request_budget(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    profile: str,
    location: tuple[float, float],
) -> None:
    """Four walks, 24 hours, every request counted against the published budget."""
    day = await _simulate_day(hass, freezer, location)
    session = day.session
    times = [when for when, _ in session.log]
    budget = BUDGET[profile]
    busiest = _busiest_hour(times)

    # Logged rather than merely asserted: these are the numbers
    # docs/ARCHITECTURE.md § Resource budget quotes, and re-running the suite with
    # `--log-cli-level=INFO` is how they are re-measured.
    _LOGGER.info(
        "%s: %d requests over the day (%s), busiest hour %d, %.0f KiB transferred",
        profile,
        len(times),
        ", ".join(f"{name} {count}" for name, count in session.counts().items() if count),
        busiest,
        session.bytes_read / 1024,
    )
    _LOGGER.info(
        "  %d cycles over the day, busiest hour %d",
        len(day.cycles),
        _busiest_hour(day.cycles),
    )

    for source in ("librewxr", "chmi", "open_meteo"):
        _LOGGER.info(
            "  %s: busiest hour %d",
            source,
            _busiest_hour([when for when, url in session.log if _source_of(url) == source]),
        )

    assert times, "the simulated day made no request at all"
    assert len(times) <= budget["per_day"]
    assert busiest <= budget["per_hour"]
    # docs/ARCHITECTURE.md § Resource budget: 6 cycles in a typical active hour,
    # never more than 24 — the ceiling that the sprint and the aligned wakeups are
    # counted against.
    assert _busiest_hour(day.cycles) <= 24

    # Each adapter also polices its own hourly ceiling, and reaching one would mean
    # frames going unsampled rather than a budget kept — so the day has to stay
    # *below* them, not merely inside them.
    # LibreWXR's ceiling is a function of the geometry, so the comparison has to be
    # against the one this location actually gets.
    tiles = librewxr.DiscMask(
        SampleGeometry(latitude=location[0], longitude=location[1], radius_km=5.0)
    ).tile_count
    for source, cap in (
        ("librewxr", librewxr.hourly_cap(tiles)),
        ("chmi", chmi.MAX_REQUESTS_PER_HOUR),
        ("open_meteo", open_meteo.MAX_REQUESTS_PER_HOUR),
    ):
        spent = _busiest_hour([when for when, url in session.log if _source_of(url) == source])
        assert spent < cap, f"{source} spent {spent} of its {cap} requests in one hour"

    assert session.counts()["metno"] == 0  # Open-Meteo never failed, so failover never woke


def _frame_read_latencies(session: Any) -> list[timedelta]:
    """How long each published radar frame waited before anything looked at it.

    The alert a frame would trigger cannot leave before the frame is read, so this
    is the latency the user feels. Computed from the index polls: each one sees
    whatever LibreWXR had published by then, and the first poll to see a given frame
    is the one that read it.
    """
    lag = timedelta(seconds=benchmark.PUBLICATION_LAG_S["librewxr"])
    step = timedelta(minutes=10)
    first_seen: dict[datetime, datetime] = {}
    for when, url in session.log:
        if "weather-maps.json" not in url:
            continue
        published = when - lag
        newest = published - timedelta(
            seconds=published.timestamp() % step.total_seconds(),
            microseconds=published.microsecond,
        )
        first_seen.setdefault(newest, when)
    return [seen - (stamp + lag) for stamp, seen in first_seen.items()]


async def test_a_published_frame_is_read_within_one_cycle(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A new radar frame must not wait longer than the cadence to be looked at.

    This is what `docs/ARCHITECTURE.md` § Coordinator scheduling promises with its
    publication alignment, stated as something measurable: the frames are published
    every ten minutes, so no frame may sit unread for a whole further cycle.
    """
    session = (await _simulate_day(hass, freezer, benchmark.WARSZAWA)).session
    latencies = _frame_read_latencies(session)

    _LOGGER.info(
        "radar frame read latency: median %s, worst %s over %d frames",
        sorted(latencies)[len(latencies) // 2],
        max(latencies),
        len(latencies),
    )

    assert latencies
    assert max(latencies) <= timedelta(minutes=10)
    assert min(latencies) >= timedelta(0)


@pytest.mark.parametrize(
    ("profile", "location"),
    [("warszawa", benchmark.WARSZAWA), ("bielsko", benchmark.BIELSKO)],
)
async def test_a_day_makes_no_request_outside_a_walk_window(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    profile: str,
    location: tuple[float, float],
) -> None:
    """The zero-polling promise, checked over a whole day rather than one window."""
    session = (await _simulate_day(hass, freezer, location)).session
    walks = [
        DAY_START.replace(hour=int(time[:2]), minute=int(time[3:])) + timedelta(days=day)
        for time in WALK_TIMES
        for day in (0, 1)
    ]

    assert session.log, "the simulated day made no request at all"
    for when, url in session.log:
        assert any(walk - WINDOW_BEFORE <= when <= walk + WINDOW_AFTER for walk in walks), (
            f"{url} was requested at {when}, outside every walk window"
        )


async def test_a_quiet_day_costs_nothing_at_all(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """With alerting off, a day of four walks makes no request whatsoever."""
    await hass.config.async_set_time_zone("UTC")
    entry = _entry(benchmark.BIELSKO)
    entry.add_to_hass(hass)
    session = benchmark.FixtureSession(now_provider=lambda: _utcnow(hass))

    with patch(
        "custom_components.walk_the_dog.coordinator.async_get_clientsession",
        return_value=session,
    ):
        freezer.move_to(DAY_START)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.async_set_enabled(False)

        moment = DAY_START
        while moment < DAY_END:
            moment += timedelta(minutes=5)
            freezer.move_to(moment)
            async_fire_time_changed(hass, moment)
            await hass.async_block_till_done()

    assert session.log == []


def test_the_publication_lag_script_mirrors_the_adapters() -> None:
    """`scripts/measure_publish_lag.py` copies two URLs; they must stay the copies.

    The script is stdlib-only on purpose — it has to run with a bare Python on a
    machine that has none of this installed — so it cannot import the adapters. This
    is the check that keeps the copies honest.
    """
    assert publish_lag.LIBREWXR_INDEX_URL == librewxr.BASE_URL + librewxr.WEATHER_MAPS_PATH
    assert publish_lag.RUN_INTERVAL_MIN == chmi.RUN_INTERVAL_MIN

    run = datetime(2026, 8, 26, 12, 35, tzinfo=UTC)
    assert publish_lag._forecast_url(run) == chmi.forecast_url(run)
