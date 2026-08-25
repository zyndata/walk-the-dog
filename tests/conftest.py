"""Shared test fixtures.

Every test in this suite runs offline: adapters are driven through the Home
Assistant aiohttp mocker against the recorded responses in `tests/fixtures/`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from custom_components.walk_the_dog.sources.base import SampleGeometry

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
