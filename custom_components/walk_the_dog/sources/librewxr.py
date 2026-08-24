"""LibreWXR OPERA radar nowcast adapter (implemented in phase 3).

Tile fetch + pixel sampling per docs/ARCHITECTURE.md § Frame sampling. The only
module allowed to use Pillow and image-related numpy.
"""

from __future__ import annotations
