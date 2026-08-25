"""The frame sample cache: LRU behaviour, invalidation, and HA Store persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant

from custom_components.walk_the_dog.cache import (
    MAX_ENTRIES,
    STORAGE_KEY,
    STORAGE_VERSION,
    SampleCache,
)
from custom_components.walk_the_dog.sources.base import SampleGeometry

NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
GEOMETRY = SampleGeometry(52.2297, 21.0122, 5.0)


def _cache() -> SampleCache:
    return SampleCache(GEOMETRY.key)


def test_stores_and_returns_a_sampled_value() -> None:
    """The cache holds one float per frame — never the tile it came from."""
    cache = _cache()
    cache.set("/v2/radar/1787640600", NOW, 3.646332)

    assert cache.get("/v2/radar/1787640600") == 3.646332
    assert cache.get("/v2/radar/9999999999") is None
    assert len(cache) == 1


def test_keyed_by_frame_path_not_timestamp() -> None:
    """A re-issued nowcast frame keeps its time but changes its path — and its value."""
    cache = _cache()
    slot = NOW + timedelta(minutes=30)
    cache.set("/v2/radar/1787640600/run-a", slot, 0.0)
    cache.set("/v2/radar/1787640600/run-b", slot, 5.0)

    assert cache.get("/v2/radar/1787640600/run-a") == 0.0
    assert cache.get("/v2/radar/1787640600/run-b") == 5.0


def test_evicts_least_recently_used_beyond_the_bound() -> None:
    """32 entries is the ceiling; the oldest untouched frame goes first."""
    cache = _cache()
    for index in range(MAX_ENTRIES):
        cache.set(f"/frame/{index}", NOW, float(index))

    cache.get("/frame/0")  # refresh the oldest so it survives
    cache.set("/frame/new", NOW, 99.0)

    assert len(cache) == MAX_ENTRIES
    assert cache.get("/frame/0") == 0.0
    assert cache.get("/frame/1") is None
    assert cache.get("/frame/new") == 99.0


def test_changing_geometry_clears_every_sample() -> None:
    """Samples are geometry-specific; a moved home makes them all wrong."""
    cache = _cache()
    cache.set("/frame/0", NOW, 1.0)

    assert cache.retarget(SampleGeometry(52.2297, 21.0122, 6.0).key)
    assert len(cache) == 0
    assert cache.get("/frame/0") is None


def test_retargeting_to_the_same_geometry_keeps_the_samples() -> None:
    """An options save that did not move anything must not throw the cache away."""
    cache = _cache()
    cache.set("/frame/0", NOW, 1.0)

    assert not cache.retarget(GEOMETRY.key)
    assert cache.get("/frame/0") == 1.0


def test_expired_frames_are_evicted() -> None:
    """A frame more than two hours old can never be part of a window we still evaluate."""
    cache = _cache()
    cache.set("/frame/old", NOW - timedelta(hours=3), 1.0)
    cache.set("/frame/recent", NOW - timedelta(minutes=30), 2.0)

    assert cache.evict_expired(NOW) == 1
    assert cache.get("/frame/old") is None
    assert cache.get("/frame/recent") == 2.0


def test_round_trips_through_its_serialized_form() -> None:
    """What is written to the Store restores exactly."""
    cache = _cache()
    cache.set("/frame/0", NOW, 1.5)
    cache.set("/frame/1", NOW + timedelta(minutes=10), 0.0)

    restored = _cache()
    restored.load_dict(cache.as_dict())

    assert len(restored) == 2
    assert restored.get("/frame/0") == 1.5
    assert restored.get("/frame/1") == 0.0


def test_stored_data_for_another_geometry_is_discarded() -> None:
    """Restoring after the user moved must not resurrect the old location's samples."""
    cache = _cache()
    cache.set("/frame/0", NOW, 1.5)
    stored = cache.as_dict()

    elsewhere = SampleCache(SampleGeometry(50.06, 19.94, 5.0).key)
    elsewhere.load_dict(stored)

    assert len(elsewhere) == 0


def test_corrupt_stored_data_is_ignored_entry_by_entry() -> None:
    """A damaged store degrades to a cold cache instead of breaking setup."""
    cache = _cache()
    cache.load_dict(
        {
            "geometry_key": GEOMETRY.key,
            "entries": [
                {"path": "/frame/ok", "slot": NOW.isoformat(), "mm_per_h": 1.0},
                {"path": "/frame/bad-slot", "slot": "not-a-date", "mm_per_h": 1.0},
                {"path": "/frame/no-value", "slot": NOW.isoformat()},
                {"slot": NOW.isoformat(), "mm_per_h": 1.0},
                "not even a dict",
            ],
        }
    )

    assert len(cache) == 1
    assert cache.get("/frame/ok") == 1.0


def test_unknown_shapes_load_as_empty() -> None:
    """A schema change or a missing file leaves an empty, usable cache."""
    for data in (None, {}, [], "garbage", {"entries": []}):
        cache = _cache()
        cache.load_dict(data)
        assert len(cache) == 0


def test_persisted_payload_stays_far_inside_the_size_budget() -> None:
    """docs/ARCHITECTURE.md budgets 20 KB for the whole persisted store."""
    cache = _cache()
    for index in range(MAX_ENTRIES):
        cache.set(f"/v2/radar/17876406{index:02d}", NOW, 12.345678)

    assert len(json.dumps(cache.as_dict()).encode()) < 20 * 1024


async def test_persists_and_reloads_through_the_home_assistant_store(
    hass: HomeAssistant,
) -> None:
    """A restart inside a walk window comes back warm rather than refetching."""
    cache = _cache()
    cache.attach_store(hass)
    cache.set("/frame/0", NOW, 4.25)
    cache.async_schedule_save()
    await hass.async_block_till_done()

    # Flush the delayed save the way HA does on shutdown.
    await cache._store.async_save(cache.as_dict())

    reloaded = _cache()
    reloaded.attach_store(hass)
    await reloaded.async_load()

    assert reloaded.get("/frame/0") == 4.25
    assert cache._store.key == STORAGE_KEY
    assert cache._store.version == STORAGE_VERSION


async def test_loading_with_no_stored_file_is_a_cold_start(hass: HomeAssistant) -> None:
    """First run has nothing to restore, and must not fail because of it."""
    cache = _cache()
    cache.attach_store(hass)

    await cache.async_load()

    assert len(cache) == 0
