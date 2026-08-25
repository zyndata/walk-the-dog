# STATE.md — Living log

The handover document between sessions and machines. Update at the end of every phase (and
whenever a decision deviates from [PLAN.md](PLAN.md)). Statuses: `not started` / `in progress` / `done`.

## Bootstrap (scaffolding session)

- **Status:** done
- **Date:** 2026-08-22
- **What was built:** `CLAUDE.md`, `PLAN.md`, `STATE.md`, stub docs (`docs/ARCHITECTURE.md`,
  `docs/DATA_SOURCES.md`, `docs/CONFIG.md`). No application code.
- **Decisions:**
  - License MIT — simplest, standard for HACS integrations (user choice).
  - `average_walk_duration` is a required user input with no default; config flow warns when
    the value exceeds 30 min — nowcast horizons make long dry windows unreliable (user choice).
  - Notification fires at `T − earlier_margin`; re-notify only on material change — latest
    moment at which a "go earlier" recommendation is still actionable (user choice).
  - Walk schedule has three user-selectable modes (same daily / weekday+weekend / per-day) with
    an adaptive form; mode changeable in options flow (user choice).
  - Suggested phase ordering 0–9 kept unchanged — research → architecture → skeleton is the
    correct dependency order.
- **Open questions carried forward:**
  - Default and minimum alert radius — decide in phase 1 from phase 0 resolution data.
  - `lead_time` value for the polling window (`next_walk − earlier_margin − lead_time`) —
    decide in phase 1 from source update frequencies.
  - How the HACS validation action behaves against a private repo — resolve in phase 2.

## Phase 0 — Research & decisions on data sources

- **Status:** done
- **Date:** 2026-08-24
- **What was built:** `docs/DATA_SOURCES.md` (full research write-up: 20-candidate comparison
  table, ranked recommendation, rejected candidates with reasons, intensity mapping, effective
  resolution table, fallback strategy, request budget, attribution obligations) and
  `CHANGELOG.md`. Claims were verified against live APIs wherever possible, not just docs; those
  observations are marked **(measured)** in the document.
- **Decisions:**
  - **Recommended sources (4):** LibreWXR OPERA radar nowcast (timing precision, +10…+60 min at
    10-min steps), Open-Meteo DWD ICON-EU (reliability baseline), Open-Meteo KNMI HARMONIE AROME
    Europe (independent model family, re-run hourly), MET Norway Locationforecast 2.0
    (provider-level failover, polled only when Open-Meteo fails) — the only combination that is
    free, key-free, covers all of Poland, and is model-independent.
  - **RainViewer rejected** — its public API now serves past frames only and the live
    `radar.nowcast` array is empty; it was the leading candidate at bootstrap.
  - **IMGW-PIB rejected** — radar products are observations (and were 11 h stale when checked),
    and its only forecast product is COSMO GRIB2, which cannot be decoded with the allowed
    dependency set; a listed GRIB file 404'd from the datastore.
  - **DWD ICON-D2 rejected** despite being the only native 15-minutely model: its domain ends at
    ~18.7–19.6° E and excludes most of Poland, Warszawa included.
  - **Source independence is enforced by measurement, not assumption.** Correlated pairs
    (ICON-EU/ICON-Global 0.89, KNMI/DMI HARMONIE 0.76, ECMWF-IFS/MET-Norway 0.71) must never both
    count as independent votes; the recommended trio peaks at 0.61.
  - **Minimum viable source count is 1**, with confidence capped at 0.5 and the result flagged
    degraded; zero sources means `unavailable` and no notification, never a guess.
  - **Common intensity scale is mm/h** (none < 0.1, light 0.1–2.5, moderate 2.5–7.6, heavy ≥ 7.6),
    with Marshall–Palmer used to convert LibreWXR reflectivity (7.0 / 29.4 / 37.1 dBZ boundaries).
  - **Request budget: ≤ 28 HTTP requests/hour while a walk window is near, 0 otherwise**,
    ≤ 200/day — at most 3 % of Open-Meteo's daily allowance on the conservative call-counting
    assumption, and comfortably inside every other provider's limits.
- **Deviations from PLAN.md (recorded before proceeding):**
  1. **The source mix is not all-tiles.** The plan's framing assumed tile/frame sources
     throughout. Only LibreWXR is tile-based; ICON-EU, KNMI HARMONIE, and MET Norway are
     point/grid JSON APIs. Consequence for phase 1: pixel sampling and `Pillow` apply to
     LibreWXR only; the other adapters sample by requesting a small set of coordinates, and
     Open-Meteo returns all sample points and all models in **one** request (5 points × 3 models
     × 24 steps measured at 508 bytes gzipped).
  2. **"Effective resolution in km per cell" was established by measurement** for the point APIs
     (precipitation transects revealing grid-cell plateaus), since the providers publish nominal
     figures only.
  3. **Mixed time steps are unavoidable.** No free source gives Poland native sub-hourly NWP;
     only LibreWXR has a 10-minute step, the rest are hourly. Phase 1's window evaluation must
     handle 10-min and 60-min series together rather than assuming one common step.
