# PLAN.md — Phased implementation plan

Read [CLAUDE.md](CLAUDE.md) for workflow rules and [STATE.md](STATE.md) for current status before
starting any phase. **One phase per conversation.** Each phase below is written to be completable
in a single isolated session with no memory of previous sessions: read the listed inputs first,
do the tasks, verify the acceptance criteria, then run the end-of-phase ritual from `CLAUDE.md`.

Decisions already fixed at bootstrap (see `STATE.md` → Bootstrap):

- License: **MIT**.
- `average_walk_duration`: **required** user input, no default; if the user enters > 30 min,
  the config flow shows a warning that nowcast horizons make long windows less reliable.
- Notification timing: fire at **`T − earlier_margin`** (re-notify only if the recommendation
  materially changes).
- Walk schedule: user picks one of **three modes** — same times daily / weekday+weekend split /
  full per-day — and the form adapts to the chosen mode; mode is changeable in the options flow.

---

## Phase 0 — Research & decisions on data sources

**The most important phase. Be thorough; this is not a quick survey.**

**Goal:** select and document 2–4 precipitation nowcast sources covering Poland that publish
**ready-made forecast frames at least 30 minutes into the future** (hard requirement — radar-only
past frames disqualify a source; we never compute cloud movement ourselves).

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/DATA_SOURCES.md` (stub with candidate list).

**Tasks:**

1. For every candidate source (see the candidate list in `docs/DATA_SOURCES.md`, extend it as
   discovered), establish and document — **verified against current official documentation, not
   assumptions, with the date checked noted per claim**:
   - Does it publish ready-made forecast/nowcast frames ≥ 30 min ahead? (mandatory; reject otherwise)
   - Forecast horizon and time step.
   - Spatial coverage (must include all of Poland) and **effective resolution in km per cell**,
     including degradation from tiling.
   - Update frequency and typical publication latency (freshness).
   - Output format: tiles / GeoTIFF / JSON / other; exact API shape.
   - How intensity is encoded (dBZ, mm/h, colour scale) and how to map it onto the common
     light / moderate / heavy scale.
   - Licence, attribution requirements, rate limits, API key needed?, cost, stability/track record.
2. Only publicly documented or officially open APIs. No private endpoints extracted from mobile
   apps. Fetch cadence must stay well inside free-tier limits.
3. Write `docs/DATA_SOURCES.md`: comparison table, ranked recommendation of 2–4 sources with
   rationale, fallback strategy when a source is down, and the resulting request budget
   (requests/hour worst case, per source and total).
4. Record the effective resolution of each recommended source — phase 1 needs it to decide the
   default and minimum alert radius.
5. Create `CHANGELOG.md` (Keep a Changelog skeleton + entry for this phase).

**Acceptance criteria:**

- `docs/DATA_SOURCES.md` has no remaining TODOs; every mandatory attribute above is filled for
  every candidate, with a "checked on YYYY-MM-DD" note.
- At least 2 sources pass the ≥ 30 min forecast-frames requirement and are recommended.
- Rejected candidates are listed with the reason (so the research is not redone later).
- Request budget demonstrably fits inside every provider's documented limits.
- End-of-phase ritual from `CLAUDE.md` completed.

**Files touched:** `docs/DATA_SOURCES.md`, `CHANGELOG.md`, `STATE.md`.

---

## Phase 1 — Architecture design

**Goal:** a complete `docs/ARCHITECTURE.md` that a later session can implement from without
re-deriving any decision.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/DATA_SOURCES.md` (now filled),
`docs/CONFIG.md` (option semantics), `docs/ARCHITECTURE.md` (stub).

**Tasks:**

1. Module layout inside `custom_components/walk_the_dog/` (coordinator, source adapters behind a
   common interface, sampling, consensus engine, config flow, entities, notifier, cache).
2. Data flow: fetch → sample pixels at location/radius → normalize intensity → consensus score →
   window evaluation → recommendation → entity/notify/event outputs.
3. Frame sampling strategy per recommended source format: sample only pixels covering the
   configured radius, never decode full frames, cap memory, discard buffers immediately.
4. Consensus scoring algorithm: agreement across sources weighted by reliability and freshness;
   define the confidence output precisely.
5. Walk-window evaluation and recommendation search: `[T, T + average_walk_duration]`, backwards
   up to `earlier_margin`, forwards up to `later_margin`, nearest dry window of full duration wins.
6. Coordinator scheduling: fetch only within `[next_walk − earlier_margin − lead_time, walk_end]`,
   near-zero polling outside it and while the enable switch is off. Notification dispatch at
   `T − earlier_margin`, re-notify only on material change (define "material").
