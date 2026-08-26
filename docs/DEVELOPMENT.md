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
| `python scripts/make_chmi_fixtures.py` | re-record `tests/fixtures/chmi/` from opendata.chmi.cz |

Tests use `pytest-homeassistant-custom-component` and recorded fixtures — they must pass with
no network access.

`tests/fixtures/chmi/` is the one fixture set with a regeneration script,
`scripts/make_chmi_fixtures.py`: it downloads a real observed composite and one forecast archive
from opendata.chmi.cz, and synthesizes two extra frames on the same geometry (no echo anywhere, and
echo only over Praha) to prove negatives a recorded frame cannot. **The expected mm/h values in
`tests/test_chmi.py` are pinned to the recorded bytes**, so re-recording means recomputing them —
which is deliberate, and is why it is a separate script rather than something the suite does.

### Tests do not run natively on Windows

Home Assistant imports `fcntl` (`homeassistant/runner.py`), a Unix-only module, so
`pytest-homeassistant-custom-component` fails at plugin import on Windows — before any test
runs. Everything else (`setup.py`, `lint.py`, `format.py`, `install.py`) works on both OSes.

Run the suite on the Windows machine through a Linux container instead:

```
docker run --rm --network none -v "%CD%:/repo" -w /repo ghcr.io/astral-sh/uv:python3.14-bookworm-slim   sh -c "uv venv /tmp/v && uv pip install --python /tmp/v/bin/python -r requirements-dev.txt && /tmp/v/bin/python -m pytest"
```

(`--network none` also proves the offline requirement.) Drop `--network none` on the first run so
the dependencies can be downloaded, or bake them into an image. On the Linux machine, and in CI,
`python scripts/test.py` runs directly.

## Deploying into a test Home Assistant instance

Which route to use depends on how the test instance runs.

### HACS custom repository (simplest for Home Assistant OS)

Home Assistant OS has no config folder a dev machine can write to directly, so install from
the repository instead — this is also the closest thing to how real users will install it.

1. In HACS: **⋮ → Custom repositories**, paste `https://github.com/zyndata/walk-the-dog`,
   category **Integration**, **Add**.
2. Find **Walk the dog**, **Download**, then restart Home Assistant.
3. To pick up new work: push to `main`, then **Redownload** in HACS and restart.

There are no releases yet, so HACS installs from the default branch — every push to `main` is
immediately installable. Once `v1.0.0` is tagged in phase 9, HACS switches to installing
releases.

### Local config folder (a container or Core install on the same machine)

1. Copy `.env.example` to `.env` and set `HA_CONFIG_DIR` to your HA configuration directory
   (the folder containing `configuration.yaml`). Same variable on both OSes; use a normal
   absolute path for the OS you are on.
2. `python scripts/install.py` — copies the integration in (refusing to touch a symlinked
   target), then restart Home Assistant.

Alternative on Linux: symlink once and only restart HA afterwards:
`ln -s "$(pwd)/custom_components/walk_the_dog" /path/to/ha-config/custom_components/walk_the_dog`.

### Home Assistant OS over a network share

If you want `scripts/install.py` against a HAOS instance, install the **Samba share** add-on,
start it, authenticate to `\\homeassistant` from the dev machine first (otherwise the script
reports the directory as missing), then set `HA_CONFIG_DIR=\\homeassistant\config`. The `.env`
parser keeps backslashes literally — no quoting or escaping.

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
- Never commit secrets, personal coordinates, or HA URLs — **the repository is public**, and
  its full history with it. Machine-specific values go in `.env`. GitHub secret scanning and
  push protection are enabled, but they are a backstop, not the rule.
