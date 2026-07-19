"""Config flow for Actionable Reminders integration.

This module handles the initial setup flows for:
- Hub creation (with sensible defaults)
- Reminder creation (simplified 3-step process)
"""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_TYPE_HUB,
    CONF_TYPE_REMINDER,
    CONF_REMINDER_NAME,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_TIME,
    CONF_ONCE_DATE,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_MONTHLY_TYPE,
    CONF_SCHEDULE_MONTHLY_DAY,
    CONF_SCHEDULE_MONTHLY_WEEK,
    CONF_SCHEDULE_MONTHLY_WEEKDAY,
    CONF_PROMPT_MESSAGES,
    CONF_ACK_MESSAGES,
    CONF_DISMISS_MESSAGES,
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
    DEFAULT_ACK_MESSAGES,
    DEFAULT_DISMISS_MESSAGES,
    WEEKDAYS,
    WEEKDAY_LABELS,
    MONTHLY_WEEKS,
    MONTHLY_WEEK_LABELS,
)

_LOGGER = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

def _get_notify_services(hass) -> list[str]:
    """Get all available notify services."""
    services = []
    for service in hass.services.async_services().get("notify", {}):
        if service != "persistent_notification":
            services.append(f"notify.{service}")
    return sorted(services)


def _get_alexa_devices(hass) -> list[str]:
    """Get all Alexa/Echo Media Player entities."""
    alexa_entities = []
    for state in hass.states.async_all("media_player"):
        entity_lower = state.entity_id.lower()
        if "alexa" in entity_lower or "echo" in entity_lower:
            alexa_entities.append(state.entity_id)
    return sorted(alexa_entities)


def _get_presence_sensors(hass) -> list[str]:
    """Get all presence binary sensors."""
    presence = []
    for state in hass.states.async_all("binary_sensor"):
        if state.attributes.get("device_class") == "presence":
            presence.append(state.entity_id)
    return sorted(presence)


def _generate_id_from_name(name: str) -> str:
    """Generate a URL-safe ID from reminder name."""
    # Convert to lowercase, replace spaces with underscores, remove non-alphanumeric
    clean = re.sub(r'[^a-z0-9_]', '', name.lower().replace(' ', '_'))
    return clean or "reminder"


# ═══════════════════════════════════════════════════════════════════════════════
# Main Config Flow
# ═══════════════════════════════════════════════════════════════════════════════

class ActionableRemindersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Actionable Reminders."""

    VERSION = 1

    def __init__(self):
        """Initialize config flow."""
        self._data = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle initial step - check if hub exists."""
        # Check if hub already exists
        existing_entries = self._async_current_entries()
        hub_exists = any(
            entry.data.get("type") == CONF_TYPE_HUB
            for entry in existing_entries
        )
        
        if hub_exists:
            # Hub exists, start reminder creation
            return await self.async_step_reminder_step1()
        else:
            # No hub exists, create one
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
        
        # Create hub entry
        return self.async_create_entry(
            title="Actionable Reminders Hub",
            data=hub_data,
        )

    async def async_step_reminder_step1(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1: Name, schedule type, and message."""
        errors = {}

        if user_input is not None:
            # Validate unique name
            existing_names = {
                entry.title
                for entry in self._async_current_entries()
                if entry.data.get("type") == CONF_TYPE_REMINDER
            }
            
            if user_input[CONF_REMINDER_NAME] in existing_names:
                errors["base"] = "name_exists"
            else:
                # Store data and move to schedule step
                self._data = user_input
                return await self.async_step_reminder_step2()

        # Build schema
        data_schema = vol.Schema({
            vol.Required(CONF_REMINDER_NAME): str,
            vol.Required(CONF_SCHEDULE_TYPE, default="daily"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "Daily", "value": "daily"},
                        {"label": "Weekly", "value": "weekly"},
                        {"label": "Monthly", "value": "monthly"},
                        {"label": "One-time", "value": "once"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required("message"): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
        })

        return self.async_show_form(
            step_id="reminder_step1",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "info": "Enter the reminder name, how often it should repeat, and the question to ask."
            }
        )

    async def async_step_reminder_step2(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 2: Schedule details based on type."""
        if user_input is not None:
            # Combine with previous data
            self._data.update(user_input)
            return await self.async_step_reminder_step3()

        schedule_type = self._data[CONF_SCHEDULE_TYPE]

        # Build schema based on schedule type
        if schedule_type == "daily":
            data_schema = vol.Schema({
                vol.Required(CONF_SCHEDULE_TIME, default="09:00"): selector.TimeSelector(),
            })
            
        elif schedule_type == "weekly":
            data_schema = vol.Schema({
                vol.Required(CONF_SCHEDULE_TIME, default="09:00"): selector.TimeSelector(),
                vol.Required(CONF_SCHEDULE_DAYS, default=["mon", "tue", "wed", "thu", "fri"]): 
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"label": label, "value": day}
                                for day, label in WEEKDAY_LABELS.items()
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
            })
            
        elif schedule_type == "once":
            data_schema = vol.Schema({
                vol.Required(CONF_ONCE_DATE): selector.DateSelector(),
                vol.Required(CONF_SCHEDULE_TIME, default="09:00"): selector.TimeSelector(),
            })

        else:  # monthly
            data_schema = vol.Schema({
                vol.Required(CONF_SCHEDULE_TIME, default="09:00"): selector.TimeSelector(),
                vol.Required(CONF_SCHEDULE_MONTHLY_TYPE, default="day"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"label": "Specific Day (1-31)", "value": "day"},
                            {"label": "Week Pattern (e.g., First Wednesday)", "value": "week_pattern"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            })

        return self.async_show_form(
            step_id="reminder_step2",
            data_schema=data_schema,
            description_placeholders={
                "schedule_type": schedule_type.title(),
                "reminder_name": self._data[CONF_REMINDER_NAME],
            }
        )

    async def async_step_reminder_step3(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 3: Monthly details (if monthly) or submit."""
        schedule_type = self._data[CONF_SCHEDULE_TYPE]
        
        # If monthly and type is selected, show appropriate fields
        if schedule_type == "monthly":
            monthly_type = self._data.get(CONF_SCHEDULE_MONTHLY_TYPE)
            
            if monthly_type and user_input is None:
                # Show monthly-specific fields
                if monthly_type == "day":
                    data_schema = vol.Schema({
                        vol.Required(CONF_SCHEDULE_MONTHLY_DAY, default=1): selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=1,
                                max=31,
                                mode=selector.NumberSelectorMode.BOX,
                            )
                        ),
                    })
                else:  # week_pattern
                    data_schema = vol.Schema({
                        vol.Required(CONF_SCHEDULE_MONTHLY_WEEK, default="first"): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    {"label": label, "value": week}
                                    for week, label in MONTHLY_WEEK_LABELS.items()
                                ],
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        ),
                        vol.Required(CONF_SCHEDULE_MONTHLY_WEEKDAY, default="mon"): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    {"label": label, "value": day}
                                    for day, label in WEEKDAY_LABELS.items()
                                ],
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        ),
                    })
                
                return self.async_show_form(
                    step_id="reminder_step3",
                    data_schema=data_schema,
                    description_placeholders={
                        "monthly_type": "Specific Day" if monthly_type == "day" else "Week Pattern",
                    }
                )
        
        # If we have user_input, add it to data
        if user_input is not None:
            self._data.update(user_input)
        
        # Now create the reminder entry
        return await self._create_reminder()

    async def _create_reminder(self) -> FlowResult:
        """Create the reminder entry with collected data."""
        # Extract message and convert to list
        message = self._data.pop("message")
        
        # Build final config
        config = {
            "type": CONF_TYPE_REMINDER,
            CONF_REMINDER_NAME: self._data[CONF_REMINDER_NAME],
            CONF_SCHEDULE_TYPE: self._data[CONF_SCHEDULE_TYPE],
            CONF_SCHEDULE_TIME: self._data[CONF_SCHEDULE_TIME],
            CONF_PROMPT_MESSAGES: [message],  # Single message as list
            CONF_ACK_MESSAGES: DEFAULT_ACK_MESSAGES,
            CONF_DISMISS_MESSAGES: DEFAULT_DISMISS_MESSAGES,
        }
        
        # Add schedule-specific fields
        if self._data[CONF_SCHEDULE_TYPE] == "weekly":
            config[CONF_SCHEDULE_DAYS] = self._data.get(CONF_SCHEDULE_DAYS, [])

        elif self._data[CONF_SCHEDULE_TYPE] == "once":
            config[CONF_ONCE_DATE] = self._data.get(CONF_ONCE_DATE)
        
        elif self._data[CONF_SCHEDULE_TYPE] == "monthly":
            config[CONF_SCHEDULE_MONTHLY_TYPE] = self._data.get(CONF_SCHEDULE_MONTHLY_TYPE)
            if config[CONF_SCHEDULE_MONTHLY_TYPE] == "day":
                config[CONF_SCHEDULE_MONTHLY_DAY] = self._data.get(CONF_SCHEDULE_MONTHLY_DAY, 1)
            else:
                config[CONF_SCHEDULE_MONTHLY_WEEK] = self._data.get(CONF_SCHEDULE_MONTHLY_WEEK)
                config[CONF_SCHEDULE_MONTHLY_WEEKDAY] = self._data.get(CONF_SCHEDULE_MONTHLY_WEEKDAY)
        
        # Use reminder name as title
        reminder_name = config[CONF_REMINDER_NAME]
        
        return self.async_create_entry(
            title=reminder_name,
            data=config,
            description="Using hub defaults. Configure via options to customize notifications, retry behavior, and more.",
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Create a reminder programmatically (e.g. quick-add from the to-do list).

        Expects reminder_name (+ optional message, schedule_type default "once",
        schedule_time, once_date). Everything else falls back to hub defaults.
        """
        schedule_type = import_data.get(CONF_SCHEDULE_TYPE, "once")
        name = import_data[CONF_REMINDER_NAME]
        message = import_data.get("message", name)

        config = {
            "type": CONF_TYPE_REMINDER,
            CONF_REMINDER_NAME: name,
            CONF_SCHEDULE_TYPE: schedule_type,
            CONF_SCHEDULE_TIME: import_data.get(CONF_SCHEDULE_TIME, "09:00"),
            CONF_PROMPT_MESSAGES: [message],
            CONF_ACK_MESSAGES: DEFAULT_ACK_MESSAGES,
            CONF_DISMISS_MESSAGES: DEFAULT_DISMISS_MESSAGES,
        }
        if schedule_type == "once":
            config[CONF_ONCE_DATE] = import_data.get(CONF_ONCE_DATE)
        elif schedule_type == "weekly":
            config[CONF_SCHEDULE_DAYS] = import_data.get(CONF_SCHEDULE_DAYS, [])

        return self.async_create_entry(title=name, data=config)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        # Import here to avoid circular imports
        from .options_flow import ActionableRemindersHubOptionsFlow, ActionableRemindersReminderOptionsFlow
        
        # Return appropriate options flow based on entry type
        if config_entry.data.get("type") == CONF_TYPE_HUB:
            return ActionableRemindersHubOptionsFlow()
        else:
            return ActionableRemindersReminderOptionsFlow()


# Home Assistant expects "ConfigFlow" as the class name
ConfigFlow = ActionableRemindersConfigFlow