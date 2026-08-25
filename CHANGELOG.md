# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Per-walk notification targets.** Every configured walk now has its own list of companion-app
  devices and its own *never alert about this walk* switch, so the morning walk and the evening
  walk can belong to different people. The setup wizard and the options flow ask about each walk
  in turn, between the walk times and the alert settings.
- 17 further tests (336 total, still green with networking disabled) covering per-walk devices,
  several devices at once, per-walk mute, the fallback to the default device, and the storage,
  pruning and validation of the new step.
- Phase 6 coordinator: one shared `DataUpdateCoordinator` that runs a cycle every 10 minutes
  only while a walk window is near — from `walk − earlier margin − 30 min` until the end of the
  walk it recommends — and holds a single armed timer the rest of the time. No `update_interval`,
  so an idle day costs zero requests and zero wakeups.
- Cycle grid anchored to the polling-window start, so one cycle lands exactly on
  `walk − earlier margin` — the promised notification moment — whatever minute the walk itself
  is scheduled at.
- Next-walk computation in `schedule.py` (`walks_from`, still pure): local walk times resolved
  to UTC per occurrence, so a 07:00 walk stays at 07:00 local across a daylight-saving change.
- One recommendation sensor for the next upcoming walk (`ok` / `earlier` / `later` /
  `no_dry_window` / `unknown`), with the scheduled and suggested times, risk, confidence,
  expected intensity, the per-source breakdown, data freshness and the required source
  attributions in its attributes.
- Alerting switch (`RestoreEntity`, default on). While it is off there are no timers, no
  requests and no cycles; the coordinator starts in the off position and the switch restores
  the real one, so a Home Assistant started with alerting disabled never reaches a provider.
- Push notification through the configured `notify.mobile_app_*` service at
  `walk − earlier margin`, re-sent only on a material change, never sent about a walk that looks
  dry, and suppressed by the switch, by auto-mute, or by having no contributing source.
- Optional `walk_the_dog_alert` event, fired whenever a notification would fire — including
  when auto-mute suppresses the push — with the payload documented in `docs/CONFIG.md`.
- English strings for both entities, their states and the notification texts.
- 48 further tests (319 total, still green with networking disabled) covering the polling
  windows, zero polling while alerting is off, switch persistence across a restart, sensor
  states and attributes, notification and event dispatch, material change, auto-mute, and the
  daylight-saving behaviour of the walk schedule.

### Changed

- The **notification device** option is now the *default* notification device: it is used for any
  walk that has no devices of its own. Existing configurations keep working unchanged — a walk
  nobody has set devices for behaves exactly as before.
- A walk is now identified by the schedule slot and the local time the user typed, rather than by
  the UTC instant it resolves to, so its notification settings survive a daylight-saving change.
- An unregistered `notify.mobile_app_*` service no longer silences the other devices on the same
  walk; it is logged and skipped.
- `notify.py` renamed to `notifier.py`: a module named after a platform inside an integration
  *is* that platform to Home Assistant, and this one is not a notify platform.
- `docs/DATA_SOURCES.md` opens with *What is actually wired in* — the four shipped sources side by
  side (horizon, series step, publisher cadence, our fetch cadence, staleness, cell size, weight,
  sampling, request ceilings, licences) ahead of the phase 0 research that chose them.
- `docs/CONFIG.md` documents the entities, the sensor states and attributes, the notification
  rules and the full `walk_the_dog_alert` payload schema.
- Work-in-progress banners in `README.md` and `info.md` updated: the prediction loop works;
  localization, branding and the performance pass are what remain.

- Phase 5 setup wizard: location on a map pre-filled with the Home Assistant home, a walk
  schedule in one of three modes (same times every day, weekday/weekend split, or a full
  per-day schedule) with the times form adapting to the chosen mode, and the alert parameters
  from `docs/CONFIG.md`.
- Options flow reusing the wizard's schedule and parameter steps verbatim, so everything except
  the location — schedule mode included — is editable later and validates identically; changing
  an option reloads the config entry.
