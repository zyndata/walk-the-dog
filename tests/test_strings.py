"""Every string the flows can show has to exist, in both files.

A missing key is invisible in Python and shows up in the frontend as a raw
`walk_the_dog::config::step::…` placeholder, so the check is structural: the step
ids and error keys are read out of the source, not restated here.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.walk_the_dog.const import INTENSITY_MM_H
from custom_components.walk_the_dog.schedule import SCHEDULE_MODES

COMPONENT = Path(__file__).parents[1] / "custom_components" / "walk_the_dog"
CONFIG_FLOW = COMPONENT / "config_flow.py"
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

#: Shown only by the options flow — the wizard has no `init` step, and neither
#: flow renders a form for it.
FORWARDING_STEPS = {"init"}


def _string_literals(keyword: str) -> set[str]:
    """Every literal passed as `keyword=` anywhere in config_flow.py."""
    tree = ast.parse(CONFIG_FLOW.read_text(encoding="utf-8"))
    return {
        node.value.value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        for node in call.keywords
        if node.arg == keyword and isinstance(node.value, ast.Constant)
    }


def _error_keys() -> set[str]:
    """Error keys the flows can report, from the modules that define them."""
    keys = set()
    for path in (CONFIG_FLOW, COMPONENT / "schedule.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id.startswith("ERROR_")
                and isinstance(node.value, ast.Constant)
            ):
                keys.add(node.value.value)
    return keys


SHOWN_STEPS = _string_literals("step_id")
ERROR_KEYS = _error_keys()


def test_the_source_scan_found_something() -> None:
    """Guard against an AST walk that silently matches nothing."""
    assert "user" in SHOWN_STEPS
    assert ERROR_KEYS


def test_translations_match_strings() -> None:
    """`translations/en.json` is the base language file — it must not drift."""
    english = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))

    assert english == STRINGS


@pytest.mark.parametrize("section", ["config", "options"])
def test_every_shown_step_has_strings(section: str) -> None:
    """Both flows show the same forms, so both sections need the same steps."""
    expected = SHOWN_STEPS - ({"user"} if section == "options" else set())

    assert set(STRINGS[section]["step"]) == expected - FORWARDING_STEPS


@pytest.mark.parametrize("section", ["config", "options"])
def test_every_error_key_has_a_message(section: str) -> None:
    """An error the flow can raise but cannot render is worse than no validation."""
    assert set(STRINGS[section]["error"]) == ERROR_KEYS


@pytest.mark.parametrize("section", ["config", "options"])
def test_every_field_is_labelled(section: str) -> None:
    """No field may reach the user without a label."""
    for step_id, step in STRINGS[section]["step"].items():
        assert step["title"], step_id
        assert step.get("data"), step_id


def _selector_options(key: str) -> dict[str, Any]:
    return STRINGS["selector"][key]["options"]


def test_schedule_modes_are_translated() -> None:
    """The mode picker shows names, not the stored identifiers."""
    assert set(_selector_options("schedule_mode")) == set(SCHEDULE_MODES)


def test_intensity_thresholds_are_translated() -> None:
    """Same for the intensity scale."""
    assert set(_selector_options("intensity_threshold")) == set(INTENSITY_MM_H)
