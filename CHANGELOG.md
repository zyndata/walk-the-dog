# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Polish localization.** The integration speaks Polish end to end — the setup wizard, the
  options flow, every field description and warning, the entity and attribute names, the sensor's
  states, the service, and the push notifications, which are looked up in the user's language at
  the moment they are sent. A Polish Home Assistant calls the integration **„Idź już z psem"**,
  and names its device that too, so entity names read in one language instead of half of each.
  Home Assistant serves whichever language the user has set; nothing needs configuring. *(After
  updating, restart Home Assistant — reloading the integration does not clear the frontend's
  translation cache.)*

- **An icon and a logo.** A rain-blue badge with a paw print under three falling drops, drawn by
  `scripts/make_branding.py` so a colour or a proportion is a code change rather than a binary
  edit. They live in [`branding/`](branding/) in exactly the layout the
  [home-assistant/brands](https://github.com/home-assistant/brands) pull request needs. **Home
  Assistant will keep showing a placeholder icon until that pull request is submitted and merged**
  — that is phase 9; everything it needs is ready.

### Changed

- **The device is named from the translations, not from the config entry title.** Its name is the
  prefix Home Assistant puts in front of every entity's friendly name, so it had to be
  translatable. English installs see no difference. A device renamed by hand still wins.

- **Shorter field descriptions in both languages.** Reviewing the Polish copy showed several
  descriptions explaining more than the field needs — the extra-devices field, the alert radius,
  the away entity and the long-walk warning. They now say the one thing that matters, and the
  away entity's description names the wizard step where a walk can override it.

### Changed

- **The entry-wide notification device is now notified about every walk**, instead of only the
  walks that named no devices of their own. Reported from a live install: the field reappeared in
  the options with no way to tell what it was for, and "fallback" is not a thing a settings screen
  can convey in a label. "Always notify this device" is, and the per-walk lists are now plainly
  additive — extra phones for that one walk. **One phone still receives exactly one push**: the
  recipient list is de-duplicated both where a walk is stored and where the alert is dispatched,
  so naming the same device in both places, or twice in one place, cannot notify it twice.
  Anyone who was relying on a per-walk list to *replace* the entry-wide device should clear that
  device, or mute the walk.

### Added

- **A per-walk away entity** (`away_entity`). "Mute alerts when this is away" watched one person
  for the whole integration, which is wrong the moment two people share the dog: the morning walk
  should fall silent when the person who does the morning walk leaves. Each walk's notification
  step now takes its own optional person or device tracker, falling back to the entry-wide one
  when left empty.

### Fixed

- **The setup wizard's per-walk step explained nothing.** It arrived as an unlabelled "Options"
  dialog — no title, no indication of *which* walk time it was configuring, and no description of
  the mute switch — and `confirm_margin_min` reached the parameter form as its raw storage key.
  Every field in both steps now carries a label and a full description, and the per-walk step
  states the walk it is asking about, which of them it is, and where the always-notified device
  comes from. (If the old text is still on screen after updating, Home Assistant is serving a
  cached translation file — restart it, a reload of the integration is not enough.)

- **A notification could recommend a time that had already passed.** Reported from the first live
  install: an alert arrived at 22:31 about a 21:15 walk, saying "rain is expected around 21:15,
  wait until 21:20". Three things combined.
  1. The recommendation search had no notion of the present. Its candidate windows were bounded by
     the user's margins only, both measured from the walk time, so at 22:31 it was still perfectly
     willing to offer 21:20 — every number in the calculation was correct, and none of them knew
     what time it was.
  2. The notifier had a lower time bound (`walk − earlier margin`, the promised moment) and no
     upper one. Nothing said "the walk has started; there is nothing left to decide".
  3. The window a walk is watched in is extended by its own recommendation — deliberately, so a
     "wait until" answer can be re-checked — which kept the cycles running long past the walk and
     gave the first two bugs the time they needed to speak.

  The search is now bounded by `now` as well as by the margins; a recommendation expires when the
  time it names does, and `no_dry_window` expires when the walk begins; and a direction that
  flipped to `no_dry_window` *only* because the previously suggested moment has passed is no
  longer re-announced — the forecast behind it has not changed, so saying it again is nagging
  rather than news. The watch window keeps outliving the walk, because that is what makes a
  "wait until 14:00" decided at 12:00 checkable at all.

### Added

- **An *Already went* button on the notification, and the `walk_the_dog.walked` service behind
  it.** Closing a walk stops the advice *and* the polling: once the dog is out, every further
  request is spent on a decision nobody is going to make. Alerting itself is untouched — this is
  about one occurrence, not the integration. The walk's own start is encoded in the button's
  action identifier, so a leftover notification from yesterday cannot close today's walk, and
  tapping it clears the message from the household's other phones as well. Not remembered across
  a restart.
- **An optional confirmation before setting off** (`confirm_margin_min`, default 0 = off). A
  second short message, that many minutes before you actually leave, saying either that the plan
  still stands or that the rain has gone and the walk is back to its normal time. The second is
  what earns the option: a "wait until 14:00" relaxing back to "walk as planned" is not an alert
  direction, so silence alone would leave you waiting for a window that stopped being necessary.
- **Publication-aligned cycles.** The cycle grid is anchored to the walk and a provider's frames
  are not, so a frame published a minute after a cycle used to wait nearly a full slot to be looked
  at. The data was never staler for it — a fetch always returns the newest frame that exists — but
  the *alert* that frame would trigger waited with it, by up to a full cadence. The coordinator now
  also wakes shortly after each frame is due from the source that publishes at the cadence being
  run (LibreWXR at ten minutes, CHMI at five). The alignment may only ever pull a cycle **earlier**,
  never replace one: the grid keeps running underneath, so a wrong guess about when a frame lands
  costs one cheap extra cycle and can never cost a cycle that was due — the notification moment
  included, which is now pinned explicitly rather than following from the arithmetic.
- **A five-minute sprint cadence in the last 20 minutes before setting off**, where a source
  publishes fast enough to make it worth it — today CHMI's five minutes, inside its composite.
  A shower can build and arrive well inside one 10-minute slot, so the stretch before the door is
  now watched at the fastest rate anything actually publishes at. Everywhere else the cycle is
  unchanged, because polling a source faster than it publishes returns the same bytes: LibreWXR
  and Open-Meteo keep their own cadences and a sprint cycle costs two CHMI requests and nothing
  more. CHMI's hourly self-cap rises from 18 to 30 to allow it.
- **A `binary_sensor.walk_the_dog_walk_window` entity** — on while a walk window is open and
  cycles are running. The same fact as the sensor's `polling` attribute, given an entity of its
  own so a dashboard card or an automation can react to it without parsing a text state.
- **The alert says how far the radar can actually see.** Every window verdict now records whether
  a radar nowcast reaches all of it. The radars forecast 60 minutes ahead and the hourly models 12
  hours, so a walk moved further out than that is answered by the models alone — sound about
  whether it will rain, imprecise about when. Such a recommendation is flagged `provisional` in the
  event payload and sensor attributes, and the push adds a sentence saying it is a model estimate
  that is still being watched. This is the honest answer to "at 12:00 there is no radar data for
  14:00 yet": there is model data, it is worth acting on, and it is re-checked as 14:00 comes into
  the radar's range.
- **A `beyond_radar` confirmation step** in the wizard and the options flow, shown when the notice
  period (`earlier_margin_min`) is set beyond the 60 minutes the radars forecast. Like the existing
  long-walk warning it is a confirmation, not an error — but the trade-off is now stated before it
  is stored, instead of being discovered from a vague notification months later. The field
  descriptions on the alert-settings form say the same thing inline, for all three timing options.
- **`recommended_end` in the payload, and "back home by HH:MM" in the message.** Moving a walk
  moves its end too, and whether the new end still fits the evening is the user's call.
- **The request budget is published**, as the `requests_last_hour` and `requests_hourly_cap` sensor
  attributes. Every adapter already policed its own rolling hourly budget before sending anything;
  now that this fix lets a walk window stay open for hours, what it costs the providers is visible
  rather than merely promised.
- **One companion-app notification tag per walk**, so a revised recommendation replaces the message
  it supersedes on the phone instead of stacking a second, contradictory one underneath it.
- 43 further tests (452 total, still green with networking disabled) covering the clock bound on
  the search, expiry of advice, the clock-driven direction flip, radar coverage of a window, both
  timing-warning steps, the published budget, closing a walk by service and by button, a button
  from the wrong walk, the confirmation and its stand-down variant, the sprint cadence and its
  absence outside CHMI's box, publication alignment (every frame read within a minute of landing,
  and every scheduled cycle still run), the per-source fetch gates, and the new entity.