- **Open questions carried forward:**
  - Default and minimum alert radius — decide in phase 1. Input is now available: coarsest
    recommended source is ICON-EU at **6.95 km north–south** at 52° N (≈ 10 km if MET Norway is
    contributing), finest is LibreWXR/OPERA at ~2 km.
  - `lead_time` for the polling window — decide in phase 1. Input: LibreWXR publishes frames
    every 10 min, KNMI HARMONIE re-runs hourly, ICON-EU every 3 h; the budget above assumes a
    10-minute cadence and a 30-minute lead time.
  - **LibreWXR's grey-value → dBZ calibration for colour scheme `0` is not documented.** Pin it in
    phase 3 by reading the palette definition in its AGPL-3.0 source and lock it into a fixture
    test. Do not guess it in phase 1.
  - **LibreWXR's operational risk.** OpenAPI reports version `0.1.0`, the forecast is labelled
    experimental, and its terms disclaim any uptime guarantee. The design must stay correct when
    it disappears; if it does disappear permanently, Rainbow Weather (1-min steps, 4 h horizon,
    5 000 free requests/month, user-supplied key) is the documented fallback to re-evaluate.
  - **LibreWXR coverage-tile semantics are ambiguous** (white@128 marks only ~5 % of pixels over
    Poland) — determine in phase 3 whether it means coverage present or a coverage boundary.
  - Whether Open-Meteo counts each coordinate in a multi-coordinate request as a separate call is
    undocumented; the budget assumes the conservative reading. Confirm in phase 3 if a
    rate-limit header ever exposes it.
  - How the HACS validation action behaves against a private repo — still open, resolve in phase 2.

## Phase 1 — Architecture design

- **Status:** done
- **Date:** 2026-08-24
- **What was built:** `docs/ARCHITECTURE.md` filled completely (module layout, data flow with
  the core dataclasses, per-source sampling strategy, exact consensus algorithm with formulas,
  window evaluation + recommendation search + material-change definition, coordinator polling
  windows, resource budget with concrete ceilings, frame cache design, alert radius decision,
  output contract for phases 5–7). `docs/CONFIG.md` updated with the radius/threshold defaults
  and the internal `lead_time` note.
- **Decisions:**
  - **Alert radius: default 5 km, minimum 4 km, maximum 15 km** — min 4 km makes the sampled
    disc (8 km diameter) span ≥ 1 full ICON-EU cell (6.95 km N-S, the coarsest regular source);
    MET Norway's ~10 km cell does not raise the minimum because it is failover-only, its
    single-point sample is by definition one full cell, and failover already caps confidence.
  - **`lead_time` = 30 min** — covers KNMI's hourly publication cadence and ≥ 3 LibreWXR cycles
    before the notification decision at `T − earlier_margin`; matches the phase 0 budget.
  - **Engine and schedule modules are pure** (no I/O, no HA imports, `now` as parameter) — the
    phase 4 testability requirement enforced structurally.
  - **Consensus = weighted vote** (`risk` = weighted wet fraction, `confidence` =
    `|2·risk − 1| × source-count cap`), weights = static reliability (librewxr 1.0, knmi 0.9,
    icon_eu 0.8, metno 0.7) × linear freshness decay (1.0 → 0.5 at 3× update interval, dropped
    beyond) — simple, explainable per-source in the sensor attributes.
  - **Spatial aggregation: p90 over the pixel disc for LibreWXR** (robust to radar speckle),
    **max over the 5 sample points for NWP sources** (smooth fields, conservative).
  - **Windows evaluated on a 10-min UTC grid**; hourly sources contribute as step functions
    (no interpolation). Window dry ⇔ every slot risk < 0.5; window confidence = min slot
    confidence; nearest dry candidate wins, earlier beats later at ties.
  - **Material change** for re-notification: direction change, recommended start moved ≥ 20 min
    vs last notified, verdict flip with 0.4/0.6 hysteresis, or peak intensity class change.
  - **Open-Meteo fetched every 3rd cycle (30 min), not every cycle** — the freshest model
    re-runs hourly, so a 10-min cadence would be ≥ ⅔ guaranteed-identical responses. This
    *lowers* usage vs the phase 0 budget (2/h instead of 6/h); recorded as a budget refinement,
    not a deviation.
  - **Cache stores sampled floats, never raw tiles/responses**, keyed by LibreWXR frame `path`
    (identity that changes on re-issue); 32-entry LRU, persisted via HA Store, ≤ 20 KB.
