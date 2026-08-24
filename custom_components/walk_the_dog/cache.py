"""Bounded sample cache with persistence via HA Store (implemented in phase 3).

Stores sampled floats only, never raw tiles or responses; 32-entry LRU keyed by
LibreWXR frame path; ≤ 20 KB persisted (docs/ARCHITECTURE.md § Frame cache).
"""

from __future__ import annotations
