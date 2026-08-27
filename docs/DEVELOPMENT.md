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
| `python scripts/make_branding.py` | redraw the icon and logo in `custom_components/walk_the_dog/brand/` |
| `python scripts/benchmark.py` | measure what one update cycle costs (offline, see below) |
| `python scripts/measure_publish_lag.py` | measure how long after its stamp a frame is fetchable (**live requests**) |

Tests use `pytest-homeassistant-custom-component` and recorded fixtures — they must pass with
no network access.

`tests/fixtures/chmi/` is the one fixture set with a regeneration script,
`scripts/make_chmi_fixtures.py`: it downloads a real observed composite and one forecast archive
from opendata.chmi.cz, and synthesizes two extra frames on the same geometry (no echo anywhere, and
echo only over Praha) to prove negatives a recorded frame cannot. **The expected mm/h values in
`tests/test_chmi.py` are pinned to the recorded bytes**, so re-recording means recomputing them —
which is deliberate, and is why it is a separate script rather than something the suite does.

### Translations

`strings.json` is the source of truth; `translations/en.json` is a byte-identical copy of it and
`translations/pl.json` is the Polish translation. Editing a user-facing string means editing all
three — `tests/test_strings.py` fails otherwise, and it is the only check `pl.json` has:
`hassfest` validates `strings.json` and `en.json` for a custom integration and ignores every
other language file.

The tests check that the Polish file has every key the English one has, that no value is empty,
that no value was left in English, and that every `{placeholder}` survived translation — the
last one matters because a mistyped placeholder reaches the phone as literal `{recommended}`.

In a running Home Assistant, a changed translation needs a **full restart**. Reloading the
integration re-reads the code but not the frontend's translation cache, which is how a step can
appear with raw `walk_the_dog::config::step::…` labels next to a fully labelled one.

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

### Measuring performance

Two tools, plus the suite itself. All three are how the numbers in
[ARCHITECTURE.md](ARCHITECTURE.md) § Resource budget were obtained, and re-running them is how
they are re-checked.

```
python scripts/benchmark.py                       # per-cycle CPU, memory, loop stalls, requests
pytest tests/test_performance.py --log-cli-level=INFO   # a simulated day: requests, cycles, latency
python scripts/measure_publish_lag.py --minutes 50      # live: how late each source publishes
```

`benchmark.py` needs only numpy, Pillow and aiohttp — it loads the adapters without starting Home
Assistant — so it runs natively on Windows too, and inside any small container. The published
figures were taken with one core and 512 MB, which is the shape of the weakest supported box:

```
docker run --rm --network none --cpus=1 --memory=512m -v "%CD%:/repo" -w /repo   ghcr.io/astral-sh/uv:python3.14-bookworm-slim   sh -c "uv venv /tmp/v && uv pip install --python /tmp/v/bin/python numpy pillow aiohttp && /tmp/v/bin/python scripts/benchmark.py"
```

It reports each profile's **cold** cycle (empty cache) and its **warm** ones separately, because
they cost very different amounts, and `--no-warmup` folds Pillow's and numpy's one-off start-up
into the first cycle — which is what a Home Assistant restart really does.

`measure_publish_lag.py` is the only tool here that touches the network. It polls two small
endpoints every 20 s and reports, per source, how long after a frame's own timestamp the frame
could first be read. That is what `const.PUBLISH_SETTLE_S` is set from; run it for at least
40 minutes to see several publications of each.

## Deploying into a test Home Assistant instance

Which route to use depends on how the test instance runs.

### HACS custom repository (simplest for Home Assistant OS)

Home Assistant OS has no config folder a dev machine can write to directly, so install from
the repository instead — this is also the closest thing to how real users will install it.

1. In HACS: **⋮ → Custom repositories**, paste `https://github.com/zyndata/walk-the-dog`,
   category **Integration**, **Add**.
2. Find **Walk the dog**, **Download**, then restart Home Assistant.
3. To pick up new work: publish a release (below), then **Redownload** in HACS and restart.

HACS installs the newest release and shows its version number. To test an unreleased commit,
pick **Redownload → show all versions → main** — HACS then names the install by its commit
hash, which is exactly what the version numbers exist to avoid.

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

## Releasing

One version number, in `custom_components/walk_the_dog/manifest.json`. Home Assistant shows it
under the integration, HACS shows it in the store, and the git tag mirrors it. Nothing derives a
version from the git history — without a release, HACS falls back to naming an install by its
commit hash, which means nothing to the person reading it.

1. Bump `version` in `manifest.json` (SemVer).
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty
   `## [Unreleased]` above it, and update the link references at the bottom of the file.
3. `python scripts/release.py` — checks that the two agree. `tests/test_release.py` checks the
   same thing in CI, so a forgotten bump fails the build rather than reaching a user.
4. Commit, push, then `python scripts/release.py --tag` — it refuses a dirty tree or an
   existing tag, then pushes `vX.Y.Z`.
5. The **Release** workflow picks the tag up, re-checks the tag against the manifest, and
   publishes a GitHub release whose notes are that changelog section. HACS offers it within
   the hour.

Never publish a release as a *pre-release*: HACS hides those unless the user has opted into
beta versions, so the update would silently not appear.

## Versions and pins

- `requirements-dev.txt` pins exact versions. `pytest` must stay at the exact version
  `pytest-homeassistant-custom-component` requires — bump the two together (phcc tracks Home
  Assistant releases; its version also pins the `homeassistant` package used in tests).
- The `ruff` pin must match the `rev` in `.pre-commit-config.yaml`.
- The Python version for the venv is pinned in `scripts/_env.py` (`PYTHON_VERSION`).
- **Shipped code has a syntax floor of Python 3.13**, one release below what the minimum Home
  Assistant runs. A newer grammar is not a degraded feature — the integration fails to import
  and the user gets a traceback instead of a config flow, and the manual install route has no
  version gate at all. `tests/test_syntax_floor.py` parses every shipped module against that
  floor; it is the only thing that catches it, because the dev interpreter is 3.14.

## CI

Every push and PR runs two workflows:

- **Validate** — `hassfest` (Home Assistant manifest/translations checks) and HACS validation,
  which runs with no ignores.
- **CI** — `scripts/setup.py` + `scripts/lint.py` + `scripts/test.py`, i.e. exactly what you
  run locally.

A third workflow, **Release**, runs only on a `v*` tag (see [Releasing](#releasing)). It is the
only one with write permission, and the only one that publishes anything.

## Git workflow between machines

- Work happens on `main` (sole contributor); throwaway branches only for experiments.
- **Push at the end of every session** — the other machine may pick up next.
- `STATE.md` is the handover document: read it first, update it at every phase end.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`, `ci:`).
- Never commit secrets, personal coordinates, or HA URLs — **the repository is public**, and
  its full history with it. Machine-specific values go in `.env`. GitHub secret scanning and
  push protection are enabled, but they are a backstop, not the rule.