- **Deviations from PLAN.md:** none. (The plan's `walk_end` for the active polling window is
  made precise as `max(T, recommended_start) + duration`, and the phase 0 request budget is
  refined downward for Open-Meteo — both elaborations, not changes of intent.)
- **Open questions carried forward:**
  - LibreWXR grey→dBZ calibration for colour scheme `0` — pin in phase 3 from the AGPL source,
    lock with a fixture test (unchanged from phase 0).
  - LibreWXR coverage-tile semantics (white@128) — determine in phase 3 (unchanged).
  - Whether Open-Meteo counts each coordinate as a separate call — confirm in phase 3 if a
    rate-limit header exposes it (unchanged).
  - How the HACS validation action behaves against a private repo — resolve in phase 2 (unchanged).
  - Whether p90 for the LibreWXR disc needs tuning against real events — revisit in phase 8
    with measured data if false alarms/misses show up.

## Phase 2 — Repo skeleton + development environment

- **Status:** done
- **Date:** 2026-08-24
- **What was built:** Complete integration skeleton in `custom_components/walk_the_dog/`
  (valid `manifest.json` v0.1.0, minimal `__init__.py` with setup/unload, filled `const.py`,
  stub `config_flow.py` that aborts until phase 5, docstring-stub modules for every file in the
  architecture layout, `strings.json` + `translations/en.json`); HACS/GitHub boilerplate
  (`hacs.json`, `info.md`, README written as-if-public, MIT `LICENSE`, two workflows:
  hassfest + HACS validation, and lint + test); dev environment (`.gitattributes`,
  `.editorconfig`, `.gitignore`, `.env.example`, pinned `requirements-dev.txt`,
  `.pre-commit-config.yaml`, `pyproject.toml` with ruff/pytest config); cross-platform task
  runner `scripts/{setup,lint,format,test,install}.py` (pathlib only, identical on both OSes);
  committed `.claude/settings.json` + `/phase` command; `docs/DEVELOPMENT.md`; first two tests
  (manifest/const consistency, config-entry setup/unload against the real HA test harness).
  Added in a follow-up session, same phase: repository-side GitHub configuration —
  `.github/dependabot.yml`, `.github/ISSUE_TEMPLATE/*` (bug/feature forms, blank issues off),
  `SECURITY.md` (private vulnerability reporting), workflow hardening (read-only `GITHUB_TOKEN`,
  concurrency groups, `push` limited to `main`, weekly scheduled validation run), and
  `scripts/github_setup.py`, which applies the GitHub-side settings themselves through `gh`.
- **Decisions:**
  - **Toolchain is uv-based.** The system Python (3.12 on the Linux machine) cannot install
    current HA test deps — `pytest-homeassistant-custom-component` requires Python ≥ 3.14 since
    HA dropped older versions. `scripts/setup.py` uses uv to provision a Python 3.14 venv
    identically on Windows and Linux; uv itself is the only prerequisite (one-line install
    documented in `docs/DEVELOPMENT.md`).
  - **Pins:** ruff 0.16.4 (same rev in pre-commit), pre-commit 4.6.2, phcc 0.13.357
    (→ homeassistant 2026.8.3 in tests), pytest 9.0.3 — pytest must always match phcc's exact
    pin, bump the two together.
  - **HACS validation runs with `ignore: brands`** — the repo is not in `home-assistant/brands`
    until the phase 9 submission; drop the ignore then. The action authenticates with the
    workflow's own `GITHUB_TOKEN`, so the private repo itself is not a blocker.
  - **A second workflow (lint + test) was added** beyond the plan's hassfest + HACS — CI runs
    exactly `scripts/setup.py` → `lint.py` → `test.py`, i.e. what developers run locally.
    Addition to the plan, not a change of intent.
  - Markdown files are excluded from ruff — current ruff reformats Python code blocks inside
    docs, and the alignment in `docs/ARCHITECTURE.md` is deliberate.
  - `manifest.json`: `integration_type: service`, `iot_class: cloud_polling`, version starts at
    0.1.0; `hacs.json` sets minimum HA 2026.8.0 (matches the test harness version).
  - The config-flow stub aborts with a translated `not_implemented` reason so
    `config_flow: true` is honest before phase 5 ships the wizard.
  - **GitHub settings are code, not clicks.** `scripts/github_setup.py` is idempotent, has a
    `--dry-run`, and tolerates settings the plan/visibility forbids — so the Windows machine and
    any later repo reset reproduce the same configuration instead of relying on memory.
  - **`main` is protected against history loss only** — deletion and force-push blocked, direct
    pushes still allowed, no required status checks: single contributor working on `main`
    (CLAUDE.md workflow rule 4). Release tags `v*` are additionally protected against update and
    deletion, because HACS installs from them and a moved tag changes a published release.
  - **Dependabot watches GitHub Actions only.** The Python pins move in coordinated pairs
    (pytest ↔ phcc, ruff ↔ pre-commit rev), which automated per-package PRs would break.
  - Secret scanning and push protection are requested by the script but expected to be refused
    while the repo is private on a free plan; they apply automatically once it goes public in
    phase 9.
