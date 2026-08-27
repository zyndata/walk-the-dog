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

- **Status:** done (one acceptance criterion pending the user's manual smoke test — see below)
- **Date:** 2026-08-25
- **What was built:** the whole runtime, wiring phases 3–5 together.
  `coordinator.py`: `WalkData` (the published result plus `payload()`, the single serialization
  both outputs use) and `WalkCoordinator` — walk resolution, polling windows, the cycle
  (registry fetch → cache maintenance → `build_consensus` → `recommend`), timer arming and the
  enable gate. `schedule.py` gained `walk_times_on` and `walks_from`, still pure. `notifier.py`
  (renamed from `notify.py`): arming at `T − E`, material-change gating, auto-mute, the event,
  the translated texts. `entity.py`: the shared service-device base. `sensor.py`: the single
  enum recommendation sensor. `switch.py`: the `RestoreEntity` alerting switch. `__init__.py`
  now builds the coordinator, loads the frame cache and forwards both platforms.
  `strings.json` / `translations/en.json` gained the `entity` section (both names, the sensor's
  four states, every attribute label) and the notification texts. `docs/CONFIG.md` documents the
  entities, sensor states and attributes, notification rules and the complete
  `walk_the_dog_alert` payload; `docs/ARCHITECTURE.md` records the module renames and the
  anchored cycle grid. Tests: `tests/test_coordinator.py`, `tests/test_notifier.py`,
  `tests/test_entities.py`, a rewritten `tests/test_init.py`, and `walks_from` coverage in
  `tests/test_schedule.py` — **48 new tests, 319 in total**, green with networking fully
  disabled (`docker run --network none`). hassfest was run locally against the real action
  image and passes.
- **Decisions:**
  - **`notify.py` became `notifier.py`.** A module named after a platform inside an integration
    *is* that platform to Home Assistant. Shipping a non-platform `notify.py` is a trap for
    anyone who later adds real notify-platform support, so the architecture's filename was
    changed rather than kept. (Deviation from `docs/ARCHITECTURE.md`, recorded below.)
  - **There is no `update_interval` at all.** The coordinator holds exactly one
    `async_track_point_in_time` timer, re-armed in `_async_refresh_finished` after every cycle.
    That is the only way "zero polling outside the window" can be structurally true rather than
    a matter of an interval being long; it also makes every scheduling test exact.
  - **The 10-minute cycle grid is anchored to the window start, not to the wall clock.** Since
    `lead_time` (30 min) is a whole number of slots, a cycle then lands exactly on `T − E`. The
    architecture promised "the notification fires at `T − E`"; anchored to the wall clock, a
    07:15 walk would have been notified up to 10 minutes late. Recorded in
    `docs/ARCHITECTURE.md` § Coordinator scheduling.
  - **The coordinator starts disabled and the switch turns it on.** The alternative — start
    enabled, let the switch turn it off — makes one fetch on every restart before the restored
    state arrives, which contradicts "zero requests while the switch is off". Consequence,
    accepted and documented: disabling the switch *entity* in the entity registry leaves the
    integration permanently idle.
  - **Notification texts live under the `common` key of `strings.json`.** hassfest rejects any
    top-level key it does not know (verified: a `notifications` key fails validation), and
    `common` is the only allowed home for a user-facing string that belongs to no form and no
    entity. Keys carry a `notification_` prefix and are read at runtime through
    `homeassistant.helpers.translation`, so phase 7 translates them like everything else.
  - **One serialization for both outputs.** `WalkData.payload()` produces the sensor attributes
    and the event payload from the same code, so an automation and the UI can never disagree
    about what a recommendation said.
  - **Scored fields are `null`, never `0`, when no source reaches the walk.** `risk`,
    `confidence` and `expected_intensity` are omitted rather than defaulted — the same principle
    as phase 4's "a slot no source covers is absent, never zero", carried out to the user-facing
    contract.
  - **A muted alert is suppressed, not queued.** The material-change state advances whether or
    not the push went out, so coming home does not release a message about a decision that has
    since moved on. `PLAN.md` says auto-mute "suppresses"; this makes that precise.
  - **The active window is resolved from the current recommendation.** `_walk_end` is
    `max(T, recommended_start) + D`, so a "go later" recommendation genuinely extends polling
    past the scheduled walk, and the coordinator moves to the next walk exactly at that end.
  - **No config-entry update listener is registered.** `OptionsFlowWithReload` (phase 5) forbids
    combining the two; the entry reload is the only path an option change travels.
  - **Both entities sit on one service device** named after the config entry, so the sensor and
    the switch group together in the UI.
- **Deviations from PLAN.md / earlier phases (recorded before proceeding):**
  1. **`notify.py` → `notifier.py`** (reason above). `docs/ARCHITECTURE.md` § Module layout and
     its data-flow diagram were updated to match.
  2. **`entity.py` is a new module** not in the phase 1 layout — the shared `CoordinatorEntity`
     base holding the device info. Two entities repeating the same `DeviceInfo` would be the
     alternative; this is an addition, not a change of intent.
  3. **The notification is dispatched from the ordinary update cycle, not from its own
     time-point listener.** `docs/ARCHITECTURE.md` § Coordinator scheduling described a separate
     listener armed at `T − E`. Anchoring the cycle grid to the window start makes a cycle land
     exactly on `T − E`, so a second timer would fire at the same instant on the same data —
     same behaviour, one timer fewer. Recorded in the doc.
  4. **`walks_from` lives in `schedule.py`**, as phase 1 assigned it, but phase 5 had already
     moved the rest of the schedule model there; phase 6 only added the next-walk computation on
     top, as phase 5's deviation note anticipated.
- **Manual smoke test — still to be run by the user (the one open acceptance criterion).**
  The test instance is Home Assistant OS, so the deploy route is HACS, not `scripts/install.py`
  (see *Repository made public*); this session deliberately did not write into the live config
  folder, because overwriting a HACS-managed copy from a dev machine would confuse HACS's own
  version tracking. Procedure:
  1. HACS → **Walk the dog** → **Redownload** (default branch) → restart Home Assistant.
  2. Settings → Devices & services → **Walk the dog**: the device should carry exactly two
     entities, `sensor.walk_the_dog_walk_recommendation` and `switch.walk_the_dog_alerting`.
  3. Outside a walk window the sensor is `unknown` with `polling: false` and `alerting: true`;
     `scheduled_start` should name the next walk.
  4. To simulate a risky walk without waiting: in the options flow set a walk time about
     40 minutes ahead and the earlier margin to 10 minutes, so the polling window opens at once.
     Within a cycle the sensor should populate `risk`, `confidence` and `sources`; if the
     forecast is wet, a push notification arrives at `walk − 10 min`.
  5. Turn the switch off and confirm the sensor's `polling` attribute stays `false` and nothing
     further is fetched (Settings → System → Logs, debug logging for `walk_the_dog`).
  Record the result here next session.
- **Open questions carried forward:**
  - **The manual smoke test above has not been run.** Everything is verified by the automated
    suite and by hassfest; nothing has yet run against live providers inside a real Home
    Assistant. First real-world contact is the item to close at the start of the next session.
  - **Disabling the switch entity in the entity registry leaves the integration idle for good**
    (consequence of the coordinator starting disabled). Harmless but surprising; revisit in
    phase 8 if it proves confusing, e.g. by persisting the enabled flag on the coordinator
    instead.
  - The location cannot be changed after setup — `async_step_reconfigure` deferred (unchanged
    from phase 5).
  - Whether p90 for the LibreWXR disc needs tuning against real events — revisit in phase 8
    (unchanged since phase 1).
  - LibreWXR fuses NWP model layers into its tiles outside radar coverage — re-check in phase 8
    if false alarms show up (unchanged from phase 3).
  - **Tests cannot run natively on Windows** (HA imports `fcntl`); this phase was developed on
    the Windows machine and its suite run in the documented Linux container with
    `--network none`, which doubles as the offline proof. Lint, format and hassfest run there
    too. Unchanged from phase 3.
  - Two HACS ignores and the brands PR — revisit in phase 9 (unchanged).

## Post-phase-6 change — per-walk notification targets

- **Status:** done (code, tests and docs; the live re-test is the user's next step)
- **Date:** 2026-08-25
- **Why it exists:** not a phase. It came out of the first real Home Assistant test of phase 6:
  the morning walk and the evening walk are usually different people's job, so a single
  entry-wide `notify_service` is the wrong shape. **Deviation from `PLAN.md`, recorded here
  before proceeding, as workflow rule 3 requires.** No phase was started or advanced.
- **What was built:**
  - `schedule.py`: a walk now has an identity. `Walk(start, slot, time)` carries the UTC instant
    **and** the `(slot key, configured local time)` pair it came from; `expand()` yields those
    pairs per weekday; `walks_from()` and `walk_times_on()` return `Walk` objects;
    `target_key()` renders the pair as the storage key and `configured_walks()` lists every walk
    in schedule-form order. Still pure.
  - `config_flow.py`: a new step `walk_target`, shown once per configured walk between the walk
    times and the parameters, asking for that walk's notification devices (multi-select over the
    registered `mobile_app_*` services, custom values allowed) and a *never alert about this
    walk* switch. Stale targets are pruned when a walk time is deleted.
  - `notifier.py`: `WalkTarget(services, muted)`; the push goes to the walk's own devices, or to
    the entry-wide default when it has none, and an unregistered device no longer silences the
    others.
  - `coordinator.py`: holds a `Walk` instead of a bare datetime and hands the notifier the
    walk's target each cycle.
  - `strings.json` / `translations/en.json`: the new step, and `common.walk_slot_*` day labels
    read back at runtime for its description.
  - `docs/CONFIG.md` § Per-walk alerts and `docs/ARCHITECTURE.md` § Coordinator scheduling.
  - Tests: **17 new (336 total)**, green offline — per-walk devices, several devices at once,
    per-walk mute, the default fallback, another walk's settings not leaking, one step per walk
    with its placeholders, storage shape, pruning, and validation of a typed device name.
- **Decisions:**
  - **A walk is identified by `(slot key, configured time)`, not by its UTC instant.** The pair
    is what the user typed; the instant moves with daylight saving and would detach a walk from
    its devices twice a year. It also keeps a 07:00 weekday walk distinct from a 07:00 weekend
    walk.
  - **One form per walk, not one form with a field per walk.** A form's schema is fixed before
    it is rendered, so dynamic field keys would have to be their own labels — untranslatable,
    which the project's i18n rule forbids. The cost is one extra click per walk in the options
    flow; the day/time and a `Walk 2 of 3` counter go in the step description via placeholders.
  - **Day labels live under `strings.json` → `common`** (`walk_slot_all`, `walk_slot_mon`, …) and
    are read at runtime with `async_get_translations`, exactly like the notification texts.
    `common` is the only top-level key hassfest allows for prose that belongs to no form field.
  - **An empty device list means "use the default device", never "notify nobody".** Silencing a
    walk is what the mute switch is for, so the two can never be confused — and a user who wants
    to keep a device list while going quiet does not have to delete it.
  - **A walk left at the defaults stores nothing.** `walk_targets` only holds walks the user
    said something about, so an entry configured before this change keeps behaving identically
    and no migration is needed.
  - **A per-walk mute behaves exactly like auto-mute:** the event still fires with
    `muted: true`, the push does not go out, and the material-change state advances either way.
  - **`notify_service` was not removed**, only relabelled *Default notification device*: it is
    the fallback for every walk without devices of its own, and it keeps existing entries working.
- **Open questions carried forward:**
  - **The manual smoke test of phase 6 still has not been run**, and now covers this change too:
    set different devices on two walks and confirm each phone gets only its own walk's alert.
    The procedure is in the phase 6 section below; step 2 now has one extra form per walk.
  - `hassfest` was not re-run for this change (it needs the action container); the automated
    suite and `ruff` are green. Run it with the phase 7 tooling pass.
  - Everything else carried forward from phase 6 is unchanged.

## Post-phase-6 change — CHMI CZRAD added as a fifth, regional source

- **Status:** done (code, tests and docs; verified against the live services)
- **Date:** 2026-08-26
- **Why it exists:** not a phase. The maintainer asked for the Meteor source in
  [docs/SOURCE_meteor_androworks.md](docs/SOURCE_meteor_androworks.md) to be wired in, and then
  for the endpoints to be tested and the calibration checked against LibreWXR over
  Bielsko-Biała. **Deviation from `PLAN.md`, recorded here as workflow rule 3 requires.** No phase
  was started or advanced; phase 7 is still `not started`.

- **What the live probing established (2026-08-26, ~07:40–07:55 UTC).** This is the part worth
  reading before touching this source again.
  1. **Meteor's endpoints do not serve frames.** `http://meteor.androworks.org/v2/feed` answers 200
     with `Content-Length: 0` — the source note's "the response body *is* the newest frame" is
     wrong. Retried with a stale `X-Frame-Date` request header, with `?date=`, and against
     `11.` and `111.meteor.androworks.org`: always empty. Every documented frame path
     (`/v2/czrad-z_max3d_masked/…`, `/v2/czrad-z_max3d_fct_masked/…`) returns **404** on
     `meteor.androworks.org` and on all three `*.fbck` hosts.
  2. **`X-Next-Query` is a delay in milliseconds**, not epoch millis: 45 935 and ~240 000 observed
     against a 5-minute cadence. **`X-Future-Levels` is never sent.** **`X-Step-Min` is 5**, not 10.
  3. **The products are CHMI's own and CHMI publishes them directly.** `pacz2gmaps3.z_max3d` is a
     CHMI filename; `opendata.chmi.cz/meteorology/weather/radar/composite/` serves `maxz/png/`
     (observation) and `fct_maxz/png/` (forecast) over HTTPS, with `radar_description_en.pdf` and a
     published colour scale beside them. That is the upstream the source note itself recommended.
  4. **The frame is 680 × 460**, not 597 × 377 with an 82 px margin. CHMI publishes the extent of
     the whole image (E 11.267–20.770, N 48.047–52.167) *and* of the data inside it
     (E 11.267–19.624, N 48.047–51.458); applying the first to the real frame puts the data
     rectangle at exactly (0, 82)–(598, 460), 1.005 km/px. The app's numbers were that same
     rectangle inset by a pixel.
  5. **The calibration assumption was right, and is now a fact.** CHMI's legend prints 4, 8, 12 …
     60 dBZ against the 15 colours, and its mm/h decades (0.1, 1, 10, 100) sit one dBZ below the 8,
     24, 40 and 56 labels — where `Z = 200·R^1.6` puts them (7.01, 23.01, 39.01, 55.01). CHMI's own
     conversion *is* the project's Marshall-Palmer inversion.
  6. **The `*_masked` products the app used are undecodable.** They render echo with blending, so
     pixels are off-palette; over Bielsko-Biała one read `#B1B1D0`, whose nearest palette neighbour
     is the white top of the ramp — **205 mm/h reported for light drizzle**. The unmasked products
     carry exact palette colours.
  7. **Cross-check against LibreWXR over the same 5 km disc, same p90, same minute:** OPERA grey 44
     = 12 dBZ = 0.205 mm/h; CZRAD level 3 = 12 dBZ = 0.205 mm/h. Over the following hour OPERA read
     0.18–0.37 mm/h and CZRAD 0.115–0.205 mm/h — same class, same trend, CZRAD a step lower.
  8. **CHMI's forecast is one tar per run** (`ft60s10` = to 60 min, step 10), 92 KB, holding all six
     frames. A cycle is therefore **2 requests**, not 7.

- **Follow-up question from the maintainer: is OPERA or CHMI more accurate inside the CHMI box, and
  if they are the same, should each region use just one source?** Answer: no, and the reason is
  the opposite of what the question assumed.
  1. **Beam geometry.** Over Bielsko-Biała the only CZRAD radar in range is Skalky at **167 km**,
     where the 0.5° beam centre is **3.87 km** up and the beam is 2.9 km wide. OPERA gets the
     Polish radar **Ramża at 44 km**, beam centre **0.85 km**, width 0.76 km. Brdy-Praha is 377 km
     away and contributes nothing. CHMI's own limit for intensity estimation is
     "approximately 150–200 km", so Bielsko-Biała sits at its edge. **Around Bielsko-Biała CHMI is
     the weaker-sighted radar source, not the stronger one** — swapping OPERA out there would trade
     the best-placed instrument for the worst-placed one.
  2. **They are not "practically the same".** 286 grid points across the domain, 64 with echo,
     both composites on the same 5 km discs and the same p90 (2026-08-26 08:20–08:25 UTC): CZRAD
     reads ~3× lower in mm/h (≈7 dBZ), and **18 points had rain in OPERA and none in CZRAD, against
     2 the other way.** A region-switched design would therefore reach different verdicts on the
     same weather either side of an invisible line.
  3. **The correlation worry is smaller than recorded earlier.** The two composites are dominated
     by *different* radars over south-western Poland, so they are closer to independent there than
     "OPERA ingests the Czech radars" implied. That argues for keeping both, not for dropping one.
  4. **What is still unknown: which of the two is right in absolute terms.** The ~3× gap persists
     at 0–80 km from the Czech radars, where overshoot cannot explain it, and the miss rate does
     not rise cleanly with range. LibreWXR's known NWP-layer fusion is an equally good suspect for
     inflating the other side. Needs rain-gauge ground truth; carried forward.

- **What was built:**
  - `sources/chmi.py`: the adapter. Run stamps computed from the clock on CHMI's 5-minute grid
    (no feed exists and none is needed), forecast archive → 6 frames, observed frame → one more,
    projection from CHMI's published extent, exact palette matching, p90 over the disc, and
    `range_factor()` — the consensus weight scaled by distance to the nearest CZRAD radar.
  - `sources/base.py`: `chmi` added to `RELIABILITY` (0.95), `UPDATE_INTERVAL_S` (5 min),
    `CELL_KM` (1.0) and `ATTRIBUTION`; new `STATE_NOT_APPLICABLE`; the Marshall-Palmer
    `dbz_to_mm_per_h` moved here from `librewxr.py` (re-exported there) so both radar sources share
    one reflectivity→mm/h conversion.
  - `sources/__init__.py`: the registry asks the adapter once whether the location is covered and
    skips it entirely when it is not.
  - `cache.py`: 32 → 48 entries, now shared by both image sources.
  - `const.py`: `SOURCE_CHMI`.
  - `scripts/make_chmi_fixtures.py` + `tests/fixtures/chmi/` (a real observed frame, a real
    forecast archive, and two synthesized frames).
  - Docs: `docs/DATA_SOURCES.md` (a full § CHMI including the probing evidence and the
    cross-check table), `docs/ARCHITECTURE.md`, `docs/CONFIG.md`, `docs/DEVELOPMENT.md`,
    `README.md`, `info.md`.
  - Tests: **67 new (403 total)**, green offline (`docker run --network none`); `ruff` clean and
    `hassfest` passing against the real action image.

- **Decisions:**
  - **The source id is `chmi`, not `meteor`.** Meteor turned out to be a delivery path that does
    not deliver; the data is CHMI's and comes from CHMI. Naming a CHMI source after an app that is
    not in the request path would be misleading in the sensor attributes, and nothing is released
    yet, so the rename is free. Meteor is credited as the discovery path in the docs.
  - **Colour matching is exact, and unrecognised colours are "no data".** Nearest-colour is what
    turned a blended `png_masked` pixel into 205 mm/h. A frame whose sampled disc is more than 20 %
    unrecognised fails outright. Producing confident nonsense is the one failure this project must
    not have, so guessing is refused at the pixel level.
  - **The unmasked products, despite `png_masked` being meteorologically better.** "Precipitation
    reaching the ground" is the more relevant field for a dog walk, but it is only published
    blended. Exact values from a slightly less apt product beat guessed values from the apt one.
  - **Reliability 0.95, and then scaled by range.** The provisional-calibration discount is gone
    now that CHMI's scale is verified. What remains as a *static* discount is quantisation: 15
    steps of 4 dBZ against LibreWXR's 1 dBZ ramp, and at the light end one step separates 0.065
    from 0.115 mm/h — dry from wet against the default threshold.
  - **`chmi` is the first source whose weight depends on the user's location.** CHMI has exactly
    two radars, so unlike every other source its measurement quality varies systematically across
    its own coverage. `range_factor()`: full weight to 120 km, linear decay to 0.5 at 200 km,
    floored. Bielsko-Biała → 0.705, so the source votes at 0.67. That is deliberately low enough
    that `librewxr` wet against `chmi` dry still scores 1.00/1.67 = 0.60, i.e. wet — the
    better-sighted radar wins the slot.
    - **The curve is justified by beam geometry and CHMI's own stated 150–200 km ceiling, not
      fitted to measured error.** The domain sweep showed a large overall gap but no clean
      range gradient in the miss rate, so it does not pin the shape. Said plainly in the code
      docstring and in `docs/DATA_SOURCES.md` so nobody later mistakes it for an empirical fit.
    - Floor is 0.5 rather than 0: past 200 km the source still measures something real, it just
      must not outweigh a radar standing 44 km away. Coverage gating stays purely geographic.
    - The adjusted weight goes on the `SourceSeries`, so `engine/consensus.py` needs no special
      case — it already multiplies `series.reliability` by freshness.
  - **Run discovery is computed, not fetched.** Runs land on a fixed 5-minute grid, so the adapter
    floors `now − 2 min` and steps back at most three runs. Parsing the 300 KB directory listing
    every cycle would be the alternative.
  - **`not_applicable` is a new source state**, distinct from `disabled` (a dormancy the next cycle
    could end) and `out_of_range` (a slot a fetched source does not reach). Only the last means
    "never poll this".
  - **The coverage gate requires the whole sampled disc**, inset 0.3° from the data rectangle.
    Outside that rectangle every pixel is transparent, so a half-covered disc would read its
    missing half as "no echo" — silent wrongness again.
  - **The observed frame is optional.** A run whose forecast arrived but whose observation 404s is
    still a usable +10…+60 nowcast, and failing the cycle over "now" would be the wrong trade.
  - **Fixtures are recorded, not synthesized**, except two frames that prove negatives (no echo
    anywhere; echo only over Praha). The expected mm/h values in `tests/test_chmi.py` are pinned to
    the recorded bytes.
  - **No `X-Inst-Id` and no app version header.** Those were Meteor's; CHMI needs only an honest
    `User-Agent`. The config-entry-derived install id was removed along with them.

- **Deviations from PLAN.md / earlier phases (recorded before proceeding):**
  1. **A fifth source, added outside the phase plan** and outside the phase 0 evaluation.
  2. **`Pillow` is no longer confined to `sources/librewxr.py`.** There are now two image sources;
     `docs/ARCHITECTURE.md` § Module layout has been updated to say so.
  3. **The in-window request budget rises from ≤ 28 to ≤ 46 requests/hour, and ≤ 200 to ≤ 320/day
     — for locations inside the Czech composite only.** Recorded in `docs/DATA_SOURCES.md`
     § Request budget and `docs/ARCHITECTURE.md` § Resource budget. Bandwidth (~110 KB/cycle), not
     request count, is what phase 8 should look at here.
  4. **The frame cache bound changed from 32 to 48 entries.** Still far inside the ≤ 20 KB budget.

- **Open questions carried forward:**
  - **Which of the two radar sources is right in absolute terms is unknown, and it is the biggest
    open question about this source set.** CZRAD reads ~3× lower than OPERA in mm/h across the whole
    domain, including close to the Czech radars where beam overshoot does not explain it. Either
    CZRAD under-reads, or LibreWXR over-reads — its NWP-layer fusion outside radar coverage is a
    standing suspicion from phase 3. Settle it against IMGW rain gauges in phase 8; the answer may
    move `RELIABILITY` for either source, or the `range_factor` curve.
  - **Measure the `librewxr` / `chmi` correlation.** Phase 0's rule is that independence is
    established by measurement, and this pair has not been measured. The worry is smaller than first
    recorded — over Bielsko-Biała the two are dominated by different radars — but "smaller than
    feared" is not "measured".
  - **Confirm ČHMÚ's preferred CC BY 4.0 attribution wording** before 1.0.0. The licence itself is
    settled (ČHMÚ publishes its open data under CC BY 4.0); only the exact credit string is open.
  - **Revisit `png_masked` only with a way to get exact values** — un-blending, or the HDF5
    products, which carry numeric reflectivity rather than a rendering.
  - **CHMI's 5-minute run grid means a run based at :?5 yields forecast slots five minutes off the
    engine's 10-minute grid.** The step-function alignment handles it correctly; watch whether the
    offset ever makes CHMI and LibreWXR look like they disagree when they do not.
  - **The manual smoke test still has not been run**, and now covers this too: with the location
    near Bielsko-Biała the sensor's `sources` attribute should show a `chmi` entry in state `ok`;
    with it in Warszawa the same entry should read `not_applicable` and the debug log should show
    no request to `opendata.chmi.cz`.
  - Everything else carried forward from phase 6 and the per-walk-targets change is unchanged.

## Post-phase-6 fix — advice that had run out of time

- **Status:** done (code, tests and docs; suite green, ruff clean)
- **Date:** 2026-08-26
- **Why it exists:** a bug report from the first live install, not a phase. **Deviation from
  `PLAN.md`, recorded here as workflow rule 3 requires.** No phase was started or advanced;
  phase 7 is still `not started`.

- **The report.** A push arrived at 22:31 about a walk scheduled for 21:15, reading "Rain is
  expected around 21:15. Wait until 21:20 — 5 minutes later — and the whole 15-minute walk should
  stay dry." Every number in it was internally consistent; all of them were 70-odd minutes stale.

- **Three causes, and none of them is a wrong calculation.**
  1. `engine.recommend()` had no notion of the present. `candidate_starts()` is bounded by
     `[T − E, T + L]`, both measured from the walk time, so the search will offer 21:20 at any
     hour of the day. The engine is deliberately pure and clock-free — the mistake was that `now`
     was never made a *parameter* of the search, only of the freshness weighting.
  2. `WalkNotifier.async_process()` gated on `now < arm_at` and nothing else. There was a lower
     bound on when advice may be sent and no upper one.
  3. `WalkCoordinator._walk_end()` extends the watch window to `max(T, recommended_start) + D`.
     That is correct and wanted — it is what lets a "wait until" answer be re-checked — but it is
     what kept cycles running past the walk and gave (1) and (2) the airtime to speak. Reproduced
     analytically: with `later_margin` at its 30-minute default the last cycle for a 21:15 walk
     falls at 21:45, so the reporter's margin must have been ≥ 70 min for a 22:31 cycle to exist
     at all. Worth confirming against the live entry's options.

- **What was changed.**
  - `candidate_starts(..., not_before=)` and `recommend(..., now=)`: a window that has already
    begun is dropped before it is scored.
  - `engine.is_actionable()`: a recommendation with a target expires when the target does;
    `no_dry_window`, which names no time of its own, expires when the walk begins. The notifier
    checks it after the material-change test.
  - `engine.superseded_by_the_clock()`: a flip to `no_dry_window` caused *only* by the previously
    notified start having passed is not re-announced. Without this, every "go earlier" alert would
    be followed ten minutes later by a "there is no dry window" that says nothing new — the
    regression test `test_nothing_is_said_once_the_advice_has_run_out_of_time` covers exactly that
    sequence.
  - `WindowVerdict.nowcast_covered` / `Recommendation.provisional`: whether a *radar* reaches every
    slot of the window. Radars see 60 min, models 12 h.
  - `engine.Search` replaces the three loose `duration` / `earlier_margin` / `later_margin`
    arguments (`recommend` would otherwise have tripped `PLR0913`, and the three had always
    travelled together anyway). The coordinator holds one `Search` instead of three fields.
  - Config flow: a `beyond_radar` confirmation when `earlier_margin_min > 60`, plus inline field
    descriptions on all three timing options. `long_walk` now names the horizon too.
  - Notification: `{until}` (when the suggested walk gets home), a provisional sentence, and a
    per-walk `tag` so a revision replaces its predecessor on the phone.
  - Sensor: `requests_last_hour` / `requests_hourly_cap`, totalled from the per-adapter
    `RequestBudget`s that already existed.

- **Decisions, with the reasoning worth keeping.**
  - **The watch window still outlives the walk.** The tempting fix — stop at `T` — would have
    thrown away the only mechanism that can answer the horizon problem. Asked at 12:00 about a
    13:00 walk, only the hourly models can see 14:00; staying awake through 14:00 is what lets the
    radars confirm or correct that answer while it still matters. Bounded by `L`, and now counted.
  - **`later_margin` gets no config-flow warning, `earlier_margin` does.** Time moves towards a
    later window and away from an earlier one: a window an hour ahead will have been seen by the
    radar long before the user has to leave, so a wide `later_margin` costs only requests. A wide
    `earlier_margin`, by contrast, moves the *decision moment* out of radar range, and that is a
    real trade-off the user should confirm rather than discover.
  - **A model-only answer is published, not withheld.** Suppressing it until the radar agrees
    would mean silence at exactly the moment the user asked to be told. It is sent, labelled
    `provisional`, and revised if the radar disagrees.

- **Open questions carried forward.**
  - **Actionable notification buttons** ("stop telling me about this walk" / "I will wait another
    hour, keep checking") were discussed and are technically straightforward — `data.actions` on
    the companion-app payload, `mobile_app_notification_action` on the bus — but they were not
    built, and they need a `walk_the_dog.snooze` / `walk_the_dog.extend` service pair so an
    automation and a button press share one code path. Decide before phase 7 freezes the strings.
  - Whether the reporter's `later_margin_min` really is ≥ 70 min, which is what the 22:31 timing
    implies. Not needed for the fix; needed to close the report.
  - Everything else carried forward from phase 6, the per-walk-targets change and the CHMI change
    is unchanged.

## Post-phase-6 change — closing a walk, confirming a plan, and a sprint before the door

- **Status:** done (code, tests and docs; 452 tests green offline, ruff clean)
- **Date:** 2026-08-26
- **Why it exists:** the four highest-value items from the improvement list written up in the
  previous entry, picked by the maintainer. **Deviation from `PLAN.md`, recorded here as workflow
  rule 3 requires.** No phase was started or advanced; phase 7 is still `not started`.

- **What was built.**
  1. **`walk_the_dog.walked` and the *Already went* button.** The button is `data.actions` on the
     companion-app payload; the tap comes back as a `mobile_app_notification_action` event, which
     the coordinator listens for. `mobile_app` is deliberately *not* a manifest dependency — a
     user without the app simply never fires the event and everything else works.
  2. **`confirm_margin_min`** (default 0 = off): one message `confirm_margin` before the departure
     moment, in two shapes — the plan stands, or the rain has gone.
  3. **Sprint cadence**: 5-minute cycles for the 20 minutes before setting off, only where a
     source publishes faster than the grid.
  4. **`binary_sensor.walk_the_dog_walk_window`.**
  5. **Publication-aligned cycles** — added after the four above, at the maintainer's request; see
     its own heading below.

- **Decisions, with the reasoning worth keeping.**
  - **The walk occurrence is encoded in the action identifier, not passed beside it.** The action
    string is the one field both companion apps hand back unchanged; extra keys are not
    guaranteed. It also makes the button self-scoping: a notification left over from yesterday
    carries yesterday's stamp and closes nothing.
  - **"I'll wait, keep checking" was *not* built as a button.** It is what the integration already
    does after the timeline fix, and making the user opt in to it would add friction and a failure
    mode — miss the tap, get nothing. Only the decision the app cannot infer got a button.
  - **Closing a walk stops polling, not alerting.** The saving is the point; the switch remains
    the way to turn the integration off.
  - **Dismissal is in memory only.** A restart inside the window resurrects the walk. That is the
    safe way round to be wrong — an extra notification beats silently skipping a walk because of
    a stale flag — and it avoids a Store write on a hot path.
  - **The stand-down message is the reason the confirmation exists.** "The plan still stands" is
    mild reassurance; "the rain has gone, walk at the normal time" closes a real gap, because
    `later` relaxing to `none` is not an alert direction and silence means "go as planned" — two
    readings of silence that contradict each other for the one user who was told to wait.
  - **The sprint is gated on the source, not on the location or the clock alone.**
    `SourceRegistry.fast_cadence()` asks CHMI whether it covers the disc. Elsewhere the extra
    cycles would re-score identical bytes: LibreWXR publishes every 10 minutes and Open-Meteo
    every 30, and both now gate their own fetch on their own cadence, so a sprint cycle costs two
    CHMI requests and nothing else. `SPRINT` divides `CYCLE`, so the anchored grid is subdivided
    rather than replaced and the cycle promised at `T − E` still lands.
  - **The sprint can run twice for one walk** — into the recommended start, and again into `T`
    once that suggestion lapses unused. Both are moments the user might walk out of the door, so
    this is wanted rather than tolerated; it is pinned by
    `test_the_last_stretch_before_setting_off_runs_at_five_minutes`.
  - **CHMI's hourly cap: 18 → 30.** Its own publication rate is 5 minutes and a cycle is 2
    requests, so a sustained sprint is 24/h. The active-hour ceiling inside its box rises from 46
    to 58; outside it nothing changes.

- **Correction to the previous entry.** It said flatly that polling faster than a source publishes
  "buys nothing". That is true of a single source's *bytes* and it is not the whole story: a
  convective cell can form and arrive inside one 10-minute slot, and CHMI publishes twice as often
  as the grid, so there was real information being left on the table around Bielsko-Biała. Hence
  the sprint, and hence the publication alignment below.

- **Publication alignment (added in the same session, after the sprint).** The open question left
  above — align the cycle to the provider's clock rather than only to ours — turned out to be
  worth building, but **not for the reason it was written down**. The note said a fresh frame
  could "sit unread for up to a full slot", implying stale data at the decision moment. That is
  wrong: a fetch always returns the newest frame that *exists*, so what is read at `T − E` is the
  same either way, and no forecast the provider has published is ever missed. What was actually
  waiting was the **alert**. A material change contained in a frame published at 04:31 was
  announced at the 04:40 cycle — up to a full cadence late, and precisely in the case the
  maintainer raised, where a cell builds inside twenty minutes.

  - `_aligned_wake()` returns `issued_at + interval + PUBLISH_SETTLE` for the source whose own
    publication interval equals the cadence being run (LibreWXR at 10 min, CHMI at 5). Hourly
    sources have nothing to align to at this timescale; a location with no fast source gets `None`
    and keeps the plain grid unchanged.
  - **`min(grid, aligned)`, never a replacement.** This is the decision that makes the feature safe
    to ship. `PUBLISH_SETTLE` is a guess (60 s) at how long after a frame's stamp it is actually on
    the server, and a *re-phased* grid built on a wrong guess degrades badly — it converges to
    fetching one frame behind, permanently worse than no alignment at all. Taking the minimum means
    the alignment can only ever pull a cycle earlier: the grid keeps running underneath at its own
    rate, a wrong guess costs one cheap extra cycle, and nothing that was due is ever skipped.
  - **The notification moment is now pinned explicitly** (`min(wake, arm_at)` while `now < arm_at`)
    rather than following from the grid arithmetic. It held anyway, but a promise that survives by
    reasoning about a `min()` chain is one refactor away from not holding.
  - **The cost is cycles, not requests.** Up to two cycles per cadence instead of one. `chmi`
    gained a 5-minute fetch gate to match the one `librewxr` got with the sprint, so every adapter
    now gates on its own publication interval and the request count follows the providers' rates
    rather than the coordinator's wakeups. A cycle without a fetch is arithmetic over ~90 slots;
    the image decode the CPU budget is about only happens on a real fetch.

- **Open questions carried forward.**
  - **`PUBLISH_SETTLE` is an estimate, not a measurement.** 60 s, never observed. Measure the real
    lag between a LibreWXR frame's stamp and its availability in phase 8 and pin it. Being wrong is
    cheap by construction (above), but being right is free.
  - **`walk_the_dog.extend`**, the other half of the service pair sketched in the previous entry:
    "I have another hour today" when the answer is `no_dry_window`. Deliberately not built —
    nothing in the current design needs it, and it should wait until there is a real complaint it
    answers.
  - Whether dismissal should survive a restart. Needs a Store write; see the decision above.
  - `hassfest` has not been run against the new `services.yaml` and `strings.json` sections on this
    machine (Windows), only the offline pytest suite. CI covers it on push.
  - Everything else carried forward from the earlier entries is unchanged.

## Config-flow clarity pass (out of phase, from live install feedback)

- **Status:** done
- **Date:** 2026-08-26
- **What was built:** rewritten `strings.json` / `translations/en.json` for the `walk_target` and
  `params` steps, an additive-and-de-duplicated notification model, and a per-walk `away_entity`.
- **Decisions:**
  - **`notify_service` is now "Always notify this device", not a fallback.** The maintainer's
    report was "why is notification device in the options *again*?" — a field whose meaning is
    "used only when the other field is empty" cannot be made self-evident in a settings screen,
    because nothing on screen shows the other field's state. Additive can: this device always
    hears, the per-walk lists add more phones. It is a behaviour change for anyone using an empty
    per-walk list to mean "replace the default", and the changelog says so.
  - **De-duplication in two places, deliberately.** `_collect_target` normalizes and de-duplicates
    what one walk stores (so `notify.mobile_app_x` typed as a custom value collapses onto a picked
    `mobile_app_x`), and `WalkNotifier.services_for` de-duplicates the union at dispatch. The
    second is what protects entries stored before this rule existed; the first is what stops the
    options form from showing the user a list that lies about how many phones a walk reaches.
  - **Per-walk `away_entity` rather than a per-walk checkbox.** A checkbox can only say "also obey
    the entry-wide person"; the actual requirement is that the walk Anna does falls silent when
    *Anna* leaves. The entity picker is a superset and costs one more optional field. It falls back
    to `auto_mute_entity` when empty, so nothing changes for an existing entry.
  - **`{default_device}` is a description placeholder with a translated fallback.** The wizard asks
    about walks *before* it asks for the always-notified device, so on a fresh install there is
    nothing to name; `common.default_device_unset` fills the hole with a phrase pointing at the
    field the user is about to meet. Placeholders are used only in `description`, never in `title`
    or `data_description` — those substitutions are frontend behaviour this repo has not verified,
    and a literal `{time}` on screen is worse than a slightly longer sentence.
- **Open questions carried forward:**
  - **The reported symptom was partly a stale translation cache, not only thin copy.** The
    screenshots showed an unlabelled `walk_target` step next to a fully labelled `params` step —
    a combination no commit in this repo produces. Home Assistant was serving a cached
    `translations/en.json` from before `walk_target` existed; a full restart is needed, an
    integration reload is not enough. Worth a line in the install docs in phase 9.
  - `hassfest` still unrun on this machine (Windows); CI covers it on push. Unchanged.

## Phase 7 — Localization + branding

- **Status:** done
- **Date:** 2026-08-26
- **What was built:**
  - `translations/pl.json` — the whole integration in Polish: both flows (every title,
    description, field label, `data_description`, warning step and error), both selectors, the
    device, the entity names, the sensor's four states, all 22 attribute labels, the service and
    its exception, and the `common` block (notification texts, the **Już byliśmy** button, the
    day labels the per-walk step's description is built from).
  - `strings.json` / `translations/en.json` gained a top-level `title` and a `device` section.
    The top-level `title` is what Home Assistant lets a translation override the integration's
    name with; `device.service.name` names the one service device.
  - `entity.py`: the device is now `DeviceInfo(translation_key=...)` instead of
    `name=entry.title`, and `manufacturer` comes from the new `INTEGRATION_NAME` constant.
  - `config_flow.py`: the config entry is titled with the translated name (`TITLE_CATEGORY`
    lookup, falling back to `INTEGRATION_NAME`).
  - `const.py`: `INTEGRATION_NAME`, `TITLE_CATEGORY`, `DEVICE_TRANSLATION_KEY`.
  - `scripts/make_branding.py` + `branding/custom_integrations/walk_the_dog/` — `icon.png`
    (256), `icon@2x.png` (512), `logo.png` / `logo@2x.png` (1140x256 / 2278x512) and a
    `dark_logo` pair. `branding/README.md` holds the design rationale, the brands size rules
    checked against the upstream README on 2026-08-26, and the step-by-step for the phase 9
    pull request.
  - Docs: `docs/CONFIG.md` § Language (what is translated, what is deliberately not, and why),
    `docs/DEVELOPMENT.md` § Translations, README/`info.md` banners updated to match reality.
  - Tests: the Polish parity suite in `tests/test_strings.py` (every key present, nothing empty,
    nothing left in English, every `{placeholder}` preserved), the translated device name in
    `tests/test_entities.py`, and an end-to-end Polish notification in `tests/test_notifier.py`.
    **14 new tests, 466 in total**, green with networking fully disabled
    (`docker run --network none`). `ruff` clean; hassfest run locally against the real action
    image and green, with no warnings.
- **Decisions:**
  - **The localized name reaches the user through three separate mechanisms, because Home
    Assistant has no single one.** The top-level `title` renames the integration in the UI;
    `device.service.name` renames the device, which is the prefix on every entity's friendly
    name; and `common.notification_title` is the push title. All three carry "Idź już z psem" in
    `pl.json`, and a test pins that they agree — one of them left in English would produce a
    screen that is half-translated.
  - **The device name comes from a translation, not from the config entry title.** An entry
    title is stored once, in the language it was created in, and can never be re-translated. The
    device name is what Home Assistant prefixes entity names with, so it is the one that had to
    move. The cost, accepted: renaming the *entry* no longer renames the device. Renaming the
    device does, and that is the control the frontend actually offers.
  - **The config entry is still given a title, now the translated one.** A fresh Polish install
    gets an entry called "Idź już z psem"; existing entries keep theirs. It is stored, not
    re-translated later — but an English entry sitting under a Polish heading is the worse of the
    two states.
  - **`hassfest` does not check `pl.json` at all** — for a custom integration it validates
    `strings.json` and `translations/en.json` and ignores every other language file (verified by
    reading `script/hassfest/translations.py` in the action image). The parity tests are
    therefore not belt-and-braces, they are the only check that exists: keys, emptiness,
    placeholders, and "not left in English". The placeholder test is the one that matters most —
    `notifier.py` falls back to the *unformatted* template when a placeholder does not resolve,
    so a mistyped `{recommended}` would reach the phone literally.
  - **A Polish install will get Polish entity IDs.** Home Assistant generates them from the
    device and entity names in the language the entity was first created in. This is Home
    Assistant's own behaviour for every integration that translates its entity names, it happens
    once and never again, and the IDs can be renamed — so it is accepted rather than fought.
    Everything that is an *identifier* stays English regardless: the domain, the event name, the
    service name, and every key and value in the event payload and the sensor attributes.
  - **The audit for hard-coded user-facing strings was run over the AST, not by eye.** Every
    string constant in `custom_components/walk_the_dog/` that is not a docstring and contains a
    space was listed and classified. What remains is: log messages (English by convention, and
    not user-facing), `INTEGRATION_NAME` (a brand), and the five provider attribution strings in
    `sources/base.py` — those are licence text the providers require verbatim, and Home
    Assistant's `attribution` attribute is not translatable anyway. `SourceStatus.detail` prose
    stays internal: it never enters `payload()`, so it cannot leak into the sensor or the event.
  - **The brand images are generated, not drawn by hand.** `scripts/make_branding.py` composes
    them from primitives at 4x and reduces with LANCZOS. A committed PNG nobody can regenerate is
    a dead end the first time a colour needs changing.
  - **A paw print, not a dog.** The icon is rendered at about 24 px in the integrations list; a
    dog silhouette does not survive that reduction and a paw plus three drops does, while still
    saying "pet" and "rain".
  - **The `icon.png`/`logo.png` "fallback" from `PLAN.md` is served from `branding/`, not from
    the integration folder.** Home Assistant never loads brand images out of a custom
    integration's directory — it goes to `brands.home-assistant.io` and shows a placeholder when
    there is nothing there — so a copy inside `custom_components/walk_the_dog/` would be dead
    weight that hassfest does not know about and that would drift. The README shows the logo from
    `branding/` instead, which is the only place either file can actually be seen before the
    brands pull request lands.
- **Deviations from PLAN.md (recorded before proceeding):**
  1. **Brand assets live in `branding/custom_integrations/walk_the_dog/`, not in the integration
     folder** (reason above). The plan's "ship icon.png/logo.png in the repo as fallback" is met
     by the files being in the repo and shown in the README.
  2. **A `dark_logo` pair is shipped in addition to the four required images.** Not asked for;
     the wordmark's ink is unreadable on a dark theme otherwise. `dark_icon` is deliberately
     absent — the badge needs no dark variant.
  3. **The `device` section, the top-level `title` and the translated entry title are additions
     to phase 5/6 code** that phase 7 needed in order to have anywhere to put the localized name.
- **Open questions carried forward:**
  - **The brands pull request is prepared but not opened** — phase 9, along with dropping the
    last `ignore: brands` from the validation workflow. Until it merges, the frontend shows a
    placeholder icon. If the brands CI objects to non-interlaced PNGs (Pillow cannot write
    interlaced) or to a `dark_logo` without a `dark_icon`, `branding/README.md` says what to do
    in each case.
  - **Nobody has yet read the Polish strings in a running Home Assistant.** The texts are pinned
    by tests and the dispatch path is proven end to end, but tone and length on a real phone
    screen and in a real settings form are worth one pass by a Polish speaker — the maintainer.
  - **Actionable notification buttons beyond "Already went"** (`walk_the_dog.extend`) — still not
    built, and the strings are now frozen for it. Unchanged from the previous entries.
  - **The manual smoke test from phase 6 has still not been recorded here**, and now also covers
    the language switch and the device name.
  - **CI was delayed by a GitHub Actions outage, then went green.** Actions was in a major
    outage (githubstatus.com) while this phase was pushed and created no run for ~10 minutes;
    when it caught up it delivered the queued pushes out of order, so the runs for the first
    two commits were cancelled by the workflows' own `cancel-in-progress` concurrency rule.
    **CI and Validate are green on `3a94e13`**, which carries every code, translation and
    branding change; the commits after it touch only `STATE.md`. Everything CI does was also
    run locally: `ruff` check and format, the full suite in the same container image with
    `--network none`, and hassfest against the real action image.
  - `PUBLISH_SETTLE` is still an estimate; measure it in phase 8. Unchanged.
  - Tests still cannot run natively on Windows (HA imports `fcntl`); this phase was developed on
    the Windows machine, its suite run in the documented Linux container with `--network none`,
    and hassfest run against the real action image in Docker. Unchanged.
  - Everything else carried forward from the earlier entries is unchanged.

## Post-phase-7 change — presence is decided per device, not per walk

- **Status:** done (code, tests, strings and docs; live re-test is the maintainer's next step)
- **Date:** 2026-08-26
- **Why it exists:** not a phase. It came out of reviewing the Polish copy for the away entity:
  writing down what the field did made plain that the behaviour itself was wrong. **Deviation
  from `PLAN.md`, recorded here before proceeding, as workflow rule 3 requires.** No phase was
  started or advanced; phase 7 is done and phase 8 has not begun.

- **The report.** Two things, from the maintainer:
  1. "If two devices are configured for a walk and one of them is not home at the moment the
     notification goes out, the other device should still get it." Today one absent person
     silences the walk for *everyone*.
  2. "The default device from the main settings screen should always be notified — unless the
     alerting switch is off, in which case nobody is."

- **Decisions (maintainer's, taken before the change was written).**
  - **A phone's presence is derived from its own tracker.** `notify.mobile_app_jan_phone` and
    `device_tracker.jan_phone` are the same phone registered by the same companion app under the
    same slug, so the link needs no configuration at all. When no such tracker exists, or it
    reads `unknown` / `unavailable`, the device is **notified** rather than silently skipped —
    an extra alert is a much cheaper mistake than a missed one.
  - **The always-notified device is exempt from every silencing rule but one.** Not the per-walk
    mute switch, not either away entity, not its own tracker. Only the alerting switch stops it,
    and that stops the whole integration. Chosen deliberately over keeping mute absolute: the
    maintainer's rule is "that phone always hears", and one rule with no exceptions is easier to
    trust than one with three.
  - **Both away entities become the fallback presence rule for a walk's own phones**, and neither
    touches the always-notified device. A walk's own entity still overrides the entry-wide one.
  - **Consequence, accepted:** there is no longer any way to silence a whole walk *conditionally*.
    "Skip this walk while Anna is out" now means "skip Anna's phone", because Anna's phone answers
    for itself. The unconditional mute switch and the alerting switch are what remain for
    silencing, and the second of them is the only thing that can silence the always-notified
    device.
  - **`muted` in the event payload now means "nobody was reached at all"**, rather than "the away
    entity said no". With an always-notified device configured it is therefore almost always
    `false` — which is accurate: somebody did get told.

- **What was built.**
  - `notifier.py`: `recipients_for()` replaces `services_for()` at dispatch and filters the
    addressed list one phone at a time; `_reaches()` holds the three rules; `_device_is_home()`
    reads `device_tracker.<slug>` for a `mobile_app_<slug>` service and returns `None` — not
    `False` — when there is nothing to read. `services_for()` is unchanged and still answers
    "who is this walk addressed to", which is what `async_clear` needs.
  - `strings.json` / `en.json` / `pl.json`: three labels renamed (they promised more than the
    code does now — "Never alert about this walk" no longer describes a switch the entry-wide
    device ignores) and four descriptions rewritten.
  - `docs/CONFIG.md`: a new § *Who is actually reached* with the three rules, plus the per-walk
    table, § Notification behavior and the `muted` row brought in line.
  - Tests: three rewritten because they encoded the old contract, six added for the new one —
    two phones with one away, a phone with no tracker, a tracker reading `unknown` / `unavailable`,
    a tracker overruling the away entity, the always-notified device ignoring its own tracker, and
    `muted` meaning nobody at all. **473 in total**, green offline.

- **Open questions carried forward.**
  - **The tracker link is by name.** `mobile_app_jan_phone` → `device_tracker.jan_phone` holds
    because the companion app registers both from one device name, but a renamed entity breaks it
    — and breaks it *safely*, into "cannot answer", which notifies. Worth re-checking against the
    live install before 1.0.0; if it turns out to be fragile in practice, the fallback is an
    explicit presence field per device, which was considered and rejected here as too heavy for
    the wizard.
  - **There is no longer a conditional way to silence a whole walk.** If someone asks for it back,
    the honest shape is a separate "silence this walk while X is away" switch, not a reinterpretation
    of the away entities.
  - Everything else carried forward from phase 7 is unchanged.

## Phase 8 — Performance pass

- **Status:** done
- **Date:** 2026-08-26
- **Where it was measured:** no ARM board was available, so the target was a **container limited
  to one CPU and 512 MB** (Debian, Python 3.14.2; host Intel i7-14700KF), which `PLAN.md` allows
  and which reproduces the *memory* constraint of the weakest supported hardware exactly and the
  *processor* one only by proportion. The benchmark was also run unmodified on `linux/arm64`
  under emulation — it works there, and its timings are useless as a proxy (QEMU interprets every
  NEON instruction and came out hundreds of times slower than the host), so none are quoted.
- **What was built:**
  - `scripts/benchmark.py` — drives the **real** adapters and the **real** engine over the
    recorded fixtures and reports, per cycle: CPU time, peak RSS (sampled from `/proc/self/statm`
    by a thread, because numpy and Pillow allocate where `tracemalloc` cannot see), the longest
    event-loop stall, requests and bytes. Two profiles: Warszawa (one tile per frame, no CHMI)
    and Bielsko-Biała (two tiles, inside the CHMI composite). It runs a *sequence* of cycles from
    a cold cache at the real cadence, so cold and warm cycles are measured as they actually
    occur, and it needs no Home Assistant at all.
  - `scripts/measure_publish_lag.py` — the only tool here that touches the network: it watches
    the two fast sources for an hour and reports how long after a frame's own timestamp the frame
    can first be read. Stdlib only, so it runs with a bare Python on either machine.
  - `tests/test_performance.py` — seven tests, including a **whole simulated day** of four walks
    driven minute by minute through the real coordinator, the real adapters and their real
    budgets, with only the network replaced. It counts requests per source per rolling hour,
    cycles per hour, bytes, and the latency between a frame being published and being read; it
    also asserts that a day with alerting off costs nothing at all. It reuses the benchmark's
    fixture session rather than growing a second one.
  - Tuning, all three items measurement-driven: the LibreWXR hourly ceiling now scales with the
    disc's tile count, `PUBLISH_SETTLE_S` is per source and measured, and both radar adapters
    will now fetch for a publication-aligned cycle.
  - Docs: `docs/ARCHITECTURE.md` § Resource budget rewritten as measurements with a verdict per
    line, § Coordinator scheduling updated for the per-source settle and the aligned fetch,
    § Frame sampling corrected on what a LibreWXR frame path actually is;
    `docs/DATA_SOURCES.md` § Request budget carries the measured day and the two corrected
    ceilings; `docs/CONFIG.md` gained § What it costs to run (for users, in megabytes);
    `docs/DEVELOPMENT.md` § Measuring performance says how to re-run all of it.
  - **483 tests** (10 of them new), green offline (`docker run --network none`); `ruff` clean.

- **Measured — per cycle** (one CPU, 512 MB; median over 16 cycles):

  | | Warszawa | Bielsko-Biała |
  |---|---|---|
  | warm cycle | **0.7 ms** CPU, 2 requests | **4.9 ms**, 5 requests |
  | cold cycle (empty cache) | 2.8 ms, 9 requests | 10.2 ms, 18 requests |
  | first cycle after a restart | 8.1 ms, peak RSS +2.4 MB | 15.8 ms, +3.2 MB |
  | peak RSS over a steady cycle | +224 KiB | +260 KiB |
  | longest event-loop stall | 0.9 ms typical, 3.2 ms worst | 1.1 ms typical, 11.1 ms worst |
  | cache after 16 cycles | 18 entries, 1 639 B persisted | 32 entries, 4 341 B |

- **Measured — a day of four walks:** 156 requests and 365 KiB outside CHMI's box, 372 requests
  and 7.9 MiB inside it; busiest hour 22 / 47 requests and 13 / 21 cycles; zero requests outside
  a walk window and zero with alerting off. Every figure is inside the budget it was measured
  against.

- **Measured — publication lag** (live, 2026-08-26 18:47–19:42 UTC, 20 s polling): **CHMI 18.2 s
  minimum and median over 11 runs, 68.1 s on its worst**; **LibreWXR 78.1 s minimum, 91.4 s
  median, 158.1 s worst over 6 frames**. Both settle margins sit above the worst lag seen.

- **Decisions:**
  - **The budget stands; it was not revised.** Every ceiling in `docs/ARCHITECTURE.md` was met
    with at least an order of magnitude to spare on processor and memory, so nothing was traded
    away to fit. Two lines were *added* to the table rather than changed: the longest event-loop
    stall, and daily bandwidth — both things the phase measured that nobody had put a number on.
  - **LibreWXR's hourly ceiling is now a function of the geometry** (`hourly_cap(tile_count)`:
    six cycles of an index poll plus up to two new frames, plus one cold start's back-fill —
    25 requests for a one-tile disc, 44 for two). The flat 20/h was **binding**: at Bielsko-Biała
    the adapter spent exactly 20 in the first hour of every window and then stopped sampling
    frames, because that is what an exhausted budget makes it do. A disc that straddles a z=8
    tile boundary is not a corner case — one of the two locations the project has tested with all
    along is one, and so is any Warszawa disc of 10 km or more. A limit meant to be polite to the
    provider was quietly shortening the forecast.
  - **`PUBLISH_SETTLE` is per source and measured**: LibreWXR 180 s, CHMI 90 s. One shared 60 s
    guess was below LibreWXR's real lag, and being early is not free — the aligned cycle then
    asks for a frame that is not there yet. CHMI's own run-stamp offset (`PUBLICATION_LAG`, a
    2-minute guess) is now the same constant: it is the same fact, and the correction makes every
    CHMI cycle up to a minute fresher.
  - **The publication alignment was inert, and now is not.** The coordinator woke early for a
    frame, and the adapter then refused to fetch because its own "have ten minutes passed since I
    last asked" gate had not: measured over a simulated day, **every** radar frame was read a
    full 8 minutes after it was published. The adapters now also fetch when *a frame they do not
    have is due* (`issued_at + interval + settle`), which locks the cadence onto the publications
    after one short interval and cuts the median wait to **1 minute**. Requests rose 5 % (156 a
    day, from 148) and every ceiling still holds.
    - The alignment tests that existed passed throughout, because they drive the coordinator with
      a fake fetch and could only ever prove that it *woke* on time. That is why the new test
      measures the thing the user feels — how long a published frame waits to be looked at —
      rather than when a timer fired.
    - CHMI keeps the stricter form (never closer together than `interval − settle`) because it
      publishes twice as fast as the cycle grid; reading every run would double its cost and its
      bandwidth for freshness the sprint cadence already exists to collect. Left unguarded it did
      exactly that in measurement: 468 requests a day instead of 372.
  - **Image sampling stays in the event loop.** Offloading the decode to an executor was
    considered and declined: the longest measured stall is 11.1 ms, on the first cycle after a
    restart, against asyncio's own 100 ms threshold — and buying it back would mean threading an
    executor through the registry into two adapters at the end of the project. The number that
    would change the answer is a real one from ARM hardware; if a user's log ever shows Home
    Assistant complaining that something is blocking the loop, this is the thing to change.
  - **`cache.py` imports `Store` inside `attach_store()`**, so the module's own promise — "the LRU
    is plain Python with no Home Assistant imports" — is now literally true, and the benchmark can
    exercise the real cache with no Home Assistant installed.
  - **The benchmark loads the integration without running its `__init__.py`**, by registering the
    package under its own name with a synthetic module object. That is the one clever thing in
    the tooling and it earns its keep: it is what lets the measurement run on Windows, in a
    100 MB container, and on `linux/arm64`, none of which have Home Assistant.

- **Deviations from `PLAN.md` (recorded before proceeding):**
  1. **The phase changed behaviour in three places**, where the plan's task 2 says to tune "until
     within budget" and the budget was already met. Each change came out of a measurement that
     showed something documented was not true — a ceiling that shortened the forecast, a settle
     margin below the real lag, an alignment that could not fetch — and leaving those in place
     would have meant publishing a measured lie. No new feature was added.
  2. **A real ARM device was not used** (none exists here). `PLAN.md` allows a constrained
     container and asks for the choice to be recorded; it is recorded above, along with what the
     container does and does not reproduce.

- **Open questions carried forward:**
  - **The processor figures still want one run on real hardware.** `scripts/benchmark.py` is in
    the repo precisely so it can be run on the maintainer's own Home Assistant box (Advanced SSH
    add-on, `python scripts/benchmark.py`); one run there replaces the order-of-magnitude
    estimate in `docs/ARCHITECTURE.md` with a number. Worth doing before 1.0.0 is announced.
  - **Two modules use Python 3.14-only syntax** — `except KeyError, IndexError:` without
    parentheses (PEP 758), in `notifier.py` and `sources/met_norway.py`. It is a `SyntaxError` on
    3.13, so the integration would fail to load for anyone whose Home Assistant still runs it.
    Found because the first arm64 benchmark image shipped 3.13 and the import blew up.
    `hacs.json` requires HA 2026.8.0, which is 3.14, so nothing is broken today — but the fix is
    two pairs of brackets and the failure mode is total, so it is worth a decision in phase 9.
  - **A re-issued LibreWXR nowcast frame is never re-read.** The frame path is
    `/v2/radar/<the frame's own valid time>`, not a per-run identity as `docs/ARCHITECTURE.md`
    used to claim, so the cache treats the 12:20 frame predicted at 12:00 and the improved one
    predicted at 12:10 as the same thing. Correcting it outright would mean 7 tiles a cycle
    instead of 1 and fits no sane budget; the honest options are to keep it (current choice, now
    documented) or to re-read only the *observed* frame, which costs one extra tile a cycle and
    would replace a ten-minute-old prediction with what actually happened. Worth deciding later,
    with a false-alarm log to point at.
  - **The worst frame still waits 8 minutes** — the first one of each window, before the cadence
    locks onto the publications. Waking a window's first cycle on the alignment rather than on
    the window start would fix it; it was left alone because that cycle is also the coldest one
    and nothing is waiting on its frame yet.
  - **The publication lag was measured once, over an hour.** LibreWXR's spread (78–158 s) is wide
    enough that a seasonal or load-related shift is plausible. If the settle margin ever proves
    too short, the symptom is an aligned cycle that fetches the frame it already has, and the
    remedy is to re-run `scripts/measure_publish_lag.py` and raise the number.
  - **p90 for the LibreWXR disc** (carried since phase 1: whether the percentile needs tuning
    against real events) is still open. This phase measured cost, not accuracy, and there is no
    false-alarm log to tune against yet.
  - **The manual smoke test from phase 6 has still not been recorded here**, and neither has a
    Polish speaker's read of the phase 7 strings. Unchanged.
  - Everything else carried forward from phase 7 is unchanged.

## Post-phase-8 change — versioning, and a dead attribution link

- **Status:** done (release metadata, workflow, tests and docs; the `v0.8.0` tag is the
  maintainer's to push)
- **Date:** 2026-08-27
- **Why it exists:** not a phase. Two reports from the maintainer after updating the integration
  on the live instance. **Deviation from `PLAN.md`, recorded here before proceeding, as workflow
  rule 3 requires.** No phase was started or advanced; phase 8 is done and phase 9 has not begun.

- **The reports.**
  1. HACS showed "Installed version `2e12f07` / Latest version `4e76f94`" — commit hashes, which
     tell the user nothing about what the update contains.
  2. The README's EUMETNET OPERA attribution linked to `observations.eu`, which now redirects to
     a domain broker.

- **Decisions.**
  - **Release, don't wait for 1.0.0.** HACS names an install by its commit hash only while a
    repository has no releases at all; the plan had the first tag in phase 9, which left the
    whole development period unreadable. Tagging now costs nothing and makes every update since
    legible.
  - **First tag is `v0.8.0`, not `v0.1.0`.** `manifest.json` still said `0.1.0` — a number set
    at the repo skeleton and never touched through six phases of work. The minor version now
    tracks the completed phase, so `1.0.0` still means "phase 9 complete" exactly as planned.
  - **`manifest.json` is the single source of the version; the tag mirrors it.** Home Assistant
    and HACS both display the manifest value, so anything that derived a version elsewhere could
    only disagree with what the user sees. `scripts/release.py` refuses to tag until
    `CHANGELOG.md` has a matching dated section, the `Release` workflow re-checks the tag against
    the manifest before publishing, and `tests/test_release.py` fails CI if the two drift.
  - **Releases are never marked pre-release.** HACS hides pre-releases unless the user has opted
    into beta versions, so a `0.x` marked that way would simply never appear as an update.
  - **The whole existing changelog became the `0.8.0` section** rather than being split
    retroactively into invented earlier versions — none of it was ever released under a number.

- **Open questions carried forward:**
  - Phase 9 should decide whether `1.0.0` also brings a `zip_release` HACS asset; today HACS
    installs the tag's source tree, which is correct but ships the tests and docs too.
  - The maintainer asked whether an alert could carry a radar image of the 25 km disc around
    home. Answered as a feasibility note only — nothing implemented, and it is not in `PLAN.md`.

## Phase 9 — Docs, release 1.0.0

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**
