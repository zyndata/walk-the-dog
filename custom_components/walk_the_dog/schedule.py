"""Walk-schedule model (3 modes) and next-walk computation (implemented in phase 6).

PURE module: no I/O, no homeassistant imports, `now` is always a parameter.
Walk times are configured in the HA local timezone and resolved to UTC per
occurrence here (docs/ARCHITECTURE.md § Data flow, timezone rule).
"""

from __future__ import annotations