7. Resource budget: max RAM per update cycle, max requests/hour (must match the budget in
   `docs/DATA_SOURCES.md`), target CPU envelope for single-core ARM.
8. Frame cache design (avoid refetching identical frames; small, bounded, persisted).
9. **Decide default and minimum for the alert radius** using the effective resolutions from
   phase 0; sampling must always cover ≥ 1 full cell of the coarsest source. Record the decision
   and reasoning in `STATE.md` and reflect it in `docs/CONFIG.md`.

**Acceptance criteria:**

- `docs/ARCHITECTURE.md` has no remaining TODOs; every task above has a written outcome.
- Radius default/minimum decision recorded in `STATE.md` and `docs/CONFIG.md`.
- The doc names concrete numbers (budget, intervals, cache size), not "TBD".
- End-of-phase ritual completed.

**Files touched:** `docs/ARCHITECTURE.md`, `docs/CONFIG.md`, `CHANGELOG.md`, `STATE.md`.

---

## Phase 2 — Repo skeleton + development environment

**Goal:** a fresh clone on either Windows or Linux plus one documented command yields a working
dev environment; HA/HACS boilerplate is valid and CI is green.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/ARCHITECTURE.md` (module layout).

**Tasks:**

1. `custom_components/walk_the_dog/`: `manifest.json` (domain `walk_the_dog`, name
   "Walk the dog", `version`, `config_flow: true`, `iot_class`), minimal `__init__.py`,
   `const.py`, empty module files per the architecture layout.
2. HACS/GitHub files: `hacs.json`, `info.md`, README skeleton (written as if the repo were
   already public: HACS install section + a "development period: manual install" section that
   documents copying or symlinking `custom_components/walk_the_dog/` into the HA config folder),
   `LICENSE` (MIT), GitHub Actions running `hassfest` and HACS validation on push/PR.
3. Dev environment files: `.gitattributes` (`* text=auto eol=lf`), `.editorconfig`, `.gitignore`
   (Python, virtualenvs, IDE folders, `Thumbs.db`, `.DS_Store`, `.env`,
   `.claude/settings.local.json`), `.env.example`, pinned `requirements-dev.txt` (exact versions:
   ruff, pytest, pytest-homeassistant-custom-component, pre-commit), pre-commit config.
4. Cross-platform task runner: Python scripts under `scripts/` invoked identically on both OSes —
   at minimum `setup`, `lint`, `format`, `test`, `install` (deploy into a local HA config folder,
   target path from `.env`). `pathlib` only.
5. `.claude/`: project-level Claude Code settings and custom commands worth committing; nothing
   machine-specific or secret.
6. `docs/DEVELOPMENT.md`: setup on Windows and Linux, running tests, deploying into a test HA
   instance from each OS, git workflow between machines (push at session end; `STATE.md` is the
   handover).

**Acceptance criteria:**

- On the current machine: `python scripts/setup.py` (or the documented equivalent) from a clean
  state succeeds; `scripts/lint` and `scripts/test` pass (a placeholder test is fine).
- CI green on GitHub: hassfest + HACS validation (HACS action configured to tolerate the private
  repo during development, or documented as expected-fail until public — note the choice in `STATE.md`).
- `git ls-files --eol` shows LF for all text files.
- README/info.md contain no content that would need rewriting when the repo goes public.
- End-of-phase ritual completed.

**Files touched:** `custom_components/walk_the_dog/*`, `hacs.json`, `info.md`, `README.md`,
`LICENSE`, `.github/workflows/*`, `.gitattributes`, `.editorconfig`, `.gitignore`,
`.env.example`, `requirements-dev.txt`, `.pre-commit-config.yaml`, `scripts/*`, `.claude/*`,
`docs/DEVELOPMENT.md`, `CHANGELOG.md`, `STATE.md`.

---

## Phase 3 — Source clients

**Goal:** one adapter per recommended provider behind a common interface, fully tested against
recorded fixtures — no live calls in CI.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/DATA_SOURCES.md`,
`docs/ARCHITECTURE.md` (adapter interface + sampling strategy).

**Tasks:**

1. Define the common source interface (per architecture): async fetch of forecast frames for a
   bounding box around a point, returning normalized frames (timestamps, intensity on the common
   light/moderate/heavy scale, cell size, freshness metadata).
2. Implement one adapter per recommended source; honor attribution/rate-limit requirements from
   `docs/DATA_SOURCES.md`; sample only needed pixels; stream and discard buffers.
3. Record real API responses (scrubbed of anything secret) as fixtures under `tests/fixtures/`.
4. Unit tests per adapter: parsing, intensity mapping, error handling (timeouts, malformed data,
   stale frames), and that no adapter ever fetches more than the budgeted amount.
5. Frame cache from the architecture doc, with tests.

**Acceptance criteria:**

- `scripts/test` green with no network access (verify: tests pass offline).
- Every adapter returns the same normalized frame structure from its fixtures.
- Intensity mapping per source matches the table in `docs/DATA_SOURCES.md`.
- End-of-phase ritual completed.

**Files touched:** `custom_components/walk_the_dog/sources/*`, cache module, `tests/*`,
`CHANGELOG.md`, `STATE.md`.

---

## Phase 4 — Sampling + consensus scoring engine

**Goal:** the decision core as pure, hardware-independent functions over already-fetched
normalized frames, exhaustively unit-tested.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/ARCHITECTURE.md` (consensus + window
algorithms), `docs/CONFIG.md` (option semantics), phase 3 code (frame structures).

**Tasks:**

1. Spatial sampling: aggregate cells covering the configured radius (always ≥ 1 full cell of the
   coarsest source) into a per-timestep intensity per source.
2. Consensus scoring: combine per-source series into risk + confidence per timestep, weighted by
   source reliability and freshness, per the architecture doc.
3. Window evaluation: risk for `[T, T + average_walk_duration]` against the user's intensity
   threshold.
4. Recommendation search: nearest dry window of full duration within `[T − earlier_margin,
   T + later_margin]`; output direction (earlier/later/none), recommended time, risk, confidence,
   per-source breakdown.
5. Unit tests: rain at window start/end, all-dry, all-wet, sources disagreeing, stale source,
   single-source degraded mode, walk longer than nowcast horizon.

**Acceptance criteria:**

- All engine functions pure (no I/O, no HA imports); tests green offline.
- Edge cases above covered; per-source breakdown exposed in results.
- End-of-phase ritual completed.

**Files touched:** engine modules in `custom_components/walk_the_dog/`, `tests/*`,
`CHANGELOG.md`, `STATE.md`.

---

## Phase 5 — Config flow wizard + options flow

**Goal:** complete setup wizard and options flow implementing `docs/CONFIG.md`.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/CONFIG.md`, `docs/ARCHITECTURE.md`.

**Tasks:**

1. Step 1 — location: map picker (`LocationSelector`), pre-filled with HA home coordinates.
2. Step 2 — schedule: mode selector (same daily / weekday+weekend / per-day) with the form
   adapting to the chosen mode; editable list(s) of walk times.
3. Step 3 — parameters per `docs/CONFIG.md`: alert radius (default/min from phase 1), intensity
   threshold, earlier margin (default 1 h), later margin (default 30 min),
   `average_walk_duration` (**required, no default; warn when > 30 min**), notification target
   from `notify.mobile_app_*`, optional custom event checkbox, optional auto-mute
   `person`/`device_tracker` picker.
4. Options flow: everything from steps 2–3 editable later, including schedule mode.
5. Validation + `strings.json` keys for every field/error/warning (English now; pl in phase 7).
6. Tests with `pytest-homeassistant-custom-component`: happy path, each schedule mode, the
   > 30 min warning, invalid inputs, options round-trip.

**Acceptance criteria:**

- Config + options flow tests green; flow creates a config entry with the documented data shape.
- `docs/CONFIG.md` matches the implementation exactly (update it if reality diverged, and note
  deviations in `STATE.md`).
- End-of-phase ritual completed.

**Files touched:** `config_flow.py`, `options_flow` code, `strings.json`, `docs/CONFIG.md`,
`tests/*`, `CHANGELOG.md`, `STATE.md`.

---

## Phase 6 — Coordinator, entities, notifications, events

**Goal:** the integration works end-to-end in a real HA instance.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/ARCHITECTURE.md`, `docs/CONFIG.md`,
phases 3–5 code.

**Tasks:**

1. One shared `DataUpdateCoordinator` wiring sources → engine; adaptive polling exactly per the
   architecture doc (active only in `[next_walk − earlier_margin − lead_time, walk_end]`,
   near-zero otherwise and while disabled).
2. `switch` to enable/disable alerting; state survives restarts (`RestoreEntity`).
3. **Exactly one** recommendation `sensor` for the next upcoming walk; attributes: scheduled
   time, risk level, confidence, recommended time, direction, precipitation intensity,
   per-source breakdown, data freshness.
4. Push notification via the configured `notify.mobile_app_*` service at `T − earlier_margin`;
   re-notify only on material change; suppressed by the switch and by auto-mute (tracked entity
   not `home`).
5. Optional `walk_the_dog_alert` event with a documented payload (document in README/`docs/CONFIG.md`).
6. Tests: coordinator scheduling windows, switch persistence, sensor attributes, notification
   and event dispatch, mute logic.

**Acceptance criteria:**

- Tests green; manual smoke test in a local HA instance via `scripts/install` shows the sensor,
  switch, and a notification for a simulated risky walk (describe the smoke test in `STATE.md`).
- Zero polling observed while the switch is off.
- End-of-phase ritual completed.

**Files touched:** `coordinator.py`, `sensor.py`, `switch.py`, `__init__.py`, notifier/event
modules, `tests/*`, `docs/CONFIG.md`, `CHANGELOG.md`, `STATE.md`.

---

## Phase 7 — Localization + branding

**Goal:** full en/pl localization and integration branding.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, all `strings.json` content from phases 5–6.

**Tasks:**

1. `strings.json` + `translations/en.json` (base) + `translations/pl.json`. Every user-facing
   string translatable. Polish is the priority language for quality — the localized title is
   **"Idź już z psem"** (used in config-flow strings, entity names, docs). `manifest.json`
   `name` stays "Walk the dog" (not translatable).
2. Design icon + logo; ship `icon.png`/`logo.png` in the repo as fallback; prepare (do not yet
   submit) the `home-assistant/brands` PR content.
3. Verify notification texts and event payload docs are localized where HA allows.

**Acceptance criteria:**

- hassfest translations check green; no hard-coded user-facing strings remain (grep for them).
- pl.json complete and idiomatic, not machine-literal.
- Brands PR content ready in the repo (e.g. under `branding/`), submission deferred to phase 9.
- End-of-phase ritual completed.

**Files touched:** `strings.json`, `translations/*`, icon/logo assets, `branding/*`,
`CHANGELOG.md`, `STATE.md`.

---

## Phase 8 — Performance pass

**Goal:** verified resource behavior on (or representative of) low-end hardware.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, `docs/ARCHITECTURE.md` (resource budget).

**Tasks:**

1. Measure real CPU and RAM of a full update cycle on the lowest-end available target (real ARM
   device if available; otherwise constrained container — record which in `STATE.md`).
2. Compare against the budget in `docs/ARCHITECTURE.md`; tune polling intervals, sampling, and
   cache until within budget.
3. Verify request counts against the budget in `docs/DATA_SOURCES.md` over a simulated day.
4. Update `docs/ARCHITECTURE.md` with measured numbers.

**Acceptance criteria:**

- Measured numbers documented and within budget (or budget consciously revised, recorded in
  `STATE.md` with rationale).
- No blocking calls in the event loop (HA's asyncio debug/warnings clean).
- End-of-phase ritual completed.

**Files touched:** tuned modules, `docs/ARCHITECTURE.md`, `CHANGELOG.md`, `STATE.md`.

---

## Phase 9 — Docs, release 1.0.0

**Goal:** first tagged release.

> **The repository was made public early, on 2026-08-25, after phase 5** — so that the
> integration could be installed for testing as a HACS custom repository from a Home Assistant
> OS instance, which has no config folder a dev machine can write to. Tasks 2 and 4's
> "go public" half are therefore already done; see `STATE.md` → *Repository made public*.
> Everything else in this phase is unchanged.

**Inputs:** `CLAUDE.md`, `STATE.md`, this phase, README, `CHANGELOG.md`.

**Tasks:**

1. Finalize README (installation via HACS + manual, configuration, screenshots from a real HA
   instance), `info.md`, `docs/*`; verify repo topics for HACS. **Remove the work-in-progress
   banners from `README.md` and `info.md`.**
2. ~~**Secrets audit of the entire git history**~~ — **done 2026-08-25** before the repo was
   made public (method and result in `STATE.md`). Re-run it over the commits added since, as a
   final check before tagging.
3. Set `manifest.json` version to `1.0.0`; finalize `CHANGELOG.md` for 1.0.0.
4. ~~Flip the repository to public~~ — **done 2026-08-25.** Tag `v1.0.0` and create the GitHub
   release.
5. Submit to HACS (default repository inclusion) and open the `home-assistant/brands` PR, then
   drop the last `ignore: brands` from the HACS validation workflow.

**Acceptance criteria:**

- History audit clean over the commits added since 2026-08-25, method recorded in `STATE.md`.
- `v1.0.0` tagged, release notes published, CI green on the tag, WIP banners gone.
- HACS validation green with **no** ignores once the brands PR lands.
- HACS and brands submissions opened (acceptance may land later; link the PRs in `STATE.md`).
- End-of-phase ritual completed.

**Files touched:** `README.md`, `info.md`, `docs/*`, `manifest.json`, `CHANGELOG.md`, `STATE.md`.
