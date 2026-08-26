"""Shared test fixtures.

Every test in this suite runs offline: adapters are driven through the Home
Assistant aiohttp mocker against the recorded responses in `tests/fixtures/`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
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
    SOURCE_CHMI,
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
    SOURCE_METNO,
)
from custom_components.walk_the_dog.sources import SourceRegistry
from custom_components.walk_the_dog.sources.base import (
    CELL_KM,
    RELIABILITY,
    STATE_OK,
    SampleGeometry,
    SourceSeries,
    SourceStatus,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

#: Forecast step each adapter publishes on — not the same thing as how often it
#: republishes, which is what `UPDATE_INTERVAL_S` measures.
STEP_S = {
    SOURCE_LIBREWXR: 600,
    SOURCE_CHMI: 600,
    SOURCE_KNMI: 3600,
    SOURCE_ICON_EU: 3600,
    SOURCE_METNO: 3600,
}

FIXTURES = Path(__file__).parent / "fixtures"

#: Warszawa city centre — a public landmark, never the user's own coordinates.
#: Matches `librewxr/tile_dry.png` and `open_meteo/dry.json`.
TEST_GEOMETRY = SampleGeometry(latitude=52.2297, longitude=21.0122, radius_km=5.0)

#: Near Sejny in north-eastern Poland — the disc that `librewxr/tile_wet.png` covers
#: with continuous precipitation, and the area `open_meteo/wet.json` was recorded for.
WET_GEOMETRY = SampleGeometry(latitude=54.0191, longitude=23.0081, radius_km=5.0)

#: Bielsko-Biala — inside the CHMI CZRAD composite, and the only geometry in the
#: suite CHMI will answer for at all. `TEST_GEOMETRY` (Warszawa) is deliberately
#: outside it, which is what makes the "silent outside its box" tests meaningful.
#: It is also where the recorded `tests/fixtures/chmi/` frames were sampled.
CHMI_GEOMETRY = SampleGeometry(latitude=49.8224, longitude=19.0584, radius_km=5.0)

#: Frozen "now" used across the suite, close to when the fixtures were recorded.
NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the HA test harness load custom_components/ in every test."""


def load_fixture(*parts: str) -> Any:
    """Read a recorded JSON response."""
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


def load_bytes(*parts: str) -> bytes:
    """Read a recorded binary response (a radar tile, composite or archive)."""
    return FIXTURES.joinpath(*parts).read_bytes()


@pytest.fixture
def geometry() -> SampleGeometry:
    """The sampled disc used by every adapter test."""
    return TEST_GEOMETRY


@pytest.fixture
def wet_geometry() -> SampleGeometry:
    """A disc that the recorded wet tile covers with rain end to end."""
    return WET_GEOMETRY


@pytest.fixture
def chmi_geometry() -> SampleGeometry:
    """A disc inside the CHMI composite — that source answers only for this one."""
    return CHMI_GEOMETRY


@pytest.fixture
def now() -> datetime:
    """Frozen clock — adapters take `now` as a parameter, never read it."""
    return NOW


def make_series(
    source_id: str,
    values: Sequence[float],
    *,
    start: datetime,
    step_s: int | None = None,
    issued_at: datetime | None = None,
    fetched_at: datetime | None = None,
) -> SourceSeries:
    """Build a normalized series from a list of mm/h values, one per step.

    The engine only ever sees `SourceSeries`, so its tests build them directly
    instead of driving adapters: that is what "pure, hardware-independent" buys.
    """
    step = step_s if step_s is not None else STEP_S[source_id]
    slots = tuple(
        (start + timedelta(seconds=index * step), float(value))
        for index, value in enumerate(values)
    )
    return SourceSeries(
        source_id=source_id,
        issued_at=issued_at if issued_at is not None else start,
        fetched_at=fetched_at if fetched_at is not None else (issued_at or start),
        step_s=step,
        slots=slots,
        cell_km=CELL_KM[source_id],
        reliability=RELIABILITY[source_id],
    )


def make_status(source_id: str, state: str = STATE_OK, **kwargs: Any) -> SourceStatus:
    """An adapter-level status, as the registry would hand it to the engine."""
    return SourceStatus(source_id, state, **kwargs)


#: The walk the coordinator/entity tests revolve around: 05:00 UTC, 30 minutes long,
#: searchable 60 minutes back and 30 forward. Polling therefore opens at 03:30 UTC
#: and the notification is promised for 04:00 UTC.
WALK_HHMM = "05:00"
WALK_START = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 25, 3, 30, tzinfo=UTC)
ARM_AT = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)
WALK_END = datetime(2026, 8, 25, 5, 30, tzinfo=UTC)

ENTRY_DATA: dict[str, Any] = {
    CONF_LOCATION: {"latitude": TEST_GEOMETRY.latitude, "longitude": TEST_GEOMETRY.longitude}
}

ENTRY_OPTIONS: dict[str, Any] = {
    CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY,
    CONF_SCHEDULE: {"all": [WALK_HHMM]},
    CONF_RADIUS_KM: 5.0,
    CONF_INTENSITY_THRESHOLD: INTENSITY_THRESHOLD_LIGHT,
    CONF_EARLIER_MARGIN_MIN: 60,
    CONF_LATER_MARGIN_MIN: 30,
    CONF_WALK_DURATION_MIN: 30,
    CONF_FIRE_EVENT: False,
}


class FakeFetch:
    """Stands in for `SourceRegistry.async_fetch` and counts what it was asked for.

    The adapters have their own fixture-driven tests; a coordinator test is about
    *when* a fetch happens, so it answers from a builder instead of the network.
    """

    def __init__(self) -> None:
        """Answer with nothing until a test says otherwise."""
        self.calls = 0
        self.build: Any = lambda now: ([], [])

    async def __call__(self, session: Any, geometry: Any, now: datetime) -> Any:
        """Record the call and return whatever the current builder produces."""
        self.calls += 1
        return self.build(now)


def hourly_sources(
    now: datetime,
    values: Sequence[float],
    *,
    start: datetime = datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
    source_ids: Sequence[str] = (SOURCE_ICON_EU, SOURCE_KNMI),
) -> tuple[list[SourceSeries], list[SourceStatus]]:
    """Two agreeing hourly models, freshly issued — one mm/h value per hour."""
    series = [make_series(sid, values, start=start, issued_at=now) for sid in source_ids]
    return series, [make_status(sid, age_s=0, contributed=True) for sid in source_ids]


@pytest.fixture
def fetch() -> Generator[FakeFetch]:
    """Replace the source registry's network cycle for the whole test."""
    fake = FakeFetch()
    with patch.object(SourceRegistry, "async_fetch", new=fake):
        yield fake


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured entry with the standard walk, not yet set up."""
    await hass.config.async_set_time_zone("UTC")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Walk the dog",
        data=ENTRY_DATA,
        options=ENTRY_OPTIONS,
        version=1,
    )
    config_entry.add_to_hass(hass)
    return config_entry


async def setup_entry(hass: HomeAssistant, config_entry: MockConfigEntry) -> Any:
    """Set the entry up and hand back its coordinator."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry.runtime_data


async def run_cycle(hass: HomeAssistant, freezer: Any, moment: datetime) -> None:
    """Move the clock to `moment` and let the armed timer fire."""
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done()
