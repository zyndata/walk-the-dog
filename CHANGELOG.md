# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

### Changed

- The source mix is one tile source plus two point/grid JSON sources, rather than the all-tiles
  design assumed at bootstrap. No free tile-based nowcast from an established provider survived
  evaluation.

### Removed

- RainViewer is no longer a candidate: its public API serves past radar frames only, and the
  live `radar.nowcast` array is empty.
