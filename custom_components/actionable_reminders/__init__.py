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

from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_TYPE_HUB,
    CONF_TYPE_REMINDER,
    SUBENTRY_TYPE_REMINDER,
    CONF_REMINDERS_CALENDAR,
    CONF_MASTER_ENABLED,
    DEFAULT_MASTER_ENABLED,
    SERVICE_MARK_DONE,
    SERVICE_DISMISS,
    SERVICE_SKIP_TODAY,
    SERVICE_FORCE_PROMPT,
    SERVICE_SET_ACCUM_BASELINE,
    SERVICE_SNOOZE,
    SERVICE_RESCHEDULE,
    SERVICE_CALENDAR_ACK,
    SIGNAL_REMINDERS_UPDATED,
)
from .reminder import ReminderRunner
from .calendar_source import CalendarSource
from .journal import ReminderJournal

_LOGGER = logging.getLogger(__name__)

# Platforms set up on a reminder entry (per-reminder switch) and on the hub
# entry (the aggregate to-do list), respectively.
PLATFORMS = [Platform.SWITCH]
HUB_PLATFORMS = [Platform.TODO, Platform.SWITCH, Platform.SENSOR]


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
        # The hub owns everything: global defaults, services, and every
        # reminder (now a subentry of the hub rather than its own entry).
        return await _setup_hub(hass, entry)

    # Legacy standalone reminder entry (pre-subentries). Do nothing here — the
    # hub setup migrates these into subentries and removes them.
    _LOGGER.debug("Legacy reminder entry %s awaiting migration", entry.title)
    return True


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

    # Audit journal — a persisted log of every completion-path action.
    journal = ReminderJournal(hass)
    await journal.async_load()
    hass.data[DOMAIN]["hub"]["journal"] = journal

    # Register integration services
    await _register_services(hass)

    # One-time: fold any legacy standalone reminder entries into subentries.
    # Runs BEFORE the update listener is registered on purpose: every
    # async_add_subentry notifies listeners, so migrating N reminders with the
    # listener already attached schedules N redundant hub reloads. Migrating
    # first means one clean setup.
    await _migrate_legacy_reminders(hass, entry)

    # Setup update listener for hub config changes (and subentry add/edit/remove)
    entry.async_on_unload(entry.add_update_listener(_hub_update_listener))

    # Calendar source (optional) — watches a calendar and drives reminders.
    hass.data[DOMAIN]["hub"]["calendar_source"] = None
    calendar_entity = entry.data.get(CONF_REMINDERS_CALENDAR)
    if calendar_entity:
        source = CalendarSource(hass, calendar_entity, dict(entry.data))
        hass.data[DOMAIN]["hub"]["calendar_source"] = source
        await source.async_start()

    # Create one runner per reminder subentry.
    hub_config = hass.data[DOMAIN]["hub"]["config"]
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_REMINDER:
            continue
        runner = ReminderRunner(hass, entry, subentry, hub_config)
        hass.data[DOMAIN]["hub"]["reminders"][subentry.subentry_id] = runner
        await runner.async_start()

    # Set up hub platforms: the aggregate to-do list + switches (master +
    # one per reminder subentry).
    await hass.config_entries.async_forward_entry_setups(entry, HUB_PLATFORMS)

    _prune_orphan_devices(hass, entry)

    _LOGGER.info(
        "Actionable Reminders hub setup complete (%d reminders)",
        len(hass.data[DOMAIN]["hub"]["reminders"]),
    )
    return True


def _prune_orphan_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove hub devices that no longer own any entity.

    Reminders dropped their per-reminder device in v0.9.2; this clears the
    devices the migration left behind (and self-heals any future
    device→no-device change). The hub's own device keeps the master switch and
    the todo list, so it survives.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not er.async_entries_for_device(
            ent_reg, device.id, include_disabled_entities=True
        ):
            _LOGGER.info("Removing orphaned reminder device: %s", device.name)
            dev_reg.async_remove_device(device.id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry
) -> bool:
    """Allow deleting a reminder device from the UI when it owns no entities.

    Guards the hub device (master switch + todo) from removal while it still
    has entities.
    """
    ent_reg = er.async_get(hass)
    return not er.async_entries_for_device(
        ent_reg, device_entry.id, include_disabled_entities=True
    )


