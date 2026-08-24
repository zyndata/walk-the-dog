"""Config flow for the Walk the dog integration.

The 3-step wizard and the options flow (docs/CONFIG.md) are implemented in
phase 5. Until then the flow aborts so the integration cannot be half-configured.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class WalkTheDogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Walk the dog config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Abort until the wizard is implemented (phase 5)."""
        return self.async_abort(reason="not_implemented")
