"""Every string the integration can show has to exist, in every language file.

A missing key is invisible in Python and shows up in the frontend as a raw
`walk_the_dog::config::step::…` placeholder, so the check is structural: the step
ids and error keys are read out of the source, not restated here.

The same applies one level up, to the translations themselves. `hassfest` only
validates `strings.json` and `translations/en.json` for a custom integration, so
`translations/pl.json` has no upstream check at all — the parity tests at the end
of this file are it.
"""

from __future__ import annotations

import ast
import json
import string
from pathlib import Path
from typing import Any

import pytest

from custom_components.walk_the_dog.config_flow import SLOT_LABEL_PREFIX
from custom_components.walk_the_dog.const import (
    DEVICE_TRANSLATION_KEY,
    INTEGRATION_NAME,
    INTENSITY_MM_H,
)
from custom_components.walk_the_dog.notifier import ALERT_DIRECTIONS, TEXT_PREFIX
from custom_components.walk_the_dog.schedule import DAY_KEYS, SCHEDULE_KEYS, SCHEDULE_MODES
from custom_components.walk_the_dog.sensor import OPTIONS

COMPONENT = Path(__file__).parents[1] / "custom_components" / "walk_the_dog"
CONFIG_FLOW = COMPONENT / "config_flow.py"
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

#: Every language the integration ships beyond the base file.
TRANSLATED = ("pl",)

#: The Polish name of the integration, used wherever Home Assistant lets a name be
#: translated: the integration itself, its device, and the notification title.
POLISH_TITLE = "Idź już z psem"

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


def test_every_sensor_state_is_translated() -> None:
    """The sensor shows words, not the identifiers the engine works in."""
    states = STRINGS["entity"]["sensor"]["recommendation"]["state"]

    assert set(states) == set(OPTIONS)


def test_both_entities_are_named() -> None:
    """An entity without a translated name shows up as a raw placeholder."""
    assert STRINGS["entity"]["sensor"]["recommendation"]["name"]
    assert STRINGS["entity"]["switch"]["alerting"]["name"]


def test_every_notification_has_a_text() -> None:
    """A direction the notifier can announce needs something to announce it with.

    They live under `common` because Home Assistant allows no other top-level key
    for strings that belong to no form and no entity — see `notifier.py`.
    """
    expected = {f"{TEXT_PREFIX}title"} | {
        f"{TEXT_PREFIX}{direction}" for direction in ALERT_DIRECTIONS
    }

    assert expected <= set(STRINGS["common"])


def test_every_schedule_slot_has_a_day_label() -> None:
    """The notification step names the days of the walk it is asking about.

    Its description is prose, not a field, so the labels live under `common` and
    are read back at runtime — the same route the notification texts take.
    """
    slots = {key for keys in SCHEDULE_KEYS.values() for key in keys}
    assert slots == {"all", "weekday", "weekend", *DAY_KEYS}

    expected = {f"{SLOT_LABEL_PREFIX}{slot}" for slot in slots}

    assert expected <= set(STRINGS["common"])


def _leaves(node: Any, prefix: str = "") -> dict[str, str]:
    """Every translated string in a language file, keyed by its path.

    Comparing paths rather than nested dicts is what makes a missing key report
    *which* key is missing instead of dumping two documents side by side.
    """
    leaves: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            leaves.update(_leaves(value, path))
        else:
            leaves[path] = value
    return leaves


def _placeholders(text: str) -> set[str]:
    """The `{name}` slots a string expects to be filled with."""
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


ENGLISH = _leaves(STRINGS)


def _language(code: str) -> dict[str, str]:
    return _leaves(json.loads((COMPONENT / "translations" / f"{code}.json").read_text("utf-8")))


def test_the_base_file_names_the_integration() -> None:
    """The root `title` is what a translation can override; en repeats the manifest."""
    assert STRINGS["title"] == INTEGRATION_NAME
    assert STRINGS["device"][DEVICE_TRANSLATION_KEY]["name"] == INTEGRATION_NAME


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_translation_has_every_key(code: str) -> None:
    """A key missing from a translation shows the user a raw identifier."""
    assert set(_language(code)) == set(ENGLISH)


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_translation_fills_every_key(code: str) -> None:
    """An empty string is worse than an English one — it shows nothing at all."""
    assert [path for path, text in _language(code).items() if not text.strip()] == []


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_translation_keeps_every_placeholder(code: str) -> None:
    """A renamed or dropped `{slot}` silently leaves the sentence unfinished.

    `notifier.py` falls back to the unformatted template when a placeholder does
    not resolve, so this failure would reach the phone as literal `{recommended}`.
    """
    translated = _language(code)
    mismatched = {
        path: (_placeholders(ENGLISH[path]), _placeholders(text))
        for path, text in translated.items()
        if path in ENGLISH and _placeholders(ENGLISH[path]) != _placeholders(text)
    }

    assert mismatched == {}


@pytest.mark.parametrize("code", TRANSLATED)
def test_a_translation_is_not_a_copy(code: str) -> None:
    """Every string is actually translated, not left in English.

    Nothing in this integration is a brand name that has to stay untranslated —
    `manifest.json` carries the only such name, and it is not in these files — so
    any value identical to the English one is an oversight.
    """
    translated = _language(code)
    copied = [path for path, text in translated.items() if ENGLISH.get(path) == text]

    assert copied == []


def test_polish_uses_the_localized_title_everywhere() -> None:
    """The one name a Polish user should see, in all three places it can appear."""
    polish = json.loads((COMPONENT / "translations" / "pl.json").read_text("utf-8"))

    assert polish["title"] == POLISH_TITLE
    assert polish["device"][DEVICE_TRANSLATION_KEY]["name"] == POLISH_TITLE
    assert polish["common"][f"{TEXT_PREFIX}title"] == POLISH_TITLE
