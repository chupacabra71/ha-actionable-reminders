"""Config flow for Actionable Reminders.

The config flow creates the single hub entry. Individual reminders are added and
edited as *subentries* of the hub (see subentry_flow.ReminderSubentryFlow), and
hub defaults are edited via the options flow.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_TYPE_HUB,
    SUBENTRY_TYPE_REMINDER,
    CONF_DEFAULT_RETRY_INTERVAL,
    CONF_DEFAULT_MAX_RETRIES,
    CONF_DEFAULT_ESCALATION_INTERVAL,
    CONF_DEFAULT_MAX_ESCALATIONS,
    CONF_EARLIEST_RETRY_TIME,
    CONF_DEFAULT_MOBILE_SERVICE,
    CONF_DEFAULT_ALEXA_DEVICES,
    CONF_DEFAULT_ACTIONABLE,
    CONF_DEFAULT_PRESENCE_SENSORS,
    CONF_DEFAULT_QUIET_START,
    CONF_DEFAULT_QUIET_END,
    DEFAULT_RETRY_INTERVAL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_ESCALATION_INTERVAL,
    DEFAULT_MAX_ESCALATIONS,
    DEFAULT_EARLIEST_RETRY_TIME,
    DEFAULT_ACTIONABLE,
    DEFAULT_QUIET_START,
    DEFAULT_QUIET_END,
)

_LOGGER = logging.getLogger(__name__)


class ActionableRemindersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Actionable Reminders (creates the hub)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Create the single hub. Reminders are added as subentries of it."""
        if any(
            entry.data.get("type") == CONF_TYPE_HUB
            for entry in self._async_current_entries()
        ):
            return self.async_abort(reason="single_instance_allowed")
        return await self._create_hub()

    async def _create_hub(self) -> FlowResult:
        """Create the hub with sensible defaults."""
        _LOGGER.info("Creating Actionable Reminders hub with default settings")
        hub_data = {
            "type": CONF_TYPE_HUB,
            CONF_DEFAULT_RETRY_INTERVAL: DEFAULT_RETRY_INTERVAL,
            CONF_DEFAULT_MAX_RETRIES: DEFAULT_MAX_RETRIES,
            CONF_DEFAULT_ESCALATION_INTERVAL: DEFAULT_ESCALATION_INTERVAL,
            CONF_DEFAULT_MAX_ESCALATIONS: DEFAULT_MAX_ESCALATIONS,
            CONF_EARLIEST_RETRY_TIME: DEFAULT_EARLIEST_RETRY_TIME,
            CONF_DEFAULT_ACTIONABLE: DEFAULT_ACTIONABLE,
            CONF_DEFAULT_QUIET_START: DEFAULT_QUIET_START,
            CONF_DEFAULT_QUIET_END: DEFAULT_QUIET_END,
            CONF_DEFAULT_MOBILE_SERVICE: None,
            CONF_DEFAULT_ALEXA_DEVICES: [],
            CONF_DEFAULT_PRESENCE_SENSORS: [],
        }
        return self.async_create_entry(title="Actionable Reminders Hub", data=hub_data)

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        """Reminders are subentries of the hub, added/edited via the wizard."""
        from .subentry_flow import ReminderSubentryFlow

        return {SUBENTRY_TYPE_REMINDER: ReminderSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Options flow — hub defaults only (reminders use the subentry flow)."""
        from .options_flow import ActionableRemindersHubOptionsFlow

        return ActionableRemindersHubOptionsFlow()


# Home Assistant expects "ConfigFlow" as the class name
ConfigFlow = ActionableRemindersConfigFlow
