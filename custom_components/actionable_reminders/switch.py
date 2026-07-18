"""Switch platform for Actionable Reminders.

Creates switch entities for each configured reminder to allow easy
enable/disable control and status display in the UI.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SIGNAL_REMINDER_UPDATE,
    CONF_REMINDER_NAME,
    CONF_ENABLED,
    STATE_LAST_DONE,
    STATE_LAST_PROMPT,
    STATE_RETRIES_TODAY,
    STATE_ESCALATED,
    STATE_ESCALATIONS_TODAY,
    STATE_AUTO_SKIPPED,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities for a reminder.
    
    Args:
        hass: Home Assistant instance
        entry: Config entry for the reminder
        async_add_entities: Callback to add entities
    """
    # Get the reminder runner from hub registry
    if DOMAIN not in hass.data or "hub" not in hass.data[DOMAIN]:
        _LOGGER.error("Hub not found when setting up switch")
        return
    
    runner = hass.data[DOMAIN]["hub"]["reminders"].get(entry.entry_id)
    if not runner:
        _LOGGER.error("Reminder runner not found: %s", entry.entry_id)
        return
    
    # Create and add switch entity
    switch = ReminderSwitch(runner)
    async_add_entities([switch])
    
    _LOGGER.info("Switch entity created for reminder: %s", runner.name)


class ReminderSwitch(SwitchEntity):
    """Switch entity representing a reminder.
    
    The switch allows users to:
    - Enable/disable the reminder
    - See current status (done today, retries, escalated, etc.)
    - Monitor reminder activity
    """

    def __init__(self, runner) -> None:
        """Initialize the switch entity.
        
        Args:
            runner: ReminderRunner instance
        """
        self._runner = runner
        self._attr_unique_id = f"{DOMAIN}_{runner.entry_id}"
        self._attr_has_entity_name = False
        
        # Initialize attributes from runner
        self._update_from_runner()

    async def async_added_to_hass(self) -> None:
        """Handle entity added to Home Assistant."""
        # Listen for update signals from the runner
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_REMINDER_UPDATE.format(self._runner.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Handle update signal from runner."""
        self._update_from_runner()
        self.async_write_ha_state()

    def _update_from_runner(self) -> None:
        """Update entity attributes from runner state."""
        # Basic entity attributes
        self._attr_name = self._runner.name
        self._attr_is_on = self._runner.is_enabled
        
        # Get current state from runner
        state = self._runner.state_dict
        
        # Build extra state attributes for display
        self._attr_extra_state_attributes = {
            "entry_id": self._runner.entry_id,
            "schedule_type": self._runner.schedule_type,
            "schedule_time": self._runner.schedule_time,
            "schedule_days": self._runner.schedule_days if self._runner.schedule_type == "weekly" else None,
            "schedule_monthly_type": self._runner.schedule_monthly_type if self._runner.schedule_type == "monthly" else None,
            "schedule_monthly_day": self._runner.schedule_monthly_day if self._runner.schedule_type == "monthly" else None,
            "schedule_monthly_week": self._runner.schedule_monthly_week if self._runner.schedule_type == "monthly" else None,
            "schedule_monthly_weekday": self._runner.schedule_monthly_weekday if self._runner.schedule_type == "monthly" else None,
            "last_done_date": state.get(STATE_LAST_DONE),
            "last_prompt_ts": state.get(STATE_LAST_PROMPT),
            "retries_today": state.get(STATE_RETRIES_TODAY, 0),
            "escalated": state.get(STATE_ESCALATED, False),
            "escalations_today": state.get(STATE_ESCALATIONS_TODAY, 0),
            "auto_skipped": state.get(STATE_AUTO_SKIPPED, False),
            "optional": self._runner.optional,
            "actionable": self._runner.actionable,
            "retry_interval": self._runner.retry_interval,
            "max_retries": self._runner.max_retries,
            "escalation_interval": self._runner.escalation_interval,
            "max_escalations": self._runner.max_escalations,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the reminder (enable it)."""
        if not self._runner.is_enabled:
            _LOGGER.info("Enabling reminder: %s", self._runner.name)
            
            # Update config entry
            config = dict(self._runner._entry.data)
            config[CONF_ENABLED] = True
            
            # Trigger reconfiguration
            await self._runner.async_reconfigure(config)
            
            # Persist to config entry
            self.hass.config_entries.async_update_entry(
                self._runner._entry,
                data=config,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the reminder (disable it)."""
        if self._runner.is_enabled:
            _LOGGER.info("Disabling reminder: %s", self._runner.name)
            
            # Update config entry
            config = dict(self._runner._entry.data)
            config[CONF_ENABLED] = False
            
            # Trigger reconfiguration
            await self._runner.async_reconfigure(config)
            
            # Persist to config entry
            self.hass.config_entries.async_update_entry(
                self._runner._entry,
                data=config,
            )

    @property
    def icon(self) -> str:
        """Return the icon for this entity."""
        if self._attr_is_on:
            return "mdi:bell-check"
        return "mdi:bell-off"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information for grouping in UI."""
        return {
            "identifiers": {(DOMAIN, self._runner.entry_id)},
            "name": self._runner.name,
            "manufacturer": "Actionable Reminders",
            "model": "Reminder",
            "sw_version": "1.0.0",
        }