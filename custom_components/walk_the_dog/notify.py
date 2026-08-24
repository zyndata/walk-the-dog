"""Notification dispatch, material-change detection, auto-mute (implemented in phase 6).

Fires at T - earlier_margin; re-notifies only on material change
(docs/ARCHITECTURE.md § Walk-window evaluation, material change rules).
"""

from __future__ import annotations
