"""Source adapter registry (implemented in phase 3).

Builds the enabled adapter set and owns provider failover: metno is polled only
while Open-Meteo is failed, so correlated sources never vote together
(docs/ARCHITECTURE.md § Consensus scoring).
"""

from __future__ import annotations
