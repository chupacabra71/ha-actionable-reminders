"""Activity sensor — surfaces the audit journal as a Home Assistant entity.

State = a short summary of the most recent activity; attributes carry the
recent entries and rolling counts. Backed by the hub's ReminderJournal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_JOURNAL_UPDATED

_LOGGER = logging.getLogger(__name__)

RECENT_LIMIT = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the activity sensor on the hub."""
    if DOMAIN not in hass.data or "hub" not in hass.data[DOMAIN]:
        _LOGGER.error("Hub not found when setting up activity sensor")
        return
    async_add_entities([ReminderActivitySensor(hass)])
    _LOGGER.info("Reminders activity sensor created")


class ReminderActivitySensor(SensorEntity):
    """Exposes the audit journal: last activity + recent log + counts."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:history"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._attr_name = "Reminders Activity"
        self._attr_unique_id = f"{DOMAIN}_activity"

    @property
    def device_info(self) -> None:
        """Hub-level entity with no device (matches master switch / to-do)."""
        return None

    def _journal(self):
        """Return the hub journal, or None if unavailable."""
        hub = self.hass.data.get(DOMAIN, {}).get("hub")
        return hub.get("journal") if hub else None

    @property
    def native_value(self) -> str:
        """Short summary of the most recent activity."""
        journal = self._journal()
        entries = journal.entries if journal else []
        if not entries:
            return "No activity"
        last = entries[-1]
        return f"{last.get('name', '?')} · {last.get('action', '?')}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Recent entries + rolling counts."""
        journal = self._journal()
        entries = journal.entries if journal else []
        today = dt_util.now().date()

        def _done_since(days: int) -> int:
            cutoff = today - timedelta(days=days)
            n = 0
            for e in entries:
                if e.get("action") != "done":
                    continue
                d = self._entry_date(e)
                if d is not None and d >= cutoff:
                    n += 1
            return n

        last = entries[-1] if entries else {}
        return {
            "last_ts": last.get("ts"),
            "last_reminder": last.get("name"),
            "last_action": last.get("action"),
            "last_actor": last.get("actor"),
            "last_source": last.get("source"),
            "done_today": sum(
                1 for e in entries
                if e.get("action") == "done" and self._entry_date(e) == today
            ),
            "done_7d": _done_since(7),
            "total_logged": len(entries),
            # Newest first for display convenience.
            "recent": list(reversed(entries[-RECENT_LIMIT:])),
        }

    @staticmethod
    def _entry_date(entry: dict[str, Any]):
        """Parse an entry's ts to a local date, or None."""
        ts = entry.get("ts")
        if not ts:
            return None
        parsed = dt_util.parse_datetime(ts)
        return dt_util.as_local(parsed).date() if parsed else None

    async def async_added_to_hass(self) -> None:
        """Refresh on journal updates and at the day boundary (for done_today)."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_JOURNAL_UPDATED, self._handle_update
            )
        )
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=5
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_midnight(self, _now: datetime) -> None:
        self.async_write_ha_state()
