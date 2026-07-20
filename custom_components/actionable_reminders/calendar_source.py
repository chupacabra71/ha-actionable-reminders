"""Calendar source: turn events on a watched calendar into reminders.

Reads a single "Reminders" calendar and treats each upcoming event as a
transient reminder keyed by (uid, start) — no config entry per event. Sends a
day-before heads-up and a day-of actionable nag; completion is tracked in this
source (engine-side) and the event is left on the calendar as history. Events
removed from the calendar are pruned from state (also treated as done).
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
import hashlib
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=5)
LOOKAHEAD_DAYS = 60
WAKE_START = dt_time(8, 0)
WAKE_END = dt_time(21, 0)
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_calendar_source"


class CalendarSource:
    """Watches one calendar and drives reminders from its events."""

    def __init__(self, hass: HomeAssistant, calendar_entity: str, hub_config: dict) -> None:
        """Initialize the source."""
        self.hass = hass
        self.calendar_entity = calendar_entity
        self.hub_config = hub_config
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # key = f"{uid}|{start}"; each maps key -> ISO date string
        self._state: dict[str, dict[str, str]] = {
            "acked": {},
            "lead_sent": {},
            "prompted": {},
        }
        self._unsub = None

    async def async_start(self) -> None:
        """Load persisted state and begin polling."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            for k in ("acked", "lead_sent", "prompted"):
                self._state[k] = dict(stored.get(k, {}))
        self._unsub = async_track_time_interval(self.hass, self._tick, POLL_INTERVAL)
        # First pass shortly after start.
        self.hass.async_create_task(self._tick(dt_util.now()))
        _LOGGER.info("Calendar source watching %s", self.calendar_entity)

    async def async_stop(self) -> None:
        """Stop polling and flush state."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        await self._store.async_save(self._state)

    @staticmethod
    def _tag(key: str) -> str:
        """Stable, per-event notification tag (so events don't overwrite each other)."""
        return "cal_" + hashlib.md5(key.encode()).hexdigest()[:12]

    def ack(self, event_key: str) -> None:
        """Mark an event acknowledged (from the calendar_ack service)."""
        self._state["acked"][event_key] = dt_util.now().date().isoformat()
        self.hass.async_create_task(self._store.async_save(self._state))
        _LOGGER.info("Calendar reminder acked: %s", event_key)

    # ────────────────────────────────────────────────────────────────────────

    async def _tick(self, now: datetime) -> None:
        """Fetch events and drive lead / day-of notifications."""
        # Master kill switch — when off, no calendar prompt fires.
        if not self.hass.data.get(DOMAIN, {}).get("hub", {}).get("master_enabled", True):
            return

        # async_track_time_interval fires in UTC; the wake-window check below
        # compares now.time() against local WAKE_START/WAKE_END. Normalize.
        now = dt_util.as_local(now)
        events = await self._fetch_events(now)
        if events is None:
            return
        today = now.date()
        waking = WAKE_START <= now.time() <= WAKE_END
        current: set[str] = set()

        for ev in events:
            start = ev.get("start")
            ev_date = self._parse_date(start)
            if ev_date is None:
                continue
            summary = (ev.get("summary") or "Reminder").strip()
            if summary.startswith("✅"):
                continue  # treated as already handled
            key = f"{ev.get('uid') or summary}|{start}"
            current.add(key)

            if key in self._state["acked"]:
                continue

            # Day-before heads-up (once).
            if ev_date == today + timedelta(days=1):
                if waking and self._state["lead_sent"].get(key) != today.isoformat():
                    await self._announce(key, f"⏰ Tomorrow: {summary}")
                    self._state["lead_sent"][key] = today.isoformat()

            # Day-of (or overdue) actionable nag — once per day.
            if ev_date <= today:
                if waking and self._state["prompted"].get(key) != today.isoformat():
                    await self._nag(key, summary)
                    self._state["prompted"][key] = today.isoformat()

        # Prune state for events no longer in the lookahead window. A past event
        # can't recur (its start is baked into the key), so dropping its state —
        # including acked — is safe and keeps the store from growing unbounded.
        # Only prune on a real non-empty fetch: a transient empty result (e.g.
        # the calendar not yet loaded just after restart) must NOT wipe acked
        # state, or already-done events would re-nag when they reappear.
        if events:
            for bucket in ("acked", "lead_sent", "prompted"):
                for key in list(self._state[bucket]):
                    if key not in current:
                        del self._state[bucket][key]

        await self._store.async_save(self._state)

    async def _fetch_events(self, now: datetime) -> list[dict] | None:
        """Return the calendar's upcoming events, or None on error."""
        try:
            resp = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": self.calendar_entity,
                    "start_date_time": now.isoformat(),
                    "end_date_time": (now + timedelta(days=LOOKAHEAD_DAYS)).isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("get_events failed for %s: %s", self.calendar_entity, e)
            return None
        cal = (resp or {}).get(self.calendar_entity, {})
        return cal.get("events", []) if isinstance(cal, dict) else []

    @staticmethod
    def _parse_date(start: str | None) -> date | None:
        """Parse an event start (all-day date or ISO datetime) to a date."""
        if not start:
            return None
        try:
            if len(start) == 10:  # YYYY-MM-DD (all-day)
                return date.fromisoformat(start)
            parsed = dt_util.parse_datetime(start)
            # Normalize to local before taking the date, or a late-evening UTC
            # timed event can land on the wrong calendar day.
            return dt_util.as_local(parsed).date() if parsed else None
        except (TypeError, ValueError):
            return None

    async def _announce(self, key: str, message: str) -> None:
        """Send an informational (non-actionable) heads-up."""
        await self._notify(
            {"severity": "INFO", "message": message, "tag": self._tag(key)}
        )

    async def _nag(self, key: str, summary: str) -> None:
        """Send the actionable day-of nag; Done acks this event."""
        await self._notify(
            {
                "severity": "TIME-SENSITIVE",
                "message": f"{summary} — say done when it's handled.",
                "tag": self._tag(key),
                "confirm_text": "Done",
                "confirm_action": [
                    {
                        "action": f"{DOMAIN}.calendar_ack",
                        "data": {"event_key": key},
                    }
                ],
            }
        )

    async def _notify(self, extra: dict) -> None:
        """Deliver via script.unified_notifications (soft dependency)."""
        if not self.hass.services.has_service("script", "unified_notifications"):
            return
        data = {
            "method": "all",
            "who": "all",
            "title": "🔔 Reminder",
            "tag": "cal_reminder",
        }
        devices = self.hub_config.get("default_alexa_devices") or []
        if devices:
            data["alexa_device"] = devices[0]
        data.update(extra)
        try:
            await self.hass.services.async_call(
                "script", "unified_notifications", data, blocking=False
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("calendar notify failed: %s", e)