- **Open questions carried forward:**
  - LibreWXR grey→dBZ calibration for colour scheme `0` — pin in phase 3 (unchanged).
  - LibreWXR coverage-tile semantics (white@128) — determine in phase 3 (unchanged).
  - Whether Open-Meteo counts each coordinate as a separate call — confirm in phase 3 (unchanged).
  - ~~CI status must be confirmed on GitHub~~ — **resolved 2026-08-24: both workflows green.**
    `gh` 2.98 is installed and authenticated on the Linux machine, so runs are checkable from a
    session. Two real failures were found and fixed: hassfest rejected `strings.json` /
    `translations/en.json` without a `config.step` key (placeholder added, phase 5 fills it in),
    and HACS failed on the missing description/topics (now set).
  - ~~`scripts/github_setup.py` has to be run once~~ — **run 2026-08-24**; description, topics,
    repo features, Dependabot alerts + security updates and the read-only workflow token are
    applied.
  - **Three settings are blocked by the free plan on a private repo** and must be revisited when
    the repo goes public in phase 9: branch/tag rulesets (HTTP 403, "Upgrade to GitHub Pro or
    make this repository public") and secret scanning + push protection (HTTP 422). Until then
    `main` has no server-side protection at all — force-push discipline is manual. Re-run
    `python scripts/github_setup.py` right after flipping the repo to public.
  - **Two HACS checks are ignored because the repo is private** (`hacsjson`,
    `integration_manifest`): the action reads repository files through the GitHub API and gets
    nothing back, so both parse as `None`. Drop both ignores together with `brands` in phase 9
    and confirm they pass for real. hassfest still validates `manifest.json` on every run.
  - **Dependabot PR #1** (`actions/checkout` 5 → 7) is open and green — merge or close it in the
    next session.
  - Whether p90 for the LibreWXR disc needs tuning — revisit in phase 8 (unchanged).

## Phase 3 — Source clients

- **Status:** done
- **Date:** 2026-08-25
- **What was built:** the whole source layer plus its cache and tests.
  `sources/base.py` (the `SourceAdapter` protocol, `SampleGeometry`, `SourceSeries`,
  `SourceStatus`, `FetchResult`, per-source reliability/cadence/cell-size/attribution tables,
  `RequestBudget`, `Backoff`, `restate`); `sources/librewxr.py` (frame index + tile fetch,
  Web-Mercator disc mask, p90 sampling, grey→dBZ→mm/h); `sources/open_meteo.py` (both models,
  five coordinates, one request); `sources/met_norway.py` (failover-only, `If-Modified-Since`,
  `Expires`); `sources/__init__.py` (registry, concurrent fetch, provider failover,
  `build_user_agent`); `cache.py` (32-entry LRU keyed by frame path, HA `Store` persistence,
  geometry invalidation, 2 h eviction); `intensity_class()` in `const.py`. Seven recorded
  fixtures under `tests/fixtures/` (LibreWXR frame index + three real tiles, three Open-Meteo
  responses, one MET Norway response — all Polish public landmarks, no personal coordinates) and
  **123 tests**, verified green with networking fully disabled.
- **Decisions:**
  - **LibreWXR calibration pinned: `dBZ = grey − 32`.** Read out of the AGPL-3.0 source, not
    guessed: `_dbz_float_to_uint8` encodes `pixel = clamp((dBZ + 32) × 2, 0, 255)` and colour
    scheme 0 renders `pixel // 2` through a table whose row `i` is grey `#iiiiii` at `dBZ = i − 32`.
    Locked by `test_grey_level_calibration`. Verified against live tiles over six Polish cities;
    the lowest level OPERA actually emits is grey 42 (10 dBZ ≈ 0.15 mm/h).
  - **Grey 0 is ambiguous** — no echo *and* no radar coverage both render transparent. Accepted:
    Poland is fully inside OPERA coverage, and the consensus vote already outvotes a wrongly-dry
    `librewxr` (weight 1.0) when both NWP sources say wet (0.9 + 0.8 = 1.7 of 2.7 → risk 0.63).
  - **`p90` is taken with `method="nearest"`**, so the result is an actually observed grey level
    and maps back to a real dBZ step instead of interpolating between palette codes.
  - **Request budgets are enforced over a rolling hour, not per cycle** (`RequestBudget`), because
    that is how `docs/DATA_SOURCES.md` states them: a cold start may legitimately spend several
    requests in one cycle provided the hour stays inside its ceiling. Limits: `librewxr` 20/h,
    `open_meteo` 6/h, `metno` 2/h.
  - **Adapters own their own cadence** (`should_fetch`), so the coordinator in phase 6 just calls
    the registry once per cycle: `librewxr` every cycle, `open_meteo` every 30 min, `metno` only
    while enabled and only after `Expires` and the terms' 10-minute floor.
  - **A skipped or failed cycle re-presents the cached series, never as fresh.** `restate()`
    recomputes age against the current clock, so a kept series flips to `stale` and stops
    contributing on its own.
  - **A timeout is an ordinary provider failure**, not an exception that escapes; only
    `asyncio.CancelledError` propagates, so HA shutdown still cancels cleanly.
  - **`User-Agent` carries the project URL as contact info**, never the user's details — mandatory
    for MET Norway and for LibreWXR (which 403s the default Python agent).
- **Deviations from PLAN.md / earlier phases (recorded before proceeding):**
  1. **Retry backoff is applied *between* cycles, not as sleeps inside one.**
     `docs/DATA_SOURCES.md` budgets "3 attempts with exponential backoff (1, 2, 4 min)"; sleeping
     1–4 minutes inside a coordinator update would block the event loop and nearly fill the
     10-minute cycle. `Backoff` instead arms `next_attempt_at`, and `should_fetch` honours it.
     Same intent — a failing provider is not hammered — without a blocking cycle.
  2. **Open-Meteo `issued_at` is the fetch time, not the model run time.** `/v1/forecast` carries
     no model-run timestamp (checked 2026-08-25; no rate-limit or run header exists either), so
     upstream run age is unmeasurable. Freshness there therefore tracks our own fetch age — which
     is exactly what degrades when Open-Meteo stops answering. `librewxr` and `metno` still use
     real upstream publication times. Noted in `docs/ARCHITECTURE.md` § Consensus scoring.
  3. **Open-Meteo's multi-coordinate response shape differs from the phase 0 assumption.** With
     several coordinates it returns a JSON *list*, one object per coordinate, and with several
     models each variable key is suffixed with the model id
     (`precipitation_icon_eu`, `precipitation_knmi_harmonie_arome_europe`) — not one object
     carrying everything. Same request count and size; only the parser is affected.
  4. **`intensity_class()` landed in `const.py` in phase 3** rather than waiting for the phase 4
     engine, because "intensity mapping per source matches the table in `docs/DATA_SOURCES.md`" is
     a phase 3 acceptance criterion and needs the classifier to be testable.
  5. **`tests/*` gained ruff per-file ignores** (`PLR2004`, `PLR0913`, `PLR0917`, `SLF001`):
     asserting on literal expected values is the point of a fixture test, and pytest injects
     fixtures as positional parameters.
- **Open questions carried forward:**
  - ~~LibreWXR grey→dBZ calibration~~ — **resolved 2026-08-25**, see decisions above.
  - ~~LibreWXR coverage-tile semantics (white@128)~~ — **resolved 2026-08-25.**
    `librewxr.tiles.renderer.compute_coverage_rgba` paints `[255, 255, 255, 128]` wherever the
    **latest frame's value is non-zero**. So it is neither a coverage boundary nor a static radar
    mask: it marks pixels that currently have an echo, which is why only ~5 % of Poland was lit
    when phase 0 checked on a dry day. It cannot distinguish "no radar coverage" from "no rain",
    so the integration does not use it — the architecture never did, and now that is deliberate
    rather than incidental.
  - ~~Whether Open-Meteo counts each coordinate as a separate call~~ — **checked 2026-08-25, still
    unknowable.** A live 5-coordinate, 2-model request returned **no rate-limit, quota or
    remaining-calls header of any kind**, so nothing exposes the accounting. The conservative
    phase 0 budget (each coordinate = one call → 3 % of the daily allowance) stands unchanged.
  - ~~Dependabot PR #1~~ — merged before this phase started (commit `e0ae92d`); no longer open.
  - **Tests cannot run natively on Windows.** Home Assistant's `runner.py` imports `fcntl`, so
    `pytest-homeassistant-custom-component` fails at plugin import before any test runs — this
    affects the whole suite, not just phase 3, and was simply never hit before because the earlier
    phases' tests were run on the Linux machine. Lint, format, setup and install all work on both
    OSes. Workaround documented in `docs/DEVELOPMENT.md`: run the suite in a Linux container
    (`--network none` doubles as the offline proof). CI is unaffected.
  - **LibreWXR fuses NWP model layers into its tiles where radar does not reach** (its
    `docs/coverage.md`, and `_fill_ecmwf_fallback` in its renderer). Over Poland the OPERA
    composite wins, so the phase 0 independence measurement holds — but outside radar coverage its
    "independent radar vote" would silently become a model vote. Re-check in phase 8 if false
    alarms show up.
  - Whether p90 for the LibreWXR disc needs tuning against real events — revisit in phase 8
    (unchanged).
  - Three GitHub settings blocked by the free plan on a private repo, and two ignored HACS checks
    — revisit when the repo goes public in phase 9 (unchanged from phase 2).

## Phase 4 — Sampling + consensus scoring engine

- **Status:** done
- **Date:** 2026-08-25
- **What was built:** the decision core, as three pure modules plus their tests.
  `engine/grid.py` (10-minute UTC grid: `floor_slot` / `ceil_slot` / `slots_between` /
  `slots_for_window`, and `align()`, which projects any source's own step onto the grid as a
  step function); `engine/consensus.py` (`freshness`, `source_weight`, `SlotScore`, `Consensus`,
  `build_consensus` — the weighted vote, the source-count confidence cap, and the restatement of
  each adapter's status into what it actually contributed); `engine/window.py`
  (`WindowVerdict`, `SourceBreakdown`, `Recommendation`, `evaluation_slots`, `evaluate_window`,
  `candidate_starts`, `source_breakdown`, `recommend`, `is_material_change`);
  `engine/__init__.py` re-exports the public API and documents the three-call sequence the
  coordinator will use in phase 6. Tests: `tests/test_grid.py`, `tests/test_consensus.py`,
  `tests/test_window.py`, `tests/test_engine_purity.py`, plus `make_series` / `make_status`
  factories in `conftest.py` — **84 new tests, 207 in total**, verified green with networking
  fully disabled (`docker run --network none`).
- **Decisions:**
  - **A slot no source covers is absent, never zero.** `align()` returns a mapping, not a dense
    array, so "we do not know" and "no rain" can never collapse into the same value — the single
    property the dry/wet verdict, the `out_of_range` status and the `horizon_limited` flag all
    rest on.
  - **`direction` gains a fifth value, `unknown`**, for a scheduled window no source reaches at
    all. The architecture listed four directions but the sensor contract already had an `unknown`
    state; carrying it on the recommendation makes the mapping 1:1 and keeps "no data" from ever
    being rendered as "walk as planned". Recorded in `docs/ARCHITECTURE.md` § Output.
  - **A window is dry only when every one of its slots is *covered* and below the threshold.**
    A partly covered window is `horizon_limited`, never dry — so a forecast running out mid-walk
    pushes the recommendation into covered ground instead of silently approving the walk.
  - **The engine assigns `out_of_range`, the adapters do not.** A source can be perfectly fresh
    and still say nothing about a distant walk; that is a different fact from being stale, and
    the sensor shows both. A source's *cycle* status stays `ok` when it covered any scored slot,
    while its *window* verdict is `unknown` — two questions, two answers.
  - **Candidate start times sit on the grid, not at exact offsets from the walk.** A 07:15 walk
    is offered 07:10 and 07:20 — the times the forecast actually resolves. Candidates are then
    ordered by true distance from the walk with earlier winning ties, which stays correct even
    when the walk time is not a multiple of 10 minutes.
  - **`Consensus` carries the aligned per-source values**, not just the scored slots, so the
    per-source breakdown is computed from the same numbers the vote used rather than re-derived.
  - **Purity is tested structurally**, not just behaviourally (`tests/test_engine_purity.py`
    parses the engine's AST): a stray `datetime.now()` would undo the whole testability
    argument without failing a single behavioural test.
- **Deviations from PLAN.md (recorded before proceeding):**
  1. **Phase 4 task 1 (spatial sampling) was already delivered in phase 3.** The architecture's
     own module layout puts spatial aggregation in the adapters — p90 over the pixel disc for
     LibreWXR, max over the five sample points for the NWP sources — and phase 3's acceptance
     criteria required it there. The engine therefore consumes one intensity per source per
     step and does no spatial work at all; the "≥ 1 full cell of the coarsest source" guarantee
     is structural, held by the 4 km minimum radius decided in phase 1.
  2. **Material-change rule 3's dry→wet half is unreachable in practice.** `direction == none`
     is equivalent to "the scheduled window is dry", so any dry→wet flip already changes
     `direction` and fires rule 1. The rule is implemented and unit-tested as specified, as a
     backstop; its wet→dry half *is* reachable (a horizon-limited window is not dry at any
     risk).
  3. **The test-series factories take an explicit forecast step.** A source's publication
     interval (`UPDATE_INTERVAL_S`, used for freshness) and its forecast step are different
     numbers; `conftest.STEP_S` names the latter so the two are never confused in a fixture.
- **Open questions carried forward:**
  - Whether p90 for the LibreWXR disc needs tuning against real events — revisit in phase 8
    (unchanged since phase 1).
  - LibreWXR fuses NWP model layers into its tiles outside radar coverage — re-check in phase 8
    if false alarms show up (unchanged from phase 3).
  - **Tests cannot run natively on Windows** (HA imports `fcntl`); this phase's suite was run in
    the documented Linux container with `--network none`, which doubles as the offline proof.
    Lint and format run natively. Unchanged from phase 3.
  - Three GitHub settings blocked by the free plan on a private repo, and two ignored HACS
    checks — revisit when the repo goes public in phase 9 (unchanged from phase 2).

## Phase 5 — Config flow wizard + options flow

- **Status:** done
- **Date:** 2026-08-25
- **What was built:** the complete setup wizard and options flow, plus the schedule model they
  write. `config_flow.py`: `_WalkFlowSteps` (the schedule and parameter steps, shared verbatim by
  both flows), `WalkTheDogConfigFlow` (step 1 location on a `LocationSelector` pre-filled with the
  HA home, then the shared steps, then entry creation) and `WalkTheDogOptionsFlow`
  (`OptionsFlowWithReload`, entering the same shared steps prefilled from stored options).
  `schedule.py` (pure): `DAY_KEYS` / `SCHEDULE_KEYS`, `normalize_time`, `normalize_times`,
  `normalize_schedule`, `expand`, `ScheduleError`. `const.py` gained the config-flow input bounds
  and the notify-service constants. Full English `strings.json` + `translations/en.json` for both
  flows, with translated `schedule_mode` and `intensity_threshold` choices.
  `manifest.json` gained `single_config_entry`. `docs/CONFIG.md` rewritten to describe the
  implementation and pin the storage shape. Tests: `tests/test_config_flow.py`,
  `tests/test_schedule.py`, `tests/test_strings.py`, and `tests/test_engine_purity.py` renamed to
  `tests/test_purity.py` and extended to cover `schedule.py` — **64 new tests, 271 in total**,
  green with networking fully disabled (`docker run --network none`). hassfest was also run
  locally against the real action image and passes.
- **Decisions:**
  - **Location is entry data; everything else is options.** `PLAN.md` scopes the options flow to
    steps 2–3, so the location is set once. It is also the only value whose change invalidates the
    whole frame cache, which makes "set once" the honest contract rather than a limitation.
  - **`single_config_entry: true`.** `CLAUDE.md` specifies one shared coordinator and phase 6
    specifies exactly one sensor; letting a second entry exist would contradict both. HA then
    aborts a second flow with its own `single_instance_allowed` string, so no extra strings are
    needed.
  - **Step 2 is two forms, not one.** HA renders a form from a fixed schema, so "the form adapts
    to the chosen mode" can only mean: submit the mode (`schedule_mode`), then render exactly the
    slots that mode uses (`schedule_times`). Recorded in `docs/CONFIG.md`.
  - **The schedule is stored under the mode's own keys** (`all` / `weekday`+`weekend` /
    `mon`…`sun`), never pre-expanded to seven days. Storage then says what the user actually
    chose, and switching mode cannot leave the previous mode's slots behind. `expand()` is the
    single place that knows what the keys mean.
  - **An empty slot is allowed, an empty week is not.** "No weekend walks" is a real answer; a
    schedule with no walk at all has nothing to predict for and is rejected with `no_walk_times`.
  - **Times are entered with the frontend's native time input** (`TextSelector`, `type=time`,
    `multiple=True`), which gives a real add/remove list of picker fields. Values are normalized
    server-side anyway (`H:MM`, `HH:MM:SS` and duplicates are all accepted) because the browser
    decides what it sends.
  - **The > 30 min warning is its own confirmation step** (`long_walk`), not an inline error.
    Declining returns to the parameter form with the entered values still in it, so the user can
    simply lower the number — a red error under a legitimate value would have been a lie.
  - **The notification device is optional and accepts a custom value.** A companion-app service
    may not be registered yet at setup time; a typed value is still validated to be a
    `mobile_app_*` service and is stored without the `notify.` prefix.
  - **Optional options left empty are absent from storage, never `null`** — so clearing a field
    in the options flow really removes it, and phase 6 can test presence rather than truthiness.
  - **`OptionsFlowWithReload`** applies option changes by reloading the entry. Phase 6 must
    therefore *not* register a config-entry update listener; the class forbids combining the two.
- **Deviations from PLAN.md (recorded before proceeding):**
  1. **The schedule model landed in `schedule.py` in phase 5**, not phase 6. `docs/ARCHITECTURE.md`
     already assigns the walk-schedule model to that module, and phase 5's task 5 (validation)
     needs it; phase 6 adds only the next-walk computation on top. Same precedent as
     `intensity_class()` landing early in phase 3.
  2. **Phase 2's `not_implemented` abort string was removed** — the wizard exists now, so nothing
     can reach it.
  3. **`tests/test_engine_purity.py` became `tests/test_purity.py`** and now checks `schedule.py`
     too. The architecture always called both `engine/*` and `schedule.py` pure; only the engine
     existed when the test was written.
- **Open questions carried forward:**
  - **The location cannot be changed after setup.** `PLAN.md` deliberately scopes the options flow
    to steps 2–3, so moving house means removing and re-adding the entry. If that proves annoying
    in real use, the HA-idiomatic fix is an `async_step_reconfigure` reusing step 1 — deferred, not
    forgotten.
  - **The flows have not been exercised in a real Home Assistant UI yet.** Everything here is
    driven through `hass.config_entries` in tests and validated by hassfest; the first visual pass
    happens with the phase 6 smoke test via `scripts/install.py`.
  - Whether p90 for the LibreWXR disc needs tuning against real events — revisit in phase 8
    (unchanged since phase 1).
  - LibreWXR fuses NWP model layers into its tiles outside radar coverage — re-check in phase 8
    if false alarms show up (unchanged from phase 3).
  - **Tests cannot run natively on Windows** (HA imports `fcntl`); this phase was developed on the
    Windows machine and its suite run in the documented Linux container with `--network none`,
    which doubles as the offline proof. Lint, format and hassfest run there too (hassfest through
    its own container image). Unchanged from phase 3.
  - Three GitHub settings blocked by the free plan on a private repo, and two ignored HACS checks
    — revisit when the repo goes public in phase 9 (unchanged from phase 2).

## Repository made public — 2026-08-25 (out of phase)

- **Status:** done
- **What changed:** the repository was flipped to public after phase 5, ahead of the phase 9
  schedule, and the settings that GitHub's free plan refuses on a private repo were applied.
  `README.md` and `info.md` gained work-in-progress banners naming exactly what works (install,
  setup wizard, options flow) and what does not (sensor, switch, notifications, events).
  `docs/DEVELOPMENT.md` gained a HACS custom-repository deploy route and a HAOS/Samba route.
  `.github/workflows/validate.yml` dropped two of its three HACS ignores. `PLAN.md` phase 9 was
  updated to mark tasks 2 and 4 as partly done.
- **Why (the deviation from PLAN.md, recorded before proceeding):** the test instance runs
  **Home Assistant OS**, which has no config folder a dev machine can write to, so
  `scripts/install.py` cannot reach it without adding a Samba share. HACS custom repositories
  require a public repo. Going public early makes the whole remaining project testable on the
  real target with a one-click redownload per push — and it *raises* the security bar rather
  than lowering it, because secret scanning, push protection and rulesets are free only on
  public repos.
- **Secrets audit (phase 9 task 2, done early).** Method, over `git rev-list --all`:
  1. files ever added matching `.env`, `settings.local.json`, `*.pem`, `*.key`, `secrets` — none;
  2. token-shaped strings (`gh[pousr]_…`, `eyJ…` JWTs, `api_key=…`, `Bearer …`) — none;
  3. IPv4 addresses, `homeassistant.local`, `duckdns`, `nabu.casa` — none;
  4. every coordinate in the tree reviewed by hand — all are the documented public landmarks
     (Warszawa centre 52.2297/21.0122, the Sejny area 54.0191/23.0081) or Open-Meteo's
     grid-snapped echoes of them. No personal coordinates.
  The author email in commit metadata is public now; that is expected for a repo owner.
  **Re-run this audit over the commits added since, before tagging `v1.0.0` in phase 9.**
- **Decisions:**
  - **No release tag yet.** With no releases, HACS installs a custom repository from the default
    branch, so every push to `main` is immediately installable — exactly what is wanted during
    development. `v1.0.0` in phase 9 remains the first real release.
  - **`hacsjson` and `integration_manifest` ignores dropped** from the HACS validation workflow.
    They only ever existed because the action reads repository files through the GitHub API and
    got nothing back for a private repo. `brands` stays until the phase 9 brands PR.
  - **The WIP banners are load-bearing** while the repo is public and installable but incomplete.
    Removing them is now an explicit phase 9 task.
- **Bug found and fixed while doing this:** `scripts/github_setup.py` enabled secret scanning
  with `gh api -f "security_and_analysis[secret_scanning][status]=enabled"`. `gh` passes
  bracketed field names through literally, GitHub ignores the unknown key and still answers
  **200**, so the script printed `ok` while changing nothing — a false positive that was
  invisible while the call was expected to fail anyway on a private repo. It now sends nested
  JSON on stdin, like the ruleset calls do, and both settings verify as `enabled`.
- **Now active on the repository:** secret scanning, push protection, `main-protection`
  (blocks deletion and force-push; direct pushes still allowed), `release-tags` (blocks
  deletion, update and force-push on `v*`), Dependabot alerts and security updates, read-only
  workflow token.
- **Open questions carried forward:**
  - The HACS custom-repository install has not been exercised yet — first run is the phase 5 UI
    check on the HAOS instance.
  - Whether the two newly-unignored HACS checks pass for real — the next Validate run answers it.

## Phase 6 — Coordinator, entities, notifications, events

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

## Phase 7 — Localization + branding

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

## Phase 8 — Performance pass

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

## Phase 9 — Docs, release 1.0.0

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**
