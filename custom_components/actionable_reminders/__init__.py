"""Actionable Reminders integration bootstrap & hub management.

This module handles:
- Integration setup and configuration
- Hub entry management (global defaults)
- Reminder entry lifecycle (setup, update, teardown)
- Service registration
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_TYPE_HUB,
    CONF_TYPE_REMINDER,
    CONF_REMINDERS_CALENDAR,
    CONF_MASTER_ENABLED,
    DEFAULT_MASTER_ENABLED,
    SERVICE_MARK_DONE,
    SERVICE_DISMISS,
    SERVICE_SKIP_TODAY,
    SERVICE_FORCE_PROMPT,
    SERVICE_CALENDAR_ACK,
    SIGNAL_REMINDERS_UPDATED,
)
from .reminder import ReminderRunner
from .calendar_source import CalendarSource

_LOGGER = logging.getLogger(__name__)

# Platforms set up on a reminder entry (per-reminder switch) and on the hub
# entry (the aggregate to-do list), respectively.
PLATFORMS = [Platform.SWITCH]
HUB_PLATFORMS = [Platform.TODO, Platform.SWITCH]


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Setup
# ═══════════════════════════════════════════════════════════════════════════════

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Actionable Reminders integration (YAML not supported).
    
    Args:
        hass: Home Assistant instance
        config: Full Home Assistant configuration
        
    Returns:
        True (YAML configuration not used)
    """
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry (either hub or individual reminder).
    
    Args:
        hass: Home Assistant instance
        entry: The config entry to set up
        
    Returns:
        True if setup successful
    """
    entry_type = entry.data.get("type", CONF_TYPE_REMINDER)
    
    if entry_type == CONF_TYPE_HUB:
        # This is the hub entry - store global defaults and register services
        return await _setup_hub(hass, entry)
    else:
        # This is an individual reminder
        return await _setup_reminder(hass, entry)


async def _setup_hub(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hub entry (global defaults).
    
    Args:
        hass: Home Assistant instance
        entry: Hub config entry
        
    Returns:
        True if successful
    """
    _LOGGER.info("Setting up Actionable Reminders hub")
    
    # Initialize domain data structure
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["hub"] = {
        "entry": entry,
        "config": dict(entry.data),
        "reminders": {},  # Will store ReminderRunner instances keyed by entry_id
        # Master kill switch — when False, no reminder or calendar prompt fires.
        "master_enabled": entry.data.get(CONF_MASTER_ENABLED, DEFAULT_MASTER_ENABLED),
    }
    
    # Register integration services
    await _register_services(hass)
    
    # Setup update listener for hub config changes
    entry.async_on_unload(entry.add_update_listener(_hub_update_listener))

    # Calendar source (optional) — watches a calendar and drives reminders.
    hass.data[DOMAIN]["hub"]["calendar_source"] = None
    calendar_entity = entry.data.get(CONF_REMINDERS_CALENDAR)
    if calendar_entity:
        source = CalendarSource(hass, calendar_entity, dict(entry.data))
        hass.data[DOMAIN]["hub"]["calendar_source"] = source
        await source.async_start()

    # Set up hub-level platforms (the aggregate Reminders to-do list)
    await hass.config_entries.async_forward_entry_setups(entry, HUB_PLATFORMS)

    _LOGGER.info("Actionable Reminders hub setup complete")
    return True


