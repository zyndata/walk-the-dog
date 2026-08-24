# Architecture

> **Stub — filled in during phase 1** (see [PLAN.md](../PLAN.md)). Prerequisite:
> [DATA_SOURCES.md](DATA_SOURCES.md) completed in phase 0. Every design decision here must
> respect the hard constraint: single-core ARM, ~512 MB RAM, only `aiohttp`/`numpy`/`Pillow`.

## Module layout

TODO (phase 1): modules inside `custom_components/walk_the_dog/` and their responsibilities.

## Data flow

TODO (phase 1): fetch → sample → normalize → consensus → window evaluation → recommendation →
outputs (sensor / switch / notification / event).

## Frame sampling strategy

TODO (phase 1): per source format, how to read only the pixels covering the configured radius
without decoding full frames; memory caps; buffer lifetime.

## Consensus scoring

TODO (phase 1): exact algorithm — per-source weights (reliability, freshness), risk and
confidence definitions, degraded modes when sources are missing.

## Walk-window evaluation & recommendation search

TODO (phase 1): `[T, T + average_walk_duration]` evaluation, earlier/later search, tie-breaking,
"material change" definition for re-notification.

## Coordinator scheduling & polling windows

TODO (phase 1): active window `[next_walk − earlier_margin − lead_time, walk_end]`, `lead_time`
value, idle behavior, behavior while the enable switch is off.

## Resource budget

TODO (phase 1): max RAM per update cycle, max requests/hour (consistent with
[DATA_SOURCES.md](DATA_SOURCES.md)), CPU envelope. Phase 8 replaces estimates with measurements.

## Frame cache

TODO (phase 1): keying, size bound, persistence, invalidation.

## Alert radius decision

TODO (phase 1): default and minimum radius derived from phase 0 effective resolutions;
sampling always covers ≥ 1 full cell of the coarsest source.
