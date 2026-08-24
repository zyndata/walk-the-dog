"""WalkCoordinator: polling windows and orchestration (implemented in phase 6).

Per docs/ARCHITECTURE.md § Coordinator scheduling: one shared DataUpdateCoordinator,
10-minute cycles only inside the active window, zero polling otherwise.
"""

from __future__ import annotations
