# Development

Two dev machines (Windows + Linux), one repo. Everything needed to work is committed;
machine-specific values live in `.env` (git-ignored). This document covers both OSes — every
project task is a Python script under `scripts/` invoked identically everywhere.

## Prerequisites

- **git**
- **[uv](https://docs.astral.sh/uv/)** — provisions the pinned Python (3.14) and installs
  dependencies; the system Python version does not matter (any Python 3.10+ can drive the
  scripts).
  - Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

  Open a new terminal afterwards so `uv` is on `PATH`.

## Setup

```
git clone git@github.com:zyndata/walk-the-dog.git
cd walk-the-dog
python scripts/setup.py
```

(`python` may be `python3` on Linux — either works for the scripts.)

This creates `.venv/` with Python 3.14, installs the pinned dev dependencies from
`requirements-dev.txt`, and installs the pre-commit hooks. It is idempotent — re-run it after
`requirements-dev.txt` changes.

## Everyday tasks

| Command | What it does |
|---|---|
| `python scripts/lint.py` | `ruff check` + `ruff format --check` (what CI runs) |
| `python scripts/format.py` | auto-format + auto-fix lint findings |
| `python scripts/test.py [pytest args]` | run the test suite (e.g. `python scripts/test.py -k manifest`) |
| `python scripts/install.py` | deploy `custom_components/walk_the_dog/` into a local HA instance |

Tests use `pytest-homeassistant-custom-component` and recorded fixtures — they must pass with
no network access.

## Deploying into a test Home Assistant instance

1. Copy `.env.example` to `.env` and set `HA_CONFIG_DIR` to your HA configuration directory
   (the folder containing `configuration.yaml`). Same variable on both OSes; use a normal
   absolute path for the OS you are on.
2. `python scripts/install.py` — copies the integration in (refusing to touch a symlinked
   target), then restart Home Assistant.

Alternative on Linux: symlink once and only restart HA afterwards:
`ln -s "$(pwd)/custom_components/walk_the_dog" /path/to/ha-config/custom_components/walk_the_dog`.

## Versions and pins

- `requirements-dev.txt` pins exact versions. `pytest` must stay at the exact version
  `pytest-homeassistant-custom-component` requires — bump the two together (phcc tracks Home
  Assistant releases; its version also pins the `homeassistant` package used in tests).
- The `ruff` pin must match the `rev` in `.pre-commit-config.yaml`.
- The Python version for the venv is pinned in `scripts/_env.py` (`PYTHON_VERSION`).

## CI

Every push and PR runs two workflows:

- **Validate** — `hassfest` (Home Assistant manifest/translations checks) and HACS validation
  (`ignore: brands` until the brands PR is submitted in phase 9).
- **CI** — `scripts/setup.py` + `scripts/lint.py` + `scripts/test.py`, i.e. exactly what you
  run locally.

## Git workflow between machines

- Work happens on `main` (sole contributor); throwaway branches only for experiments.
- **Push at the end of every session** — the other machine may pick up next.
- `STATE.md` is the handover document: read it first, update it at every phase end.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`, `ci:`).
- Never commit secrets, personal coordinates, or HA URLs — the repo goes public at 1.0.0
  with its full history. Machine-specific values go in `.env`.
