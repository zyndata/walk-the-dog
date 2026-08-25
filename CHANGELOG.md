# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 3 source clients: one adapter per recommended provider behind a common interface —
  LibreWXR (frame index, tile fetch, Web-Mercator disc mask, 90th-percentile pixel sampling),
  Open-Meteo (ICON-EU and KNMI HARMONIE from a single five-coordinate request), and MET Norway
  (failover-only, centre point, `If-Modified-Since`, honouring `Expires`). All three return the
  same normalized `SourceSeries` of UTC slots in mm/h.
- Source registry with provider failover: MET Norway is woken only after Open-Meteo fails twice
  in a row and stood down after it succeeds twice, so correlated sources never vote together.
- Frame sample cache: 32-entry LRU keyed by LibreWXR frame path, persisted through the Home
  Assistant `Store`, cleared when the location or radius changes, storing sampled floats only.
- Per-source request budgets enforced over a rolling hour, cross-cycle retry backoff, and
  cached-series reuse that keeps ageing so stale data drops itself out of the consensus.
- Recorded API fixtures under `tests/fixtures/` and 123 tests covering parsing, intensity
  mapping, disc geometry, budgets, failover, caching and error handling — all passing with
  networking disabled.

- Phase 2 repo skeleton: the `walk_the_dog` custom integration package with a valid
  `manifest.json` (v0.1.0), constants from the phase 0/1 decisions, a config-flow stub that
  aborts until the wizard ships, and empty modules matching the architecture layout.
- HACS/GitHub boilerplate: `hacs.json`, `info.md`, a README written for the public repo,
  MIT license, and CI workflows for hassfest, HACS validation (brands ignored until the
  phase 9 submission), lint, and tests.
- Cross-platform dev environment: uv-provisioned Python 3.14 venv via `scripts/setup.py`,
  pinned dev dependencies, pre-commit with ruff, `.editorconfig`/`.gitattributes` (LF
  everywhere), `.env.example`, and task-runner scripts (`lint`, `format`, `test`, `install`)
  that work identically on Windows and Linux; documented in `docs/DEVELOPMENT.md`.
- First tests: manifest/const consistency and config-entry setup/unload against the Home
  Assistant test harness (HA 2026.8.3).
- Repository configuration: Dependabot for GitHub Actions, issue forms, a security policy with
  private vulnerability reporting, hardened workflows (read-only token, concurrency groups,
  weekly validation run), and `scripts/github_setup.py`, which applies the GitHub-side settings
  (description, topics, features, `main` and release-tag rulesets, security toggles) through the
  GitHub CLI.

- Phase 1 architecture: `docs/ARCHITECTURE.md` is now the complete implementation blueprint —
  module layout, data flow and core dataclasses, per-source sampling strategy, the exact
  weighted-vote consensus algorithm (risk, confidence, freshness decay, degraded modes),
  walk-window evaluation with the earlier/later recommendation search and the material-change
  definition, coordinator polling windows (`lead_time` 30 min, 10-min cycles only inside active
  windows, zero polling otherwise), a concrete resource budget, and the frame cache design.
- Alert radius decision: default 5 km, minimum 4 km, maximum 15 km, derived from the measured
  source resolutions (sampled disc always spans ≥ 1 full cell of the coarsest regular source);
  reflected in `docs/CONFIG.md` together with the default intensity threshold (light).
- Phase 0 research: `docs/DATA_SOURCES.md` now documents every evaluated precipitation source
  with per-claim verification dates, a comparison table covering 20 candidates, the ranked
  recommendation, rejected candidates with reasons, the intensity mapping onto the common
  light/moderate/heavy scale, effective resolution per source, the fallback strategy, and the
  request budget.
- Recommended sources: LibreWXR (EUMETNET OPERA radar nowcast, +10…+60 min), Open-Meteo DWD
  ICON-EU, Open-Meteo KNMI HARMONIE AROME Europe, and MET Norway Locationforecast 2.0 as
  provider-level failover.
- Measured source independence (pairwise correlation of hourly precipitation over Poland) to
  keep correlated models from counting as separate votes in the consensus engine.
- Measured effective grid resolution per source, as the input to the phase 1 alert radius
  decision.
- This changelog.

### Fixed

- LibreWXR intensity calibration is now pinned rather than assumed: the rendered grey level of
  colour scheme 0 equals `dBZ + 32`, established from the AGPL-3.0 LibreWXR source and locked
  by a fixture test. `docs/DATA_SOURCES.md` records the derivation.

### Changed

- `docs/DEVELOPMENT.md` documents that the test suite cannot run natively on Windows (Home
  Assistant imports the Unix-only `fcntl`) and gives the Linux-container command to run it there.
- The source mix is one tile source plus two point/grid JSON sources, rather than the all-tiles
  design assumed at bootstrap. No free tile-based nowcast from an established provider survived
  evaluation.

### Removed

- RainViewer is no longer a candidate: its public API serves past radar frames only, and the
  live `radar.nowcast` array is empty.
