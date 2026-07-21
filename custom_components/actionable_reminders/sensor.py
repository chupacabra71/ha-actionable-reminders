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
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_JOURNAL_UPDATED, SIGNAL_REMINDERS_UPDATED

_LOGGER = logging.getLogger(__name__)

RECENT_LIMIT = 25
# Statuses that count as "needs attention" for the next-up surface.
ACTIONABLE_STATUSES = ("triggered", "overdue", "due_soon")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the activity sensor on the hub."""
    if DOMAIN not in hass.data or "hub" not in hass.data[DOMAIN]:
        _LOGGER.error("Hub not found when setting up activity sensor")
        return
    async_add_entities([ReminderActivitySensor(hass), ReminderNextUpSensor(hass)])
    _LOGGER.info("Reminders activity + next-up sensors created")


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


class ReminderNextUpSensor(SensorEntity):
    """The single highest-urgency reminder needing attention (§16.2 surface).

    State = that reminder's name (or "All clear"); attributes carry its
    entry_id (for one-tap actions) and the full urgency-ranked pending list.
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:alert-decagram"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._attr_name = "Reminders Next Up"
        self._attr_unique_id = f"{DOMAIN}_next_up"

    @property
    def device_info(self) -> None:
        """Hub-level entity with no device."""
        return None

    def _pending(self) -> list[dict[str, Any]]:
        """Actionable reminders, most-urgent first."""
        hub = self.hass.data.get(DOMAIN, {}).get("hub")
        runners = hub.get("reminders", {}).values() if hub else []
        out = []
        for r in runners:
            try:
                if r.is_snoozed or r.status not in ACTIONABLE_STATUSES:
                    continue
                out.append({
                    "name": r.name,
                    "entry_id": r.entry_id,
                    "status": r.status,
                    "urgency": round(r.urgency, 3),
                    "summary": r.summary,
                })
            except Exception:  # noqa: BLE001 - never let one reminder break the surface
                continue
        out.sort(key=lambda e: e["urgency"], reverse=True)
        return out

    @property
    def native_value(self) -> str:
        pending = self._pending()
        return pending[0]["name"] if pending else "All clear"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pending = self._pending()
        top = pending[0] if pending else {}
        return {
            "entry_id": top.get("entry_id"),
            "status": top.get("status"),
            "urgency": top.get("urgency"),
            "summary": top.get("summary"),
            "count": len(pending),
            "pending": pending,
        }

    async def async_added_to_hass(self) -> None:
        """Recompute on reminder changes and on a periodic tick (time-driven urgency)."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_REMINDERS_UPDATED, self._handle_update
            )
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._handle_tick, timedelta(minutes=5))
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_tick(self, _now: datetime) -> None:
        self.async_write_ha_state()
