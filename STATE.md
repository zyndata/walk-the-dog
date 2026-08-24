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

- **Status:** not started
- **Date:**
- **What was built:**
- **Decisions:**
- **Open questions carried forward:**

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
