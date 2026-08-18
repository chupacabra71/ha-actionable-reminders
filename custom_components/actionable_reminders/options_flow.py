"""Options flow for Actionable Reminders — hub defaults only.

Individual reminders are subentries and are edited through
subentry_flow.ReminderSubentryFlow. A parallel per-reminder options flow lived
here until the subentry rewire deleted the branch of async_get_options_flow
that reached it; it was removed once it had been unreachable for that long.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DEFAULT_RETRY_INTERVAL,
    CONF_DEFAULT_MAX_RETRIES,
    CONF_DEFAULT_ESCALATION_INTERVAL,
    CONF_DEFAULT_RESPONSE_WINDOW,
    CONF_DEFAULT_NAG_MIN_GAP,
    CONF_DEFAULT_NAG_MAX_GAP,
    CONF_DEFAULT_NAG_FRACTION,
    CONF_DEFAULT_MAX_ESCALATIONS,
    CONF_EARLIEST_RETRY_TIME,
    CONF_DEFAULT_MOBILE_SERVICE,
    CONF_CLEAR_NOTIFICATION_SERVICE,
    CONF_DEFAULT_ALEXA_DEVICES,
    CONF_REMINDERS_CALENDAR,
    CONF_DEFAULT_ACTIONABLE,
    CONF_DEFAULT_PRESENCE_SENSORS,
    CONF_VOICE_RESPONDERS,
    CONF_DEFAULT_QUIET_START,
    CONF_DEFAULT_QUIET_END,
    DEFAULT_RETRY_INTERVAL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_ESCALATION_INTERVAL,
    DEFAULT_RESPONSE_WINDOW,
    DEFAULT_NAG_MIN_GAP,
    DEFAULT_NAG_MAX_GAP,
    DEFAULT_NAG_FRACTION,
    DEFAULT_MAX_ESCALATIONS,
    DEFAULT_EARLIEST_RETRY_TIME,
    DEFAULT_ACTIONABLE,
    DEFAULT_QUIET_START,
    DEFAULT_QUIET_END,
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

    # No __init__: `config_entry` is a framework-provided property on OptionsFlow
    # in modern HA — assigning it raises. HA binds it from async_get_options_flow.

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

            # How long the actionable response window stays open (mobile buttons
            # live; voice is Amazon-capped shorter). Per-reminder override lives
            # in the reminder wizard.
            vol.Required(
                CONF_DEFAULT_RESPONSE_WINDOW,
                default=current_data.get(CONF_DEFAULT_RESPONSE_WINDOW, DEFAULT_RESPONSE_WINDOW)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),

            # Adaptive-nag timing: gap floor/ceiling (minutes) and the fraction of
            # time-until-quiet each nag targets (nags compress toward the cutoff).
            vol.Required(
                CONF_DEFAULT_NAG_MIN_GAP,
                default=current_data.get(CONF_DEFAULT_NAG_MIN_GAP, DEFAULT_NAG_MIN_GAP)
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),

            vol.Required(
                CONF_DEFAULT_NAG_MAX_GAP,
                default=current_data.get(CONF_DEFAULT_NAG_MAX_GAP, DEFAULT_NAG_MAX_GAP)
            ): vol.All(vol.Coerce(int), vol.Range(min=15, max=240)),

            vol.Required(
                CONF_DEFAULT_NAG_FRACTION,
                default=current_data.get(CONF_DEFAULT_NAG_FRACTION, DEFAULT_NAG_FRACTION)
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=0.9)),

            # Voice-ID responder map: one 'amzn1.ask.person.XXXX = Name' per line.
            vol.Optional(
                CONF_VOICE_RESPONDERS,
                description={"suggested_value": current_data.get(CONF_VOICE_RESPONDERS)},
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            
            vol.Required(
                CONF_EARLIEST_RETRY_TIME,
                default=current_data.get(CONF_EARLIEST_RETRY_TIME, DEFAULT_EARLIEST_RETRY_TIME)
            ): selector.TimeSelector(),
            
            # Notification defaults
            vol.Optional(
                CONF_DEFAULT_MOBILE_SERVICE,
                description={"suggested_value": current_data.get(CONF_DEFAULT_MOBILE_SERVICE)},
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ) if notify_services else str,

            vol.Optional(
                CONF_CLEAR_NOTIFICATION_SERVICE,
                description={"suggested_value": current_data.get(CONF_CLEAR_NOTIFICATION_SERVICE)},
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

            # Calendar source: every event on this calendar becomes a reminder
            vol.Optional(
                CONF_REMINDERS_CALENDAR,
                description={"suggested_value": current_data.get(CONF_REMINDERS_CALENDAR)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar")
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