async def _setup_reminder(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an individual reminder entry.
    
    Args:
        hass: Home Assistant instance
        entry: Reminder config entry
        
    Returns:
        True if successful, False if hub not found
    """
    _LOGGER.info("Setting up reminder: %s", entry.title)
    
    # Ensure hub exists
    if DOMAIN not in hass.data or "hub" not in hass.data[DOMAIN]:
        _LOGGER.error("Hub not found - please set up the integration first")
        return False
    
    # Get hub config for global defaults
    hub_config = hass.data[DOMAIN]["hub"]["config"]
    
    # Create reminder runner
    runner = ReminderRunner(hass, entry, hub_config)
    
    # Store runner in hub registry
    hass.data[DOMAIN]["hub"]["reminders"][entry.entry_id] = runner
    
    # Start the reminder (begins monitoring)
    await runner.async_start()
    
    # Setup switch platform (creates switch entity)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Refresh the aggregate to-do list (a reminder was added)
    async_dispatcher_send(hass, SIGNAL_REMINDERS_UPDATED)

    # Setup update listener for reminder config changes
    entry.async_on_unload(entry.add_update_listener(_reminder_update_listener))
    
    _LOGGER.info("Reminder setup complete: %s", entry.title)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Teardown
# ═══════════════════════════════════════════════════════════════════════════════

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.
    
    Args:
        hass: Home Assistant instance
        entry: Config entry to unload
        
    Returns:
        True if successful
    """
    entry_type = entry.data.get("type", CONF_TYPE_REMINDER)
    
    if entry_type == CONF_TYPE_HUB:
        # Unloading hub - stop all reminders first
        return await _unload_hub(hass, entry)
    else:
        # Unloading individual reminder
        return await _unload_reminder(hass, entry)


async def _unload_hub(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the hub entry.
    
    Args:
        hass: Home Assistant instance
        entry: Hub config entry
        
    Returns:
        True if successful
    """
    _LOGGER.info("Unloading Actionable Reminders hub")

    # Unload hub-level platforms (to-do list) before dropping hub data
    await hass.config_entries.async_unload_platforms(entry, HUB_PLATFORMS)

    # Stop the calendar source
    if DOMAIN in hass.data and "hub" in hass.data[DOMAIN]:
        source = hass.data[DOMAIN]["hub"].get("calendar_source")
        if source:
            await source.async_stop()

    # Stop all reminder runners
    if DOMAIN in hass.data and "hub" in hass.data[DOMAIN]:
        reminders = hass.data[DOMAIN]["hub"]["reminders"]
        for runner in reminders.values():
            await runner.async_stop()
    
    # Remove hub data structure
    hass.data.pop(DOMAIN, None)
    
    _LOGGER.info("Actionable Reminders hub unloaded")
    return True


async def _unload_reminder(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an individual reminder entry.
    
    Args:
        hass: Home Assistant instance
        entry: Reminder config entry
        
    Returns:
        True if successful
    """
    _LOGGER.info("Unloading reminder: %s", entry.title)
    
    # Stop and remove the reminder runner
    if DOMAIN in hass.data and "hub" in hass.data[DOMAIN]:
        runner = hass.data[DOMAIN]["hub"]["reminders"].pop(entry.entry_id, None)
        if runner:
            await runner.async_stop()
    
    # Unload switch platform
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Refresh the aggregate to-do list (a reminder was removed)
    if DOMAIN in hass.data and "hub" in hass.data[DOMAIN]:
        async_dispatcher_send(hass, SIGNAL_REMINDERS_UPDATED)

    _LOGGER.info("Reminder unloaded: %s", entry.title)
    return unload_ok


# ═══════════════════════════════════════════════════════════════════════════════
# Update Listeners
# ═══════════════════════════════════════════════════════════════════════════════

async def _hub_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle hub configuration updates from config flow.
    
    Args:
        hass: Home Assistant instance
        entry: Updated hub config entry
    """
    _LOGGER.info("Hub configuration updated")
    
    # Update stored hub config
    if DOMAIN in hass.data and "hub" in hass.data[DOMAIN]:
        hass.data[DOMAIN]["hub"]["config"] = dict(entry.data)
        
        # Notify all reminder runners of hub config change
        for runner in hass.data[DOMAIN]["hub"]["reminders"].values():
            await runner.async_update_hub_config(dict(entry.data))

        # Recreate the calendar source if the watched calendar changed.
        hub = hass.data[DOMAIN]["hub"]
        old = hub.get("calendar_source")
        old_cal = old.calendar_entity if old else None
        new_cal = entry.data.get(CONF_REMINDERS_CALENDAR)
        if new_cal != old_cal:
            if old:
                await old.async_stop()
            hub["calendar_source"] = None
            if new_cal:
                source = CalendarSource(hass, new_cal, dict(entry.data))
                hub["calendar_source"] = source
                await source.async_start()


async def _reminder_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle reminder configuration updates from config flow.
    
    Args:
        hass: Home Assistant instance
        entry: Updated reminder config entry
    """
    _LOGGER.info("Reminder configuration updated: %s", entry.title)
    
    # Get the runner and update its config
    if DOMAIN in hass.data and "hub" in hass.data[DOMAIN]:
        runner = hass.data[DOMAIN]["hub"]["reminders"].get(entry.entry_id)
        if runner:
            await runner.async_reconfigure(dict(entry.data))


# ═══════════════════════════════════════════════════════════════════════════════
# Services
# ═══════════════════════════════════════════════════════════════════════════════

async def _register_services(hass: HomeAssistant) -> None:
    """Register integration services.
    
    Args:
        hass: Home Assistant instance
    """
    
    async def handle_mark_done(call: ServiceCall) -> None:
        """Handle mark_done service call."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_mark_done()
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)
    
    async def handle_dismiss(call: ServiceCall) -> None:
        """Handle dismiss service call."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_dismiss()
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)
    
    async def handle_skip_today(call: ServiceCall) -> None:
        """Handle skip_today service call."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_skip_today()
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)
    
    async def handle_force_prompt(call: ServiceCall) -> None:
        """Handle force_prompt service call."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_force_prompt()
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)

    async def handle_calendar_ack(call: ServiceCall) -> None:
        """Ack a calendar-sourced reminder (from its Done button)."""
        event_key = call.data.get("event_key")
        hub = hass.data.get(DOMAIN, {}).get("hub", {})
        source = hub.get("calendar_source")
        if source and event_key:
            source.ack(event_key)

    # Register services with Home Assistant
    hass.services.async_register(
        DOMAIN,
        SERVICE_MARK_DONE,
        handle_mark_done,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
        }),
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISMISS,
        handle_dismiss,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
        }),
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_SKIP_TODAY,
        handle_skip_today,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
        }),
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_PROMPT,
        handle_force_prompt,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
        }),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CALENDAR_ACK,
        handle_calendar_ack,
        schema=vol.Schema({
            vol.Required("event_key"): cv.string,
        }),
    )

    _LOGGER.info("Services registered: %s, %s, %s, %s, %s",
                 SERVICE_MARK_DONE, SERVICE_DISMISS, SERVICE_SKIP_TODAY,
                 SERVICE_FORCE_PROMPT, SERVICE_CALENDAR_ACK)


def _get_runner_by_id(hass: HomeAssistant, entry_id: str) -> ReminderRunner | None:
    """Get a reminder runner by its entry_id.
    
    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID to look up
        
    Returns:
        ReminderRunner instance or None if not found
    """
    if DOMAIN not in hass.data or "hub" not in hass.data[DOMAIN]:
        return None
    
    return hass.data[DOMAIN]["hub"]["reminders"].get(entry_id)