"""Audit journal — a persisted, bounded log of reminder activity.

Records every completion-path action (done / skip / dismiss / auto_skip) with a
timestamp, the reminder, the actor (the HA user who triggered it, when known),
and a free-text source. Exposed via the activity sensor and the
``actionable_reminders_journal`` event; cheap to add, and the foundation for
fairness/assignment later.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_JOURNAL,
    JOURNAL_MAX_ENTRIES,
    JOURNAL_STORAGE_KEY,
    JOURNAL_STORAGE_VERSION,
    SIGNAL_JOURNAL_UPDATED,
)

_LOGGER = logging.getLogger(__name__)


class ReminderJournal:
    """A persisted ring buffer of reminder activity, newest last."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the journal."""
        self.hass = hass
        self._store: Store = Store(hass, JOURNAL_STORAGE_VERSION, JOURNAL_STORAGE_KEY)
        self._entries: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        """Load persisted entries."""
        stored = await self._store.async_load()
        if isinstance(stored, list):
            self._entries = stored[-JOURNAL_MAX_ENTRIES:]
        elif isinstance(stored, dict) and isinstance(stored.get("entries"), list):
            self._entries = stored["entries"][-JOURNAL_MAX_ENTRIES:]

    @property
    def entries(self) -> list[dict[str, Any]]:
        """Return a copy of the entries, newest last."""
        return list(self._entries)

    async def _resolve_actor(self, context: Context | None) -> str | None:
        """Best-effort map a call context to the triggering user's name."""
        if context is None or not context.user_id:
            return None
        try:
            user = await self.hass.auth.async_get_user(context.user_id)
        except Exception:  # noqa: BLE001 - actor resolution is best-effort only
            return None
        return user.name if user else None

    async def record(
        self,
        *,
        uid: str,
        name: str,
        action: str,
        context: Context | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Append an entry, persist, fire the event, and notify listeners."""
        entry = {
            "ts": dt_util.now().isoformat(),
            "uid": uid,
            "name": name,
            "action": action,
            "actor": await self._resolve_actor(context),
            "source": source,
        }
        self._entries.append(entry)
        if len(self._entries) > JOURNAL_MAX_ENTRIES:
            self._entries = self._entries[-JOURNAL_MAX_ENTRIES:]
        await self._store.async_save(self._entries)
        self.hass.bus.async_fire(EVENT_JOURNAL, entry)
        async_dispatcher_send(self.hass, SIGNAL_JOURNAL_UPDATED)
        _LOGGER.debug("Journal: %s %s (%s)", action, name, entry["actor"] or source)
        return entry

    async def async_clear(self) -> None:
        """Wipe the journal."""
        self._entries = []
        await self._store.async_save(self._entries)
        async_dispatcher_send(self.hass, SIGNAL_JOURNAL_UPDATED)
