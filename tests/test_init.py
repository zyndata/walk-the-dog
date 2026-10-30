"""Manifest sanity, and the config entry's setup/unload/reload lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.walk_the_dog import ISSUE_TIMING_FITTED
from custom_components.walk_the_dog.const import (
    CONF_EARLIER_MARGIN_MIN,
    CONF_SCHEDULE_MODE,
    CONF_WALK_DURATION_MIN,
    DOMAIN,
    RADAR_REACH_MIN,
    SCHEDULE_MODE_DAILY,
)
from custom_components.walk_the_dog.coordinator import WalkCoordinator

from .conftest import ENTRY_DATA, ENTRY_OPTIONS, hourly_sources, setup_entry
from .test_config_flow import PARAMS

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from .conftest import FakeFetch

MANIFEST = Path(__file__).parents[1] / "custom_components" / "walk_the_dog" / "manifest.json"

IDLE = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)


def test_manifest_matches_const() -> None:
    """The manifest and const.py must agree on the domain."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert manifest["version"]


def test_the_manifest_allows_only_one_entry() -> None:
    """One home, one schedule, one recommendation sensor (docs/CONFIG.md)."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["single_config_entry"] is True


@pytest.fixture(autouse=True)
def _idle_clock(freezer: FrozenDateTimeFactory, fetch: FakeFetch) -> None:
    """Set up away from any walk window, so setup makes no request."""
    freezer.move_to(IDLE)
    fetch.build = lambda now: hourly_sources(now, [0.0] * 5)


async def test_setup_and_unload_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The entry sets up its coordinator and both platforms, and unloads cleanly."""
    coordinator = await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(coordinator, WalkCoordinator)
    assert hass.states.get("sensor.walk_the_dog_walk_recommendation") is not None
    assert hass.states.get("switch.walk_the_dog_alerting") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("sensor.walk_the_dog_walk_recommendation").state == STATE_UNAVAILABLE


async def test_editing_the_schedule_reaches_the_coordinator(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """`OptionsFlowWithReload` reloads the entry, so a new walk time takes effect.

    No config-entry update listener is registered anywhere — that class forbids
    combining the two — so this is the only path the new settings can travel.
    """
    await setup_entry(hass, entry)
    assert entry.runtime_data.data.walk_start == datetime(2026, 8, 25, 5, 0, tzinfo=UTC)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCHEDULE_MODE: SCHEDULE_MODE_DAILY}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"all": ["06:00"]}
    )
    # One notification step per walk sits between the schedule and the parameters.
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.config_entries.options.async_configure(result["flow_id"], PARAMS)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.data.walk_start == datetime(2026, 8, 25, 6, 0, tzinfo=UTC)


async def test_setup_makes_no_request_when_no_walk_is_near(
    hass: HomeAssistant, entry: MockConfigEntry, fetch: FakeFetch
) -> None:
    """Setting the integration up is not itself a reason to call a weather provider."""
    await setup_entry(hass, entry)

    assert fetch.calls == 0


# --- the 1.3.0 migration: timing fitted to the radar's reach -------------------


async def _legacy_entry(hass: HomeAssistant, **timing: int) -> MockConfigEntry:
    """An entry as 1.2 stored it: minor version 1, timing exactly as given."""
    await hass.config.async_set_time_zone("UTC")
    legacy = MockConfigEntry(
        domain=DOMAIN,
        title="Walk the dog",
        data=ENTRY_DATA,
        options={**ENTRY_OPTIONS, **timing},
        version=1,
        minor_version=1,
    )
    legacy.add_to_hass(hass)
    return legacy


async def test_an_old_entry_is_fitted_to_the_radar_and_told_why(
    hass: HomeAssistant, fetch: FakeFetch, freezer: FrozenDateTimeFactory
) -> None:
    """The reported settings — 60 minutes of notice for a 15-minute walk — become 30.

    And a repair issue says so: a notice period that shrank on its own is exactly
    what a user would otherwise report as a bug.
    """
    freezer.move_to(IDLE)
    legacy = await _legacy_entry(hass, **{CONF_EARLIER_MARGIN_MIN: 60, CONF_WALK_DURATION_MIN: 15})

    await setup_entry(hass, legacy)

    assert legacy.minor_version == 2
    assert legacy.options[CONF_EARLIER_MARGIN_MIN] == 30
    assert legacy.options[CONF_WALK_DURATION_MIN] == 15
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_TIMING_FITTED)
    assert issue is not None
    assert issue.translation_placeholders == {
        "reach": str(RADAR_REACH_MIN),
        "earlier_before": "60",
        "earlier_after": "30",
        "duration_before": "15",
        "duration_after": "15",
    }


async def test_an_old_entry_that_already_fits_is_only_renumbered(
    hass: HomeAssistant, fetch: FakeFetch, freezer: FrozenDateTimeFactory
) -> None:
    """Nobody inside the rule is shown a repair issue for nothing."""
    freezer.move_to(IDLE)
    legacy = await _legacy_entry(hass, **{CONF_EARLIER_MARGIN_MIN: 30, CONF_WALK_DURATION_MIN: 15})

    await setup_entry(hass, legacy)

    assert legacy.minor_version == 2
    assert legacy.options[CONF_EARLIER_MARGIN_MIN] == 30
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_TIMING_FITTED) is None
