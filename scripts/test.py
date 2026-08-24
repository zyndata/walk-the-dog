"""Run the test suite. Usage: python scripts/test.py [pytest args...]"""

from __future__ import annotations

import sys

from _env import run_tool


def main() -> int:
    return run_tool("pytest", sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