- Walk-schedule model in `schedule.py` (pure): the per-mode storage shape, time parsing and
  normalization, and `expand()`, the single place that turns any mode into per-weekday walk
  times.
- Long-walk warning: an average walk duration over 30 minutes has to be confirmed on its own
  step, and declining returns to the parameters form with the entered values intact.
- Full English `strings.json` / `translations/en.json` for both flows, including translated
  schedule-mode and rain-intensity choices.
- 64 further tests (271 total, still green with networking disabled) covering the happy path,
  each schedule mode, the long-walk warning, invalid input, an options round-trip, and the
  strings files themselves.

- Phase 4 decision engine: `engine/grid.py`, `engine/consensus.py` and `engine/window.py` turn
  the sources' normalized series into a recommendation, as pure functions with no I/O, no Home
  Assistant imports and no clock of their own.
- Shared 10-minute UTC grid: each source's own steps are projected onto it as a step function
  with honest gaps, so an hourly model covers all six slots of its hour and a slot nobody
  forecasts stays absent instead of reading as "no rain".
- Weighted consensus vote per slot: risk, confidence and expected intensity from reliability x
  freshness weights, with stale series dropped, confidence capped by how many sources actually
  voted, and each source's status restated as `ok` / `stale` / `out_of_range` / what its adapter
  reported.
- Window evaluation and recommendation search: the scheduled walk is scored on its worst slot
  and its weakest slot, and when it is not dry the nearest dry window on the grid within the
  configured margins wins, earlier beating later at equal distance.
- Per-source breakdown travelling with every recommendation, plus `degraded`, `horizon_limited`
  and the material-change test that gates re-notification.
- 84 further tests (207 total, still green with networking disabled), covering rain at the start
  and end of a walk, all-dry, all-wet, disagreement, a stale source, single-source degraded
  mode, walks that outrun the forecast horizon, and a structural check that the engine stays
  pure.

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

- `scripts/github_setup.py` really enables secret scanning and push protection. It sent them as
  `gh api -f "security_and_analysis[secret_scanning][status]=enabled"`; `gh` passes bracketed
  field names through literally, so GitHub ignored the key, answered 200, and the script
  reported success while changing nothing. It now sends nested JSON on stdin.
- LibreWXR intensity calibration is now pinned rather than assumed: the rendered grey level of
  colour scheme 0 equals `dBZ + 32`, established from the AGPL-3.0 LibreWXR source and locked
  by a fixture test. `docs/DATA_SOURCES.md` records the derivation.

### Changed

- **The repository is public** as of 2026-08-25, ahead of the phase 9 schedule, so the
  integration can be installed for testing as a HACS custom repository. `README.md` and
  `info.md` carry work-in-progress banners until the 1.0.0 release; a full secrets audit of the
  git history was run first and came back clean (method recorded in `STATE.md`).
- HACS validation now runs the `hacsjson` and `integration_manifest` checks for real — they were
  ignored only because the GitHub API returns nothing for a private repo. Only `brands` is still
  ignored, until the phase 9 brands PR.
- `docs/DEVELOPMENT.md` documents deploying through a HACS custom repository (the practical route
  for a Home Assistant OS test instance) alongside the local-folder and Samba routes.
- `manifest.json` declares `single_config_entry`: one home, one schedule, one recommendation
  sensor, so a second setup attempt now aborts.
- `docs/CONFIG.md` describes the implemented flows and pins the config entry data and options
  shape, replacing the phase 1 placeholders.
- `docs/DEVELOPMENT.md` documents that the test suite cannot run natively on Windows (Home
  Assistant imports the Unix-only `fcntl`) and gives the Linux-container command to run it there.
- The source mix is one tile source plus two point/grid JSON sources, rather than the all-tiles
  design assumed at bootstrap. No free tile-based nowcast from an established provider survived
  evaluation.

### Removed

- RainViewer is no longer a candidate: its public API serves past radar frames only, and the
  live `radar.nowcast` array is empty.
