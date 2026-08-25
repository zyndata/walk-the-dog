"""The engine's purity is a structural property, so it is checked structurally.

docs/ARCHITECTURE.md makes `engine/*` pure — no I/O, no Home Assistant imports, no
clock reads, `now` always a parameter — because that is what makes every rain
scenario testable as plain arithmetic and every decision reproducible from a
recorded set of series. A stray `datetime.now()` would quietly undo that without
failing a single behavioural test, so this module reads the source instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE = Path(__file__).parents[1] / "custom_components" / "walk_the_dog" / "engine"
MODULES = sorted(ENGINE.glob("*.py"))

#: Absolute imports the engine may use: the standard library, and nothing else.
ALLOWED_ROOTS = {
    "__future__",
    "bisect",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "math",
    "typing",
}

#: Reading a clock, or anything that touches the outside world.
FORBIDDEN_CALLS = {"now", "utcnow", "today", "time", "monotonic", "sleep", "open"}


def _module_names(tree: ast.AST) -> set[str]:
    """Absolute module roots imported by a parsed module (relative imports excluded)."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_engine_package_is_not_empty() -> None:
    """Guard against the glob silently finding nothing and passing every check."""
    assert {path.name for path in MODULES} == {
        "__init__.py",
        "consensus.py",
        "grid.py",
        "window.py",
    }


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_engine_module_imports_only_the_standard_library(path: Path) -> None:
    """No homeassistant, no aiohttp, no numpy: the engine is plain Python over dataclasses."""
    roots = _module_names(ast.parse(path.read_text(encoding="utf-8")))

    assert roots <= ALLOWED_ROOTS, f"{path.name} imports {sorted(roots - ALLOWED_ROOTS)}"


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_engine_module_never_reads_a_clock_or_does_io(path: Path) -> None:
    """`now` is a parameter everywhere — nothing in here may ask the system for it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }

    assert not called & FORBIDDEN_CALLS, f"{path.name} calls {sorted(called & FORBIDDEN_CALLS)}"


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_engine_module_defines_nothing_async(path: Path) -> None:
    """Async is where I/O lives; a pure decision layer has no use for it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    assert not [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
