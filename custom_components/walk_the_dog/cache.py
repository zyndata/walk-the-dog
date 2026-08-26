"""Bounded sample cache with persistence via HA Store.

Stores **sampled floats only** — never raw tiles or responses — which is what keeps
it tiny: 48 entries of `{slot, mm/h}` fit in well under the 20 KB budget. Keyed by
frame `path`, because a nowcast frame is re-issued on every model run and only the
path changes with it; the frame's timestamp alone would collide across runs
(docs/ARCHITECTURE.md § Frame cache). Both image sources share the cache, keyed by
whatever string identifies a frame for them — a path for LibreWXR, the full URL for
CHMI — so their entries cannot collide.

The LRU itself is plain Python with no Home Assistant imports, so it can be unit
tested on its own; `async_load` / `async_schedule_save` are the only parts that
touch HA.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: LibreWXR keeps 12 past + 6 nowcast frames live (18) and CHMI one observed frame
#: per 5-minute run; 48 leaves slack across runs and restarts for both together.
MAX_ENTRIES = 48

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.frame_cache"

#: Entries older than this are dropped at every cycle — a two-hour-old radar frame
#: can never be part of a window we still evaluate.
MAX_AGE = timedelta(hours=2)

#: Delay before the debounced write, so a cycle that samples several frames
#: still costs at most one disk write.
SAVE_DELAY_S = 10


class Sample(NamedTuple):
    """One sampled frame: when it applies and how hard it rains over the disc."""

    slot: datetime
    mm_per_h: float


class SampleCache:
    """LRU of sampled frames, invalidated whenever the sampled geometry changes."""

    def __init__(self, geometry_key: str, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: OrderedDict[str, Sample] = OrderedDict()
        self._geometry_key = geometry_key
        self._max_entries = max_entries
        self._store: Any = None
        self._dirty = False

    # --- pure cache behaviour ----------------------------------------------

    @property
    def geometry_key(self) -> str:
        """Geometry the cached samples belong to."""
        return self._geometry_key

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, path: str) -> float | None:
        """Sampled intensity for a frame path, or None if it was never sampled."""
        entry = self._entries.get(path)
        if entry is None:
            return None
        self._entries.move_to_end(path)
        return entry.mm_per_h

    def set(self, path: str, slot: datetime, mm_per_h: float) -> None:
        """Record a sampled frame, evicting the least recently used if full."""
        self._entries[path] = Sample(slot, mm_per_h)
        self._entries.move_to_end(path)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        self._dirty = True

    def retarget(self, geometry_key: str) -> bool:
        """Point the cache at a new geometry, clearing it if it actually changed.

        Samples are geometry-specific: a moved location or a changed radius makes
        every stored value wrong, so they are dropped rather than reused.
        """
        if geometry_key == self._geometry_key:
            return False
        self._entries.clear()
        self._geometry_key = geometry_key
        self._dirty = True
        return True

    def evict_expired(self, now: datetime) -> int:
        """Drop samples whose slot is more than `MAX_AGE` in the past."""
        cutoff = now - MAX_AGE
        expired = [path for path, entry in self._entries.items() if entry.slot < cutoff]
        for path in expired:
            del self._entries[path]
        if expired:
            self._dirty = True
        return len(expired)

    # --- persistence --------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Serializable form written to the HA Store."""
        return {
            "geometry_key": self._geometry_key,
            "entries": [
                {"path": path, "slot": entry.slot.isoformat(), "mm_per_h": entry.mm_per_h}
                for path, entry in self._entries.items()
            ],
        }

    def load_dict(self, data: Any) -> None:
        """Restore from stored data, ignoring anything that no longer applies."""
        self._entries.clear()
        if not isinstance(data, dict) or data.get("geometry_key") != self._geometry_key:
            # Different location/radius, or a schema we do not recognise: start empty.
            return
        for raw in data.get("entries") or []:
            if not isinstance(raw, dict):
                continue
            path = raw.get("path")
            slot = raw.get("slot")
            value = raw.get("mm_per_h")
            if not isinstance(path, str) or not isinstance(slot, str):
                continue
            if not isinstance(value, int | float):
                continue
            try:
                parsed = datetime.fromisoformat(slot)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            self._entries[path] = Sample(parsed.astimezone(UTC), float(value))
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def attach_store(self, hass: HomeAssistant) -> None:
        """Wire up the HA Store used by `async_load` and `async_schedule_save`."""
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    async def async_load(self) -> None:
        """Restore the persisted samples, so a restart inside a walk window is warm."""
        if self._store is None:
            return
        try:
            data = await self._store.async_load()
        except Exception as err:  # a corrupt store must never block setup
            _LOGGER.warning("Discarding unreadable frame cache: %s", err)
            return
        self.load_dict(data)

    def async_schedule_save(self) -> None:
        """Queue a debounced write; at most one disk write per cycle."""
        if self._store is None or not self._dirty:
            return
        self._store.async_delay_save(self.as_dict, SAVE_DELAY_S)
        self._dirty = False


__all__ = ["MAX_ENTRIES", "STORAGE_KEY", "STORAGE_VERSION", "Sample", "SampleCache"]
