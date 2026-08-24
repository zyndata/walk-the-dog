"""Skeleton tests: manifest sanity and config-entry setup/unload."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.walk_the_dog.const import DOMAIN

MANIFEST = Path(__file__).parents[1] / "custom_components" / "walk_the_dog" / "manifest.json"


def test_manifest_matches_const() -> None:
    """The manifest and const.py must agree on the domain."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert manifest["version"]


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """The skeleton config entry sets up and unloads cleanly."""
    entry = MockConfigEntry(domain=DOMAIN, title="Walk the dog")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
