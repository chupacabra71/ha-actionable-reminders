"""Options flow for Actionable Reminders integration.

This module handles the options flows for:
- Hub options (editing global defaults)
- Reminder options (editing individual reminder settings)
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_TYPE_HUB,
    CONF_REMINDER_NAME,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_TIME,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_MONTHLY_TYPE,
    CONF_SCHEDULE_MONTHLY_DAY,
    CONF_SCHEDULE_MONTHLY_WEEK,
    CONF_SCHEDULE_MONTHLY_WEEKDAY,
    CONF_PROMPT_MESSAGES,
    CONF_ACK_MESSAGES,
    CONF_DISMISS_MESSAGES,
    CONF_MOBILE_SERVICE,
    CONF_ALEXA_DEVICES,
    CONF_ACTIONABLE,
    CONF_ESCALATION_VOLUME,
    CONF_RETRY_INTERVAL,
    CONF_MAX_RETRIES,
    CONF_ESCALATION_INTERVAL,
    CONF_MAX_ESCALATIONS,
    CONF_PRESENCE_SENSORS,
    CONF_CATCHUP_ON_ARRIVAL,
    CONF_QUIET_START,
    CONF_QUIET_END,
    CONF_OPTIONAL,
    CONF_UNTIL_DONE,
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
    DEFAULT_ESCALATION_VOLUME,
    DEFAULT_CATCHUP_ON_ARRIVAL,
    DEFAULT_QUIET_START,
    DEFAULT_QUIET_END,
    DEFAULT_OPTIONAL,
    DEFAULT_UNTIL_DONE,
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


# ═══════════════════════════════════════════════════════════════════════════════
# Hub Options Flow
# ═══════════════════════════════════════════════════════════════════════════════

class ActionableRemindersHubOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for hub (global defaults)."""

    def __init__(self, config_entry):
        """Initialize hub options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage hub options."""
        if user_input is not None:
            # Update hub config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data
        notify_services = _get_notify_services(self.hass)
        alexa_devices = _get_alexa_devices(self.hass)
        presence_sensors = _get_presence_sensors(self.hass)

        data_schema = vol.Schema({
            # Retry and escalation defaults
            vol.Required(
                CONF_DEFAULT_RETRY_INTERVAL,
                default=current_data.get(CONF_DEFAULT_RETRY_INTERVAL, DEFAULT_RETRY_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            
            vol.Required(
                CONF_DEFAULT_MAX_RETRIES,
                default=current_data.get(CONF_DEFAULT_MAX_RETRIES, DEFAULT_MAX_RETRIES)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
            
            vol.Required(
                CONF_DEFAULT_ESCALATION_INTERVAL,
                default=current_data.get(CONF_DEFAULT_ESCALATION_INTERVAL, DEFAULT_ESCALATION_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            
            vol.Required(
                CONF_DEFAULT_MAX_ESCALATIONS,
                default=current_data.get(CONF_DEFAULT_MAX_ESCALATIONS, DEFAULT_MAX_ESCALATIONS)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
            
            vol.Required(
                CONF_EARLIEST_RETRY_TIME,
                default=current_data.get(CONF_EARLIEST_RETRY_TIME, DEFAULT_EARLIEST_RETRY_TIME)
            ): selector.TimeSelector(),
            
            # Notification defaults
            vol.Optional(
                CONF_DEFAULT_MOBILE_SERVICE,
                default=current_data.get(CONF_DEFAULT_MOBILE_SERVICE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ) if notify_services else str,
            
            vol.Optional(
                CONF_DEFAULT_ALEXA_DEVICES,
                default=current_data.get(CONF_DEFAULT_ALEXA_DEVICES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="media_player",
                    multiple=True,
                )
            ),
            
            vol.Required(
                CONF_DEFAULT_ACTIONABLE,
                default=current_data.get(CONF_DEFAULT_ACTIONABLE, DEFAULT_ACTIONABLE)
            ): bool,
            
            # Presence defaults
            vol.Optional(
                CONF_DEFAULT_PRESENCE_SENSORS,
                default=current_data.get(CONF_DEFAULT_PRESENCE_SENSORS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor",
                    device_class="presence",
                    multiple=True,
                )
            ),
            
            # Quiet hours defaults
            vol.Required(
                CONF_DEFAULT_QUIET_START,
                default=current_data.get(CONF_DEFAULT_QUIET_START, DEFAULT_QUIET_START)
            ): selector.TimeSelector(),
            
            vol.Required(
                CONF_DEFAULT_QUIET_END,
                default=current_data.get(CONF_DEFAULT_QUIET_END, DEFAULT_QUIET_END)
            ): selector.TimeSelector(),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            description_placeholders={
                "info": "These are the default settings that will be used for all new reminders. Individual reminders can override these settings."
            }
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Reminder Options Flow
# ═══════════════════════════════════════════════════════════════════════════════

class ActionableRemindersReminderOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for individual reminders."""

    def __init__(self, config_entry):
        """Initialize reminder options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show menu of options to configure."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "edit_schedule",
                "edit_messages",
                "edit_notifications",
                "edit_retry",
                "edit_presence_quiet",
                "edit_behavior",
            ]
        )

    # ────────────────────────────────────────────────────────────────────────────
    # Edit Schedule
    # ────────────────────────────────────────────────────────────────────────────

    async def async_step_edit_schedule(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit schedule settings."""
        if user_input is not None:
            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data
        schedule_type = current_data.get(CONF_SCHEDULE_TYPE, "daily")

        # Build schema based on schedule type
        if schedule_type == "daily":
            data_schema = vol.Schema({
                vol.Required(
                    CONF_SCHEDULE_TIME,
                    default=current_data.get(CONF_SCHEDULE_TIME, "09:00")
                ): selector.TimeSelector(),
            })
            
        elif schedule_type == "weekly":
            data_schema = vol.Schema({
                vol.Required(
                    CONF_SCHEDULE_TIME,
                    default=current_data.get(CONF_SCHEDULE_TIME, "09:00")
                ): selector.TimeSelector(),
                vol.Required(
                    CONF_SCHEDULE_DAYS,
                    default=current_data.get(CONF_SCHEDULE_DAYS, [])
                ): selector.SelectSelector(
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
            
        else:  # monthly
            monthly_type = current_data.get(CONF_SCHEDULE_MONTHLY_TYPE, "day")
            
            if monthly_type == "day":
                data_schema = vol.Schema({
                    vol.Required(
                        CONF_SCHEDULE_TIME,
                        default=current_data.get(CONF_SCHEDULE_TIME, "09:00")
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_SCHEDULE_MONTHLY_DAY,
                        default=current_data.get(CONF_SCHEDULE_MONTHLY_DAY, 1)
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=31,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                })
            else:  # week_pattern
                data_schema = vol.Schema({
                    vol.Required(
                        CONF_SCHEDULE_TIME,
                        default=current_data.get(CONF_SCHEDULE_TIME, "09:00")
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_SCHEDULE_MONTHLY_WEEK,
                        default=current_data.get(CONF_SCHEDULE_MONTHLY_WEEK, "first")
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"label": label, "value": week}
                                for week, label in MONTHLY_WEEK_LABELS.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_SCHEDULE_MONTHLY_WEEKDAY,
                        default=current_data.get(CONF_SCHEDULE_MONTHLY_WEEKDAY, "mon")
                    ): selector.SelectSelector(
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
            step_id="edit_schedule",
            data_schema=data_schema,
            description_placeholders={
                "schedule_type": schedule_type.title(),
            }
        )

    # ────────────────────────────────────────────────────────────────────────────
    # Edit Messages
    # ────────────────────────────────────────────────────────────────────────────

    async def async_step_edit_messages(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit messages (prompt, ack, dismiss)."""
        if user_input is not None:
            # Parse messages (newline-separated strings to lists)
            processed = {}
            
            # Prompt messages
            prompt_text = user_input.get("prompt_messages_text", "")
            processed[CONF_PROMPT_MESSAGES] = [
                line.strip() for line in prompt_text.split("\n") if line.strip()
            ]
            
            # Ack messages
            ack_text = user_input.get("ack_messages_text", "")
            processed[CONF_ACK_MESSAGES] = [
                line.strip() for line in ack_text.split("\n") if line.strip()
            ] or DEFAULT_ACK_MESSAGES
            
            # Dismiss messages
            dismiss_text = user_input.get("dismiss_messages_text", "")
            processed[CONF_DISMISS_MESSAGES] = [
                line.strip() for line in dismiss_text.split("\n") if line.strip()
            ] or DEFAULT_DISMISS_MESSAGES
            
            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **processed}
            )
            return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data
        
        # Convert message lists to newline-separated text
        prompt_text = "\n".join(current_data.get(CONF_PROMPT_MESSAGES, []))
        ack_text = "\n".join(current_data.get(CONF_ACK_MESSAGES, DEFAULT_ACK_MESSAGES))
        dismiss_text = "\n".join(current_data.get(CONF_DISMISS_MESSAGES, DEFAULT_DISMISS_MESSAGES))
        
        data_schema = vol.Schema({
            vol.Required(
                "prompt_messages_text",
                default=prompt_text
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Required(
                "ack_messages_text",
                default=ack_text
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Required(
                "dismiss_messages_text",
                default=dismiss_text
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
        })

        return self.async_show_form(
            step_id="edit_messages",
            data_schema=data_schema,
            description_placeholders={
                "info": "Enter one message per line. A random message will be chosen each time."
            }
        )

    # ────────────────────────────────────────────────────────────────────────────
    # Edit Notifications
    # ────────────────────────────────────────────────────────────────────────────

    async def async_step_edit_notifications(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit notification settings."""
        if user_input is not None:
            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data
        notify_services = _get_notify_services(self.hass)
        
        data_schema = vol.Schema({
            vol.Optional(
                CONF_MOBILE_SERVICE,
                default=current_data.get(CONF_MOBILE_SERVICE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ) if notify_services else str,
            
            vol.Optional(
                CONF_ALEXA_DEVICES,
                default=current_data.get(CONF_ALEXA_DEVICES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="media_player",
                    multiple=True,
                )
            ),
            
            vol.Required(
                CONF_ACTIONABLE,
                default=current_data.get(CONF_ACTIONABLE, DEFAULT_ACTIONABLE)
            ): bool,
            
            vol.Required(
                CONF_ESCALATION_VOLUME,
                default=current_data.get(CONF_ESCALATION_VOLUME, DEFAULT_ESCALATION_VOLUME)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0,
                    max=1.0,
                    step=0.1,
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
        })

        return self.async_show_form(
            step_id="edit_notifications",
            data_schema=data_schema,
            description_placeholders={
                "info": "Leave mobile service or Alexa devices empty to use hub defaults."
            }
        )

    # ────────────────────────────────────────────────────────────────────────────
    # Edit Retry & Escalation
    # ────────────────────────────────────────────────────────────────────────────

    async def async_step_edit_retry(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit retry and escalation settings."""
        if user_input is not None:
            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data
        
        data_schema = vol.Schema({
            vol.Optional(
                CONF_RETRY_INTERVAL,
                default=current_data.get(CONF_RETRY_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            
            vol.Optional(
                CONF_MAX_RETRIES,
                default=current_data.get(CONF_MAX_RETRIES)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
            
            vol.Optional(
                CONF_ESCALATION_INTERVAL,
                default=current_data.get(CONF_ESCALATION_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            
            vol.Optional(
                CONF_MAX_ESCALATIONS,
                default=current_data.get(CONF_MAX_ESCALATIONS)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
        })

        return self.async_show_form(
            step_id="edit_retry",
            data_schema=data_schema,
            description_placeholders={
                "info": "Leave empty to use hub defaults. After max retries, enters escalation mode (louder, rotating devices). After max escalations, auto-skips for the day."
            }
        )

    # ────────────────────────────────────────────────────────────────────────────
    # Edit Presence & Quiet Hours
    # ────────────────────────────────────────────────────────────────────────────

    async def async_step_edit_presence_quiet(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit presence and quiet hours settings."""
        if user_input is not None:
            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data
        
        data_schema = vol.Schema({
            vol.Optional(
                CONF_PRESENCE_SENSORS,
                default=current_data.get(CONF_PRESENCE_SENSORS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor",
                    device_class="presence",
                    multiple=True,
                )
            ),
            
            vol.Required(
                CONF_CATCHUP_ON_ARRIVAL,
                default=current_data.get(CONF_CATCHUP_ON_ARRIVAL, DEFAULT_CATCHUP_ON_ARRIVAL)
            ): bool,
            
            vol.Optional(
                CONF_QUIET_START,
                default=current_data.get(CONF_QUIET_START)
            ): selector.TimeSelector(),
            
            vol.Optional(
                CONF_QUIET_END,
                default=current_data.get(CONF_QUIET_END)
            ): selector.TimeSelector(),
        })

        return self.async_show_form(
            step_id="edit_presence_quiet",
            data_schema=data_schema,
            description_placeholders={
                "info": "Leave presence sensors empty to use hub defaults. Leave quiet hours empty to use hub defaults."
            }
        )

    # ────────────────────────────────────────────────────────────────────────────
    # Edit Behavior
    # ────────────────────────────────────────────────────────────────────────────

    async def async_step_edit_behavior(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit behavior flags."""
        if user_input is not None:
            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})

        current_data = self.config_entry.data
        
        data_schema = vol.Schema({
            vol.Required(
                CONF_OPTIONAL,
                default=current_data.get(CONF_OPTIONAL, DEFAULT_OPTIONAL)
            ): bool,
            
            vol.Required(
                CONF_UNTIL_DONE,
                default=current_data.get(CONF_UNTIL_DONE, DEFAULT_UNTIL_DONE)
            ): bool,
        })

        return self.async_show_form(
            step_id="edit_behavior",
            data_schema=data_schema,
            description_placeholders={
                "optional": "Optional reminders can be skipped/dismissed without consequence.",
                "until_done": "Keeps prompting daily until marked as done."
            }
        )