- **A fifth weather source: the Czech CHMI CZRAD radar nowcast** (`chmi`), from the institute's own
  open-data service at `opendata.chmi.cz`. A second, independent radar network with its own
  10-minute extrapolation out to +60 minutes — the same "when" precision the integration previously
  got only from LibreWXR — on a 1 km grid. Keyless, HTTPS, CC BY 4.0, nothing to configure.
- **Regional sources.** CHMI covers the Czech composite only (E 11.267–19.624, N 48.047–51.458):
  it is active around Bielsko-Biała and south-western Poland and reports the new `not_applicable`
  state everywhere else — no request is made, and the recommendation is exactly what it was before.
  The gate requires the *whole* sampled disc to fit inside the data rectangle, inset by 0.3°, so a
  disc hanging over the edge can never read its missing half as "no rain".
- 67 further tests (403 total, still green with networking disabled) covering the coverage gate, the
  projection against CHMI's published extent, the palette calibration, run discovery, archive
  parsing, request politeness, caching, the hourly budget and failure handling.
- **Range-weighted voting for CHMI.** CHMI has two radars, and a radar beam climbs and widens with
  distance, so the source's consensus weight is now scaled by how far the location is from the
  nearest of them: full to 120 km, decaying to half at 200 km — the institute's own stated ceiling
  for precipitation-intensity estimation. Over Bielsko-Biała (167 km from Skalky, beam centre
  ~3.9 km up) that gives 0.67 instead of 0.95, which is low enough that when LibreWXR says wet and
  CHMI says dry, the slot still comes out wet. This is the first source whose weight depends on
  where the user lives.
