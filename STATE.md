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

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

## Phase 2 — Repo skeleton + development environment

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

## Phase 3 — Source clients

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

## Phase 4 — Sampling + consensus scoring engine

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

## Phase 5 — Config flow wizard + options flow

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

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

## Phase 9 — Docs, release 1.0.0, go public

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**
