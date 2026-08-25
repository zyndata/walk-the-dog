"""Shared test fixtures.

Every test in this suite runs offline: adapters are driven through the Home
Assistant aiohttp mocker against the recorded responses in `tests/fixtures/`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.walk_the_dog.const import (
    SOURCE_ICON_EU,
    SOURCE_KNMI,
    SOURCE_LIBREWXR,
    SOURCE_METNO,
)
from custom_components.walk_the_dog.sources.base import (
    CELL_KM,
    RELIABILITY,
    STATE_OK,
    SampleGeometry,
    SourceSeries,
    SourceStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Forecast step each adapter publishes on — not the same thing as how often it
#: republishes, which is what `UPDATE_INTERVAL_S` measures.
STEP_S = {
    SOURCE_LIBREWXR: 600,
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

#: Frozen "now" used across the suite, close to when the fixtures were recorded.
NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the HA test harness load custom_components/ in every test."""


def load_fixture(*parts: str) -> Any:
    """Read a recorded JSON response."""
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


def load_bytes(*parts: str) -> bytes:
    """Read a recorded binary response (a radar tile)."""
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