async def _migrate_legacy_reminders(hass: HomeAssistant, hub_entry: ConfigEntry) -> None:
    """Convert legacy standalone reminder config entries into hub subentries.

    Each reminder's legacy entry_id is carried as the subentry's unique_id, so
    the entity registry (keyed on unique_id, not the owning entry) keeps every
    switch.* entity_id and its history — dashboards/automations are unaffected.
    Runtime state persists too: the per-reminder Store is keyed by that same id.
    """
    legacy = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != hub_entry.entry_id
        and e.data.get("type", CONF_TYPE_REMINDER) != CONF_TYPE_HUB
    ]
    if not legacy:
        return

    _LOGGER.warning("Migrating %d legacy reminder(s) to subentries", len(legacy))
    for e in legacy:
        data = {k: v for k, v in e.data.items() if k != "type"}
        hass.config_entries.async_add_subentry(
            hub_entry,
            ConfigSubentry(
                data=MappingProxyType(data),
                subentry_type=SUBENTRY_TYPE_REMINDER,
                title=e.title,
                unique_id=e.entry_id,
            ),
        )
        _LOGGER.info("Migrated reminder '%s' -> subentry", e.title)

    # Remove the legacy entries now that their subentries exist. async_remove_entry
    # is guarded to NOT delete the state Store these subentries inherited.
    for e in legacy:
        await hass.config_entries.async_remove(e.entry_id)


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
        return await _unload_hub(hass, entry)

    # Legacy standalone reminder entry — the hub owns all reminders now, so
    # nothing was set up for it and there's nothing to unload.
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete a removed reminder's runtime-state Store file.

    Skip deletion when a migrated subentry now owns that state (the legacy
    entry_id is carried as the subentry's unique_id) — otherwise migration
    would wipe the state it's meant to inherit.
    """
    if entry.data.get("type", CONF_TYPE_REMINDER) == CONF_TYPE_HUB:
        return
    hub = next(
        (e for e in hass.config_entries.async_entries(DOMAIN)
         if e.data.get("type") == CONF_TYPE_HUB),
        None,
    )
    if hub and any(s.unique_id == entry.entry_id for s in hub.subentries.values()):
        return
    await Store(hass, 1, f"{DOMAIN}_state_{entry.entry_id}").async_remove()


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


# ═══════════════════════════════════════════════════════════════════════════════
# Update Listeners
# ═══════════════════════════════════════════════════════════════════════════════

async def _hub_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the hub on any change.

    Fires when hub options are edited OR a reminder subentry is added, edited,
    or removed (all subentry mutators notify update listeners). A reload
    rebuilds runners, switch entities, and the calendar source from the current
    configuration — the single reconcile path covering add, edit, and remove.

    Uses async_schedule_reload rather than awaiting async_reload: the listener
    already runs as its own task, and scheduling avoids reloading the entry
    from inside its own update notification.
    """
    hass.config_entries.async_schedule_reload(entry.entry_id)


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
            await runner.async_mark_done(context=call.context, source=call.data.get("source"))
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)

    async def handle_dismiss(call: ServiceCall) -> None:
        """Handle dismiss service call."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_dismiss(context=call.context, source=call.data.get("source"))
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)

    async def handle_skip_today(call: ServiceCall) -> None:
        """Handle skip_today service call."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_skip_today(context=call.context, source=call.data.get("source"))
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

    async def handle_set_accumulator_baseline(call: ServiceCall) -> None:
        """Set an accumulator reminder's baseline directly."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_set_accumulator_baseline(call.data.get("baseline"))
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)

    async def handle_snooze(call: ServiceCall) -> None:
        """Defer a reminder by a duration."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_snooze(
                call.data["duration"], context=call.context, source=call.data.get("source")
            )
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)

    async def handle_reschedule(call: ServiceCall) -> None:
        """Move a scheduled reminder's next due date."""
        entry_id = call.data.get("entry_id")
        runner = _get_runner_by_id(hass, entry_id)
        if runner:
            await runner.async_reschedule_next(
                str(call.data["date"]), context=call.context, source=call.data.get("source")
            )
        else:
            _LOGGER.error("Reminder not found: %s", entry_id)

    async def handle_calendar_ack(call: ServiceCall) -> None:
        """Ack a calendar-sourced reminder (from its Done button)."""
        event_key = call.data.get("event_key")
        hub = hass.data.get(DOMAIN, {}).get("hub", {})
        source = hub.get("calendar_source")
        if source and event_key:
            await source.ack(event_key)

    # Register services with Home Assistant
    hass.services.async_register(
        DOMAIN,
        SERVICE_MARK_DONE,
        handle_mark_done,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
            vol.Optional("source"): cv.string,
        }),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DISMISS,
        handle_dismiss,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
            vol.Optional("source"): cv.string,
        }),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SKIP_TODAY,
        handle_skip_today,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
            vol.Optional("source"): cv.string,
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
        SERVICE_SET_ACCUM_BASELINE,
        handle_set_accumulator_baseline,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
            vol.Required("baseline"): vol.Coerce(float),
        }),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SNOOZE,
        handle_snooze,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
            vol.Required("duration"): cv.time_period,
            vol.Optional("source"): cv.string,
        }),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESCHEDULE,
        handle_reschedule,
        schema=vol.Schema({
            vol.Required("entry_id"): cv.string,
            vol.Required("date"): cv.date,
            vol.Optional("source"): cv.string,
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

    _LOGGER.info(
        "Services registered: %s",
        ", ".join([
            SERVICE_MARK_DONE, SERVICE_DISMISS, SERVICE_SKIP_TODAY,
            SERVICE_FORCE_PROMPT, SERVICE_SET_ACCUM_BASELINE,
            SERVICE_SNOOZE, SERVICE_RESCHEDULE, SERVICE_CALENDAR_ACK,
        ]),
    )


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