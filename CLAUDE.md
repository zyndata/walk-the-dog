# CLAUDE.md — Walk the dog

## What this project is

"Walk the dog" (Polish: "Idź już z psem") is a Home Assistant custom integration that predicts
whether it will rain during the user's recurring dog walks. It combines ready-made precipitation
nowcast frames from multiple independent weather sources covering Poland, scores the consensus
between them, and — when a walk window is at risk — proactively suggests going out earlier or
later so the walk stays dry. It is distributed via HACS and must run comfortably on the weakest
hardware that can run Home Assistant (single-core ARM, ~512 MB RAM).

## Where the details live

This file holds only general working rules. Everything else is in dedicated documents:

- [PLAN.md](PLAN.md) — the phased implementation plan. **Read the current phase before doing anything.**
- [STATE.md](STATE.md) — living log: per-phase status, decisions, open questions. **The handover document between sessions and machines.**
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module layout, data flow, sampling, consensus scoring, resource budget.
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — evaluated weather sources, comparison table, request budget, fallback strategy.
- [docs/CONFIG.md](docs/CONFIG.md) — all user-facing configuration options and their semantics.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — dev environment setup (Windows + Linux), test/deploy/release instructions (created in phase 2).
- [docs/BRANDING.md](docs/BRANDING.md) — the icon and logo: where they live, how they are drawn, what they must satisfy.

## Tech stack

- Python, Home Assistant custom integration (`config_flow` + one shared `DataUpdateCoordinator`).
  **Not** a Supervisor add-on.
- Domain: `walk_the_dog`. Code lives in `custom_components/walk_the_dog/`.
- Allowed runtime dependencies: only libraries already shipped with HA core (`aiohttp`, `numpy`,
  `Pillow`). Any new dependency must be justified in `STATE.md` before it is added.
- All I/O async; no blocking calls in the event loop.
- Tests: `pytest` with recorded fixtures — no live network calls in CI.
- Distribution: HACS (`hacs.json`, `manifest.json` with `version`, `info.md`), CI runs `hassfest`
  and HACS validation.

## Coding conventions

- Formatting and linting: `ruff` (pinned version in dev requirements), enforced by pre-commit and CI.
- Type hints everywhere; `from __future__ import annotations` in every module.
- Path handling with `pathlib` only — never hard-coded separators or absolute machine paths.
- All user-facing text goes through `strings.json` / `translations/` (en base, pl priority quality).
  English for everything that cannot be translated: repo, domain, entity IDs, event names, code.
- Comments only for constraints the code cannot express itself.
- Design for low-end hardware in every change: no full-frame decoding, sample only needed pixels,
  cap memory, discard buffers immediately, poll only when a walk window is near.

## Workflow rules

1. **One phase per conversation.** Read `CLAUDE.md`, `STATE.md`, and the current phase in
   `PLAN.md` first. Never start the next phase in the same conversation — not even "just a bit".
2. **End of phase ritual**, in order:
   1. Update `STATE.md`: status, date, what was built, decisions with one-line rationale,
      open questions carried forward.
   2. Add a `CHANGELOG.md` entry (Keep a Changelog format, SemVer).
   3. Make a conventional commit and **push** — the next phase may start on the other machine.
      A phase is not finished until pushed.
   4. Give the user a short summary **in plain, non-technical language** of what was done, and an
      explicit statement whether the next phase can start.
3. **Any deviation from `PLAN.md` must be recorded in `STATE.md` before proceeding.**
4. Sole contributor, no PR review. Work happens on the `main` branch unless an experiment needs a
   throwaway branch.
5. Commits: [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
   `docs:`, `chore:`, `test:`, `refactor:`, `ci:`). Small, self-contained commits within a phase
   are fine; the phase-closing commit is mandatory.
6. **Secrets discipline from day one:** the repo is private during development but becomes public
   at release 1.0.0, and history comes with it. Never commit tokens, API keys, personal
   coordinates, or HA URLs. Machine/user secrets go in `.env` (git-ignored); document required
   variables in `.env.example`. `.claude/settings.local.json` is git-ignored.
7. Two dev machines (Windows + Linux), same repo. Everything needed to work must be committed;
   always push at the end of a session. `STATE.md` is the handover document.

## Definition of done for a phase

A phase is done when **all** of the following hold:

- Every acceptance criterion listed for the phase in `PLAN.md` is met.
- The phase's testable outcome actually passes (tests green, doc complete, CI green — whatever
  the phase defines).
- No new lint/type errors; pre-commit passes (once tooling exists, phase 2+).
- `STATE.md` updated, `CHANGELOG.md` entry added, conventional commit created and **pushed**.
- The user received the plain-language summary and the go/no-go statement for the next phase.
