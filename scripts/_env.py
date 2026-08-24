"""Shared helpers for the task-runner scripts. Same behaviour on Windows and Linux."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".venv"
PYTHON_VERSION = "3.14"


def venv_bin(tool: str) -> Path:
    """Path of a tool inside the project virtualenv."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / f"{tool}.exe"
    return VENV_DIR / "bin" / tool


def find_uv() -> Path | None:
    """Locate uv on PATH or in its default install locations."""
    found = shutil.which("uv")
    if found:
        return Path(found)
    suffix = ".exe" if os.name == "nt" else ""
    for candidate in (
        Path.home() / ".local" / "bin" / f"uv{suffix}",
        Path.home() / ".cargo" / "bin" / f"uv{suffix}",
    ):
        if candidate.is_file():
            return candidate
    return None


def run(cmd: list[str | Path]) -> int:
    """Run a command from the repo root, echoing it first."""
    printable = " ".join(str(part) for part in cmd)
    print(f"$ {printable}")
    return subprocess.run([str(part) for part in cmd], cwd=REPO_ROOT, check=False).returncode


def run_tool(tool: str, args: list[str]) -> int:
    """Run a tool from the virtualenv, failing clearly if setup has not been run."""
    exe = venv_bin(tool)
    if not exe.is_file():
        print(f"error: {exe} not found — run `python scripts/setup.py` first", file=sys.stderr)
        return 1
    return run([exe, *args])


def load_dotenv() -> dict[str, str]:
    """Parse the repo-root .env file (simple KEY=VALUE lines, # comments)."""
    env_file = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_file.is_file():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values
