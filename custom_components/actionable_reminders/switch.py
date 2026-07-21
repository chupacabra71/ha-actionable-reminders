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
    CONF_TYPE_HUB,
    SUBENTRY_TYPE_REMINDER,
    CONF_MASTER_ENABLED,
    DEFAULT_MASTER_ENABLED,
    SIGNAL_REMINDER_UPDATE,
    SIGNAL_MASTER_UPDATED,
    CONF_REMINDER_NAME,
    CONF_ENABLED,
    STATE_LAST_DONE,
    STATE_LAST_PROMPT,
    STATE_RETRIES_TODAY,
    STATE_ESCALATED,
    STATE_ESCALATIONS_TODAY,
    STATE_AUTO_SKIPPED,
    STATE_SNOOZE_UNTIL,
    STATE_RESCHEDULE_DATE,
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
    # Switch platform is set up on the hub entry only. It creates the global
    # master switch plus one switch per reminder subentry.
    if DOMAIN not in hass.data or "hub" not in hass.data[DOMAIN]:
        _LOGGER.error("Hub not found when setting up switch")
        return

    async_add_entities([MasterSwitch(hass, entry)])
    _LOGGER.info("Master switch entity created")

    reminders = hass.data[DOMAIN]["hub"]["reminders"]
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_REMINDER:
            continue
        runner = reminders.get(subentry_id)
        if not runner:
            _LOGGER.error("Reminder runner not found for subentry %s", subentry_id)
            continue
        async_add_entities(
            [ReminderSwitch(runner)], config_subentry_id=subentry_id
        )
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
        # Keyed on the reminder's stable uid (the legacy entry_id for migrated
        # reminders) so switch.* entity_ids survive the move to subentries.
        self._attr_unique_id = f"{DOMAIN}_{runner.uid}"
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
            "next_due_date": (
                d.isoformat() if (d := self._runner.next_due_date) else None
            ),
            "last_done_date": state.get(STATE_LAST_DONE),
            "last_prompt_ts": state.get(STATE_LAST_PROMPT),
            "retries_today": state.get(STATE_RETRIES_TODAY, 0),
            "escalated": state.get(STATE_ESCALATED, False),
            "escalations_today": state.get(STATE_ESCALATIONS_TODAY, 0),
            "auto_skipped": state.get(STATE_AUTO_SKIPPED, False),
            "urgency": round(self._runner.urgency, 3),
            "status": self._runner.status,
            "summary": self._runner.summary,
            "mandatory": self._runner.mandatory,
            "snoozed_until": state.get(STATE_SNOOZE_UNTIL),
            "reschedule_date": state.get(STATE_RESCHEDULE_DATE),
            "optional": self._runner.optional,
            "actionable": self._runner.actionable,
            "retry_interval": self._runner.retry_interval,
            "max_retries": self._runner.max_retries,
            "escalation_interval": self._runner.escalation_interval,
            "max_escalations": self._runner.max_escalations,
        }
        # Condition-mode progress (accumulator/threshold) for dashboards + urgency.
        self._attr_extra_state_attributes.update(self._runner.condition_status())

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the reminder (enable it)."""
        if not self._runner.is_enabled:
            await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the reminder (disable it)."""
        if self._runner.is_enabled:
            await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        """Persist enabled state to the subentry and reload the hub to apply."""
        _LOGGER.info(
            "%s reminder: %s", "Enabling" if enabled else "Disabling", self._runner.name
        )
        runner = self._runner
        hub = runner._hub_entry
        subentry = runner._subentry
        # Updating the subentry fires the hub update-listener, which reloads
        # and rebuilds this reminder's runner with the new enabled state.
        self.hass.config_entries.async_update_subentry(
            hub, subentry, data={**subentry.data, CONF_ENABLED: enabled}
        )

    @property
    def icon(self) -> str:
        """Return the icon for this entity."""
        if self._attr_is_on:
            return "mdi:bell-check"
        return "mdi:bell-off"

    @property
    def device_info(self) -> dict[str, Any] | None:
        """No per-reminder device.

        Each reminder is already its own config subentry; giving it a device
        too made the integration page list every reminder twice (subentry row
        + device row). Returning None keeps one clean row per reminder, whose
        gear opens the reconfigure wizard.
        """
        return None


class MasterSwitch(SwitchEntity):
    """Global on/off switch for the whole integration.

    When off, no reminder or calendar-sourced prompt fires. The authoritative
    flag lives in hass.data[DOMAIN]["hub"]["master_enabled"] (read by every
    reminder tick and the calendar poll) and is persisted to the hub entry so
    it survives restarts.
    """

    _attr_has_entity_name = False
    _attr_name = "Actionable Reminders Master"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the master switch."""
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_master"

    async def async_added_to_hass(self) -> None:
        """Subscribe to master-state changes (e.g. from a restore)."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_MASTER_UPDATED, self.async_write_ha_state
            )
        )

    @property
    def is_on(self) -> bool:
        """Return whether reminders are globally enabled."""
        return bool(
            self.hass.data.get(DOMAIN, {})
            .get("hub", {})
            .get("master_enabled", DEFAULT_MASTER_ENABLED)
        )

    @property
    def icon(self) -> str:
        """Return the icon for this entity."""
        return "mdi:bell-ring" if self.is_on else "mdi:bell-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable all reminders."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Silence all reminders."""
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        """Update the master flag, persist it, and refresh the entity."""
        hub = self.hass.data.get(DOMAIN, {}).get("hub")
        if hub is not None:
            hub["master_enabled"] = enabled
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, CONF_MASTER_ENABLED: enabled},
        )
        _LOGGER.info("Master switch turned %s", "on" if enabled else "off")
        self.async_write_ha_state()

    @property
    def device_info(self) -> dict[str, Any] | None:
        """No hub device.

        The master switch and the aggregate to-do are hub-level entities that
        belong to no reminder subentry. Giving them a hub device made the
        integration page show a "Devices that don't belong to a sub-entry"
        section; matching the convention used by other subentry integrations
        (OpenAI, etc.), the parent entry carries no device. Both stay reachable
        from the entry's ⋮ → entities.
        """
        return None