- `scripts/make_chmi_fixtures.py`, which re-records `tests/fixtures/chmi/` from the live service.

### Changed

- **Every adapter now gates its fetch on its own publication interval** rather than on the cycle
  (`librewxr` 10 min and `chmi` 5 min join Open-Meteo's 30 and MET Norway's 10). This is what keeps
  the request count tied to how often the providers publish rather than to how often the
  coordinator wakes: the cycle count roughly doubles inside a window, and the request count does
  not move. CHMI's hourly ceiling is 30 and it now spends at most 24.
- `engine.recommend()` and `engine.evaluation_slots()` take a `Search` value — walk duration and
  the two margins, which travelled together at every call site — instead of three separate
  arguments. `recommend()` also takes `now`; omitting it searches the whole margin, past included,
  which only a test evaluating the geometry in isolation should want.
- `sources/base.py` now owns the Marshall-Palmer dBZ → mm/h conversion, so both radar sources land
  on the same intensity scale. `sources/librewxr.py` re-exports it unchanged.
- The frame sample cache holds 48 entries instead of 32, and is shared by both image sources.
- The in-window request budget rises from ≤ 28 to ≤ 46 requests/hour **for locations inside the
  Czech composite only**; everywhere else it is unchanged. A CHMI cycle is two requests — its whole
  +10…+60 min forecast ships as one archive — for about 110 KB.

### Notes on how this source was verified

The work started from an analysis of the Meteor Android app, which was the request. Probing the
live services **(measured 2026-08-26)** moved it to CHMI, and corrected several things along the
way; all of it is written up in `docs/DATA_SOURCES.md` § CHMI:

- **Meteor's own endpoints serve no frames.** Its feed answers with `Content-Length: 0` on every
  host and variant tried, and every documented frame path returns 404. CHMI publishes the same
  products itself, over HTTPS, with a specification and a published colour scale.
- **The intensity calibration is confirmed, not assumed.** CHMI's legend is a 4 dBZ ladder from 4
  to 60 dBZ whose mm/h decades land exactly where `Z = 200·R^1.6` puts them — so the project's
  existing Marshall-Palmer conversion is CHMI's own. The provisional discount is gone; the weight
  is 0.95, held just below LibreWXR only for CHMI's coarser 4 dBZ quantisation.
- **A live cross-check over Bielsko-Biała agreed to the dBZ**: OPERA read grey 44 (12 dBZ,
  0.205 mm/h) and CZRAD read level 3 (12 dBZ, 0.205 mm/h) on the same disc at the same minute, both
  calling it light drizzle easing over the hour.
- **CHMI's `png_masked` variants are not usable and the adapter avoids them.** They render
  precipitation with blending, so their pixels are off-palette; a nearest-colour decode reported
  205 mm/h for light drizzle. Colour matching is now exact, and an unrecognised colour is treated
  as no data rather than guessed.

### Known limitations

- **It is not established which of the two radars is right in absolute terms.** A sweep of the
  whole domain found CZRAD reading roughly 3× lower than OPERA in mm/h, with 18 points where OPERA
  saw rain and CZRAD saw none against 2 the other way. The gap persists close to the Czech radars,
  where beam overshoot does not explain it, and LibreWXR's known NWP-layer fusion is an equally
  good suspect. Settling it needs rain-gauge ground truth.
- **The LibreWXR/CHMI correlation has not been measured**, though the concern is smaller than it
  first looked: over Bielsko-Biała the two composites are dominated by different radars (Ramża at
  44 km for OPERA, Skalky at 167 km for CZRAD).
- CHMI's runs land on a 5-minute grid, so a run based at :?5 produces forecast slots offset by five
  minutes from the engine's 10-minute grid. The step-function alignment handles it; it is noted
  because it makes CHMI's slots differ from LibreWXR's by up to five minutes.

### Added — earlier in this cycle

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
