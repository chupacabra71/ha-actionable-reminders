"""Core reminder runner logic.

This module implements the ReminderRunner class which handles:
- Scheduled prompts (daily/weekly/monthly)
- Retry and escalation logic
- Presence detection and quiet hours
- Catch-up prompts when returning home
- Actionable notifications (mobile + Alexa)
- State persistence across restarts
"""

from __future__ import annotations

import asyncio
import calendar
import random
from datetime import date, datetime, time as dt_time, timedelta
import logging
from typing import Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, Event, Context
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.script import Script
from homeassistant.helpers.storage import Store
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SIGNAL_REMINDER_UPDATE,
    SIGNAL_REMINDERS_UPDATED,
    EVENT_COMPLETED,
    CONF_REMINDER_NAME,
    CONF_ENABLED,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_TIME,
    CONF_ONCE_DATE,
    CONF_ANNIVERSARY_DATE,
    CONF_DUE_TEMPLATE,
    CONF_ON_COMPLETE,
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
    CONF_LEAD_TIMES,
    CONF_NAG,
    CONF_ALLOW_CRITICAL,
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
    STATE_LAST_PROMPT,
    STATE_LAST_DONE,
    STATE_LAST_LEAD_DATE,
    STATE_RETRIES_TODAY,
    STATE_ESCALATED,
    STATE_ESCALATIONS_TODAY,
    STATE_AUTO_SKIPPED,
    STATE_RESET_DAY,
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
    DEFAULT_LEAD_TIMES,
    DEFAULT_NAG,
    DEFAULT_ALLOW_CRITICAL,
    DEFAULT_ENABLED,
    DEFAULT_ACK_MESSAGES,
    DEFAULT_DISMISS_MESSAGES,
    WEEKDAYS,
    MONTHLY_WEEKS,
)

_LOGGER = logging.getLogger(__name__)

# Tick interval for reminder checking (every minute)
TICK_INTERVAL = timedelta(minutes=1)

# Storage version for per-reminder runtime state (kept out of the config entry
# so saving state never triggers a config-update / reconfigure cycle).
STATE_STORAGE_VERSION = 1


def _time_parts(value: Any, fallback: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    """Parse an 'HH:MM' or 'HH:MM:SS' (or None/'') time string to (hour, minute).

    HA's TimeSelector stores times as 'HH:MM:SS'; older/imported values may be
    'HH:MM'. Both — and any malformed/empty value — are handled without raising,
    so a bad time can never crash the scheduling tick.
    """
    if not value:
        return fallback
    try:
        parts = str(value).split(":")
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError, TypeError):
        return fallback
    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour, minute
    return fallback


# ═══════════════════════════════════════════════════════════════════════════════
# ReminderRunner Class
# ═══════════════════════════════════════════════════════════════════════════════

class ReminderRunner:
    """Manages the lifecycle and logic for an individual reminder."""

    def __init__(
        self,
        hass: HomeAssistant,
        hub_entry,
        subentry,
        hub_config: dict[str, Any],
    ) -> None:
        """Initialize the reminder runner.

        Args:
            hass: Home Assistant instance
            hub_entry: The hub ConfigEntry that owns this reminder subentry
            subentry: The ConfigSubentry describing this reminder
            hub_config: Global configuration from hub (for defaults)
        """
        self.hass = hass
        self._hub_entry = hub_entry
        self._subentry = subentry
        self._hub_config = hub_config

        # Core identification.
        #   entry_id = the subentry id — the key used across the reminder
        #              registry, services, signals, and notification actions.
        #   uid      = the stable identity for the entity unique_id and the
        #              state store. It carries the legacy config-entry id for
        #              migrated reminders, so entity_ids never change.
        self.entry_id = subentry.subentry_id
        self.uid = subentry.unique_id or subentry.subentry_id
        self.name = subentry.data.get(CONF_REMINDER_NAME) or subentry.title

        # State tracking (persisted via a dedicated Store, not the config entry)
        self._state = {
            STATE_LAST_PROMPT: None,
            STATE_LAST_DONE: None,
            STATE_RETRIES_TODAY: 0,
            STATE_ESCALATED: False,
            STATE_ESCALATIONS_TODAY: 0,
            STATE_AUTO_SKIPPED: False,
            STATE_RESET_DAY: None,
        }
        self._store = Store(
            hass, STATE_STORAGE_VERSION, f"{DOMAIN}_state_{self.uid}"
        )

        # Control flags
        self._enabled = True
        self._started = False
        self._removing = False        # set while the reminder is being removed
        self._tmpl_warned = False     # de-spam due_template render errors
        self._tick_lock = asyncio.Lock()  # serialize timer/presence ticks

        # Timer and event listeners
        self._timer_remove = None
        self._presence_remove = []

        # Apply configuration from the subentry
        self._apply_config(subentry.data)

        _LOGGER.info("Initialized reminder: %s", self.name)

    # ────────────────────────────────────────────────────────────────────────────
    # Configuration Management
    # ────────────────────────────────────────────────────────────────────────────

    def _apply_config(self, config: dict[str, Any]) -> None:
        """Apply configuration from entry data."""
        # Basic settings
        self._enabled = config.get(CONF_ENABLED, DEFAULT_ENABLED)
        
        # Schedule configuration
        self.schedule_type = config.get(CONF_SCHEDULE_TYPE, "daily")
        self.schedule_time = config.get(CONF_SCHEDULE_TIME, "09:00")
        self.once_date = config.get(CONF_ONCE_DATE)  # one-time target date
        self.schedule_days = config.get(CONF_SCHEDULE_DAYS, [])
        self.schedule_monthly_type = config.get(CONF_SCHEDULE_MONTHLY_TYPE)
        self.schedule_monthly_day = config.get(CONF_SCHEDULE_MONTHLY_DAY)
        self.schedule_monthly_week = config.get(CONF_SCHEDULE_MONTHLY_WEEK)
        self.schedule_monthly_weekday = config.get(CONF_SCHEDULE_MONTHLY_WEEKDAY)
        
        # Messages (lists)
        self.prompt_messages = config.get(CONF_PROMPT_MESSAGES, [])
        self.ack_messages = config.get(CONF_ACK_MESSAGES, DEFAULT_ACK_MESSAGES)
        self.dismiss_messages = config.get(CONF_DISMISS_MESSAGES, DEFAULT_DISMISS_MESSAGES)
        
        # Notification settings (use hub defaults if not specified)
        self.mobile_service = config.get(
            CONF_MOBILE_SERVICE,
            self._hub_config.get(CONF_DEFAULT_MOBILE_SERVICE)
        )
        self.alexa_devices = config.get(
            CONF_ALEXA_DEVICES,
            self._hub_config.get(CONF_DEFAULT_ALEXA_DEVICES, [])
        )
        self.actionable = config.get(
            CONF_ACTIONABLE,
            self._hub_config.get(CONF_DEFAULT_ACTIONABLE, DEFAULT_ACTIONABLE)
        )
        self.escalation_volume = config.get(CONF_ESCALATION_VOLUME, DEFAULT_ESCALATION_VOLUME)
        
        # Retry and escalation (use hub defaults if not specified)
        self.retry_interval = config.get(
            CONF_RETRY_INTERVAL,
            self._hub_config.get(CONF_DEFAULT_RETRY_INTERVAL, DEFAULT_RETRY_INTERVAL)
        )
        self.max_retries = config.get(
            CONF_MAX_RETRIES,
            self._hub_config.get(CONF_DEFAULT_MAX_RETRIES, DEFAULT_MAX_RETRIES)
        )
        self.escalation_interval = config.get(
            CONF_ESCALATION_INTERVAL,
            self._hub_config.get(CONF_DEFAULT_ESCALATION_INTERVAL, DEFAULT_ESCALATION_INTERVAL)
        )
        self.max_escalations = config.get(
            CONF_MAX_ESCALATIONS,
            self._hub_config.get(CONF_DEFAULT_MAX_ESCALATIONS, DEFAULT_MAX_ESCALATIONS)
        )
        self.earliest_retry_time = self._hub_config.get(
            CONF_EARLIEST_RETRY_TIME,
            DEFAULT_EARLIEST_RETRY_TIME
        )
        
        # Presence and quiet hours (use hub defaults if not specified)
        self.presence_sensors = config.get(
            CONF_PRESENCE_SENSORS,
            self._hub_config.get(CONF_DEFAULT_PRESENCE_SENSORS, [])
        )
        self.catchup_on_arrival = config.get(CONF_CATCHUP_ON_ARRIVAL, DEFAULT_CATCHUP_ON_ARRIVAL)
        self.quiet_start = config.get(
            CONF_QUIET_START,
            self._hub_config.get(CONF_DEFAULT_QUIET_START, DEFAULT_QUIET_START)
        )
        self.quiet_end = config.get(
            CONF_QUIET_END,
            self._hub_config.get(CONF_DEFAULT_QUIET_END, DEFAULT_QUIET_END)
        )
        
        # Behavior flags
        self.optional = config.get(CONF_OPTIONAL, DEFAULT_OPTIONAL)
        self.until_done = config.get(CONF_UNTIL_DONE, DEFAULT_UNTIL_DONE)
        self.nag = config.get(CONF_NAG, DEFAULT_NAG)
        self.allow_critical = config.get(CONF_ALLOW_CRITICAL, DEFAULT_ALLOW_CRITICAL)
        self.lead_times = config.get(CONF_LEAD_TIMES, DEFAULT_LEAD_TIMES)
        self.anniversary_date = config.get(CONF_ANNIVERSARY_DATE)  # yearly
        self.due_template = config.get(CONF_DUE_TEMPLATE)  # condition source
        self.on_complete = config.get(CONF_ON_COMPLETE) or []  # HA action sequence

    async def async_reconfigure(self, config: dict[str, Any]) -> None:
        """Reconfigure the reminder with new settings."""
        _LOGGER.info("Reconfiguring reminder: %s", self.name)
        
        old_presence = self.presence_sensors
        
        # Apply new configuration
        self._apply_config(config)
        
        # Handle presence listener changes
        if old_presence != self.presence_sensors:
            for remove in self._presence_remove:
                remove()
            self._presence_remove.clear()
            
            if self._started:
                self._setup_presence_listeners()
        
        # Notify switch entity of changes
        async_dispatcher_send(
            self.hass,
            SIGNAL_REMINDER_UPDATE.format(self.entry_id),
        )

    async def async_update_hub_config(self, hub_config: dict[str, Any]) -> None:
        """Update hub-level configuration."""
        self._hub_config = hub_config
        _LOGGER.debug("Hub config updated for reminder: %s", self.name)

    def _self_remove(self) -> None:
        """Remove this reminder's subentry, drop its state, and reload the hub.

        Reloading the hub entry rebuilds the runner set from the remaining
        subentries — the reconcile path for any subentry add/edit/remove.
        """
        self._removing = True
        self.hass.async_create_task(self._store.async_remove())
        self.hass.config_entries.async_remove_subentry(
            self._hub_entry, self._subentry.subentry_id
        )
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self._hub_entry.entry_id)
        )

    # ────────────────────────────────────────────────────────────────────────────
    # Lifecycle Management
    # ────────────────────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start the reminder runner."""
        if self._started:
            _LOGGER.warning("Reminder already started: %s", self.name)
            return
        
        _LOGGER.info("Starting reminder: %s", self.name)
        
        # Load persisted state
        await self._load_state()
        
        # Setup presence listeners
        self._setup_presence_listeners()
        
        # Start periodic timer
        self._timer_remove = async_track_time_interval(
            self.hass,
            self._on_timer_tick,
            TICK_INTERVAL,
        )
        
        self._started = True
        _LOGGER.info("Reminder started: %s", self.name)

    async def async_stop(self) -> None:
        """Stop the reminder runner."""
        if not self._started:
            return
        
        _LOGGER.info("Stopping reminder: %s", self.name)
        
        # Stop timer
        if self._timer_remove:
            self._timer_remove()
            self._timer_remove = None
        
        # Remove presence listeners
        for remove in self._presence_remove:
            remove()
        self._presence_remove.clear()
        
        # Save final state (skip when the entry is being removed).
        if not self._removing:
            await self._save_state()

        self._started = False
        _LOGGER.info("Reminder stopped: %s", self.name)

    def _setup_presence_listeners(self) -> None:
        """Setup state change listeners for presence entities."""
        if not self.presence_sensors:
            return
        
        for entity_id in self.presence_sensors:
            remove = async_track_state_change_event(
                self.hass,
                [entity_id],
                self._on_presence_change,
            )
            self._presence_remove.append(remove)

    # ────────────────────────────────────────────────────────────────────────────
    # State Persistence
    # ────────────────────────────────────────────────────────────────────────────

    async def _load_state(self) -> None:
        """Load persisted state from the Store, migrating from entry data once."""
        stored = await self._store.async_load()
        if stored:
            self._state.update(stored)
            _LOGGER.debug("Loaded state for %s: %s", self.name, self._state)
            return
        # Migration: older versions kept state inside the config entry.
        legacy = self._subentry.data.get("state")
        if legacy:
            self._state.update(legacy)
            await self._store.async_save(dict(self._state))
            _LOGGER.debug("Migrated legacy state for %s", self.name)
        else:
            _LOGGER.debug("No persisted state for %s", self.name)

    async def _save_state(self) -> None:
        """Persist runtime state to the Store (never the config entry).

        Writing runtime state here — instead of via async_update_entry — keeps
        every prompt/retry from triggering the entry update-listener (and thus a
        full reconfigure). A copy is stored so the live dict isn't aliased.
        """
        await self._store.async_save(dict(self._state))
        _LOGGER.debug("Saved state for %s: %s", self.name, self._state)

    # ────────────────────────────────────────────────────────────────────────────
    # Timer Tick Logic
    # ────────────────────────────────────────────────────────────────────────────

    async def _on_timer_tick(self, now: datetime) -> None:
        """Handle timer tick (runs every minute)."""
        if not self._enabled:
            return

        # Master kill switch — when off, nothing fires (any reminder).
        if not self.hass.data.get(DOMAIN, {}).get("hub", {}).get("master_enabled", True):
            return

        # Serialize ticks. A presence-change catch-up can invoke this while the
        # minutely tick is mid-flight; without this both could pass _is_due
        # before last_prompt is written and double-fire (and race the counter).
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            # async_track_time_interval fires with a UTC datetime, but every gate
            # below (quiet hours, schedule_time, earliest_retry_time) compares
            # now.time() against local "HH:MM" strings. Without this the whole
            # schedule is evaluated in UTC — the cause of pre-dawn nagging.
            now = dt_util.as_local(now)

            # Reset daily state at midnight
            await self._check_daily_reset(now)

            # Pre-notifications (lead-time heads-ups) — independent of the due nag
            await self._maybe_send_lead_announcement(now)

            # Check if reminder is due for prompting
            if self._is_due(now):
                await self._handle_due_reminder(now)

    async def _check_daily_reset(self, now: datetime) -> None:
        """Reset daily counters at midnight."""
        today = now.date().isoformat()

        # Reset once per calendar day, tracked by its own marker rather than
        # last_done — otherwise a reminder that is NEVER completed never resets,
        # so its escalation/auto-skip state bleeds across midnight (a reminder
        # that auto-skipped once would go permanently silent).
        if self._state.get(STATE_RESET_DAY) != today:
            self._state[STATE_RESET_DAY] = today
            self._state[STATE_RETRIES_TODAY] = 0
            self._state[STATE_ESCALATED] = False
            self._state[STATE_ESCALATIONS_TODAY] = 0
            self._state[STATE_AUTO_SKIPPED] = False
            await self._save_state()

    def _is_due(self, now: datetime) -> bool:
        """Check if reminder is currently due for prompting."""
        today = now.date().isoformat()
        
        # Already done today, or gave up (auto-skipped) for today. Both clear at
        # the next day boundary via _check_daily_reset.
        if self._state[STATE_LAST_DONE] == today or self._state[STATE_AUTO_SKIPPED]:
            return False
        
        # Check basic schedule
        if not self._is_scheduled(now):
            return False
        
        # Blocked by quiet hours?
        if self._in_quiet_hours(now):
            return False
        
        # Blocked by presence?
        if not self._presence_satisfied():
            return False
        
        # Check retry gate
        if not self._retry_ready(now):
            return False
        
        return True

    def _is_scheduled(self, now: datetime) -> bool:
        """Check if current time matches the schedule."""
        # Condition-based reminders are due whenever the template is truthy
        # (no time-of-day gate).
        if self.schedule_type == "condition":
            return self._eval_due_template()

        # Parse schedule time
        hour, minute = _time_parts(self.schedule_time, (9, 0))
        schedule_time = dt_time(hour, minute)
        
        # Must be past the scheduled time today
        if now.time() < schedule_time:
            return False
        
        # Check schedule type
        if self.schedule_type == "daily":
            return True
        
        elif self.schedule_type == "weekly":
            if not self.schedule_days:
                return True
            today_key = WEEKDAYS[now.weekday()]
            return today_key in self.schedule_days
        
        elif self.schedule_type == "monthly":
            return self._is_scheduled_monthly(now)

        elif self.schedule_type == "yearly":
            return self._date_matches_schedule(now.date())

        elif self.schedule_type == "once":
            if not self.once_date:
                return False
            try:
                target = date.fromisoformat(self.once_date)
            except (TypeError, ValueError):
                return False
            # Due on or after the target date (time-of-day gate already applied).
            return now.date() >= target
        
        return False

    def _is_scheduled_monthly(self, now: datetime) -> bool:
        """Check if current date matches monthly schedule.

        Delegates to the guarded _date_matches_monthly so live scheduling and
        the display path share one implementation (and both survive a
        half-configured week-pattern instead of crashing the tick).
        """
        return self._date_matches_monthly(now.date())

    def _date_matches_schedule(self, d: date) -> bool:
        """Whether the given date matches this reminder's recurrence."""
        if self.schedule_type == "daily":
            return True
        if self.schedule_type == "weekly":
            if not self.schedule_days:
                return True
            return WEEKDAYS[d.weekday()] in self.schedule_days
        if self.schedule_type == "monthly":
            return self._date_matches_monthly(d)
        if self.schedule_type == "yearly":
            if not self.anniversary_date:
                return False
            try:
                a = date.fromisoformat(self.anniversary_date)
            except (TypeError, ValueError):
                return False
            # Feb-29 anniversaries fall back to Feb 28 in non-leap years so they
            # still fire every year instead of only 1 year in 4.
            if (a.month, a.day) == (2, 29) and not calendar.isleap(d.year):
                return (d.month, d.day) == (2, 28)
            return (d.month, d.day) == (a.month, a.day)
        if self.schedule_type == "once":
            return self.once_date == d.isoformat()
        return False

    def _date_matches_monthly(self, d: date) -> bool:
        """Whether a date matches the monthly schedule (day or week-pattern)."""
        if self.schedule_monthly_type == "day":
            if not self.schedule_monthly_day:
                return False
            # Clamp to the month's length so "day 31" still fires in short
            # months (on the last day) instead of being silently skipped.
            last_day = calendar.monthrange(d.year, d.month)[1]
            return d.day == min(self.schedule_monthly_day, last_day)
        if self.schedule_monthly_type == "week_pattern":
            try:
                target_weekday = WEEKDAYS.index(self.schedule_monthly_weekday)
            except (ValueError, TypeError):
                return False
            weeks_with_day = [
                w for w in calendar.monthcalendar(d.year, d.month)
                if w[target_weekday] != 0
            ]
            if not weeks_with_day:
                return False
            if self.schedule_monthly_week == "last":
                target_day = weeks_with_day[-1][target_weekday]
            else:
                try:
                    idx = MONTHLY_WEEKS.index(self.schedule_monthly_week)
                except (ValueError, TypeError):
                    return False
                if idx >= len(weeks_with_day):
                    return False
                target_day = weeks_with_day[idx][target_weekday]
            return d.day == target_day
        return False

    @property
    def next_due_date(self) -> date | None:
        """The next date this reminder is due (for display), or None."""
        if self.schedule_type == "condition":
            return None  # no calendar date — driven by a template
        today = dt_util.now().date()
        done_today = self._state.get(STATE_LAST_DONE) == today.isoformat()
        if self.schedule_type == "once":
            if not self.once_date:
                return None
            try:
                return date.fromisoformat(self.once_date)
            except (TypeError, ValueError):
                return None
        # Recurring: scan forward for the next matching date.
        for offset in range(0, 366):
            d = today + timedelta(days=offset)
            if self._date_matches_schedule(d):
                if offset == 0 and done_today:
                    continue
                return d
        return None

    def _eval_due_template(self) -> bool:
        """Render the condition source's due_template to a bool.

        Exposes `days_since_done` (int; a large number if never done) and
        `last_done` (ISO date string or None) to the template, so a condition
        can combine an interval with a state check, e.g.
        `{{ days_since_done >= 7 and temp > 55 }}`.
        """
        if not self.due_template:
            return False
        last_done = self._state.get(STATE_LAST_DONE)
        days_since = 99999
        if last_done:
            try:
                days_since = (dt_util.now().date() - date.fromisoformat(last_done)).days
            except (TypeError, ValueError):
                days_since = 99999
        try:
            res = Template(self.due_template, self.hass).async_render(
                {"days_since_done": days_since, "last_done": last_done}
            )
        except Exception as e:  # noqa: BLE001
            # Log once, not every tick, so a broken template doesn't spam the log.
            if not self._tmpl_warned:
                _LOGGER.warning("due_template error for %s: %s", self.name, e)
                self._tmpl_warned = True
            return False
        self._tmpl_warned = False
        if isinstance(res, bool):
            return res
        return str(res).strip().lower() in ("true", "on", "yes", "1")

    def is_condition_due(self) -> bool:
        """Whether a condition reminder is currently due (for the to-do list)."""
        return self.schedule_type == "condition" and self._eval_due_template()

    def _in_quiet_hours(self, now: datetime) -> bool:
        """Check if current time is in quiet hours."""
        if not self.quiet_start or not self.quiet_end:
            return False
        
        start_time = dt_time(*_time_parts(self.quiet_start))
        end_time = dt_time(*_time_parts(self.quiet_end))
        current_time = now.time()

        # Equal bounds would otherwise mean a 24-hour mute — treat as "no quiet".
        if start_time == end_time:
            return False

        # Handle quiet hours that wrap midnight
        if end_time > start_time:
            return start_time <= current_time < end_time
        else:
            return current_time >= start_time or current_time < end_time

    def _presence_satisfied(self) -> bool:
        """Check if presence requirements are satisfied."""
        if not self.presence_sensors:
            return True

        # Only consider sensors that are actually reporting. If a presence
        # integration is down (all unavailable/unknown/missing) we must NOT
        # silently suppress the reminder — fail open so it still prompts.
        valid = [
            s
            for e in self.presence_sensors
            if (s := self.hass.states.get(e))
            and s.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ]
        if not valid:
            return True

        # At least one person home
        return any(s.state == STATE_ON for s in valid)

    def _retry_ready(self, now: datetime) -> bool:
        """Check if enough time has passed for retry."""
        last_prompt = self._state[STATE_LAST_PROMPT]
        if not last_prompt:
            return True
        
        last_time = datetime.fromisoformat(last_prompt)
        elapsed = (now - last_time).total_seconds() / 60
        
        # Determine interval based on escalation
        if self._state[STATE_ESCALATED]:
            interval = self.escalation_interval
        else:
            interval = self.retry_interval
        
        return elapsed >= interval

    def _past_earliest_retry_time(self, now: datetime) -> bool:
        """Check if we're past the earliest retry time for a new day."""
        earliest = dt_time(*_time_parts(self.earliest_retry_time, (10, 0)))
        return now.time() >= earliest

    # ────────────────────────────────────────────────────────────────────────────
    # Due Reminder Handling
    # ────────────────────────────────────────────────────────────────────────────

    async def _handle_due_reminder(self, now: datetime) -> None:
        """Handle a reminder that is due for prompting."""
        # Non-nagging reminders: one announce at due, then auto-complete
        # (no retry/escalation, nothing to acknowledge).
        if not self.nag:
            await self._send_announcement(now, offset=0)
            self._state[STATE_LAST_DONE] = now.date().isoformat()
            await self._save_state()
            async_dispatcher_send(
                self.hass, SIGNAL_REMINDER_UPDATE.format(self.entry_id)
            )
            async_dispatcher_send(self.hass, SIGNAL_REMINDERS_UPDATED)
            # A one-time announce is finished after it fires — remove it so it
            # doesn't re-announce every following day.
            if self.schedule_type == "once":
                self._self_remove()
            return

        retries = self._state[STATE_RETRIES_TODAY]
        escalations = self._state[STATE_ESCALATIONS_TODAY]
        
        # Check if we should escalate
        if retries >= self.max_retries and not self._state[STATE_ESCALATED]:
            self._state[STATE_ESCALATED] = True
            _LOGGER.info("Reminder %s entering escalation mode", self.name)
        
        # Check if we hit max escalations
        if self._state[STATE_ESCALATED] and escalations >= self.max_escalations:
            _LOGGER.info("Reminder %s reached max escalations, auto-skipping", self.name)
            await self._auto_skip()
            return
        
        # Send the prompt
        await self._send_prompt(now)

    async def _maybe_send_lead_announcement(self, now: datetime) -> None:
        """Fire one informational heads-up if today is a lead-time day."""
        if not self.lead_times:
            return
        today = now.date()
        if self._state.get(STATE_LAST_LEAD_DATE) == today.isoformat():
            return
        try:
            hour, minute = _time_parts(self.schedule_time, (9, 0))
        except (ValueError, AttributeError):
            hour, minute = 9, 0
        if now.time() < dt_time(hour, minute):
            return
        if self._in_quiet_hours(now) or not self._presence_satisfied():
            return
        for offset in sorted((o for o in self.lead_times if o and o > 0), reverse=True):
            if self._date_matches_schedule(today + timedelta(days=offset)):
                await self._send_announcement(now, offset=offset)
                self._state[STATE_LAST_LEAD_DATE] = today.isoformat()
                await self._save_state()
                async_dispatcher_send(
                    self.hass, SIGNAL_REMINDER_UPDATE.format(self.entry_id)
                )
                return

    def _humanize_offset(self, offset: int) -> str:
        """Human phrase for a lead-time offset in days."""
        if offset <= 0:
            return "today"
        if offset == 1:
            return "tomorrow"
        if offset == 7:
            return "in a week"
        if offset == 14:
            return "in two weeks"
        if offset in (30, 31):
            return "in a month"
        if offset % 7 == 0:
            return f"in {offset // 7} weeks"
        return f"in {offset} days"

    def _yearly_age(self, now: datetime) -> int | None:
        """Age at the next anniversary (yearly reminders with a birth year)."""
        if self.schedule_type != "yearly" or not self.anniversary_date:
            return None
        try:
            a = date.fromisoformat(self.anniversary_date)
        except (TypeError, ValueError):
            return None
        today = now.date()
        year = today.year if (a.month, a.day) >= (today.month, today.day) else today.year + 1
        return year - a.year

    async def _send_announcement(self, now: datetime, offset: int = 0) -> None:
        """Send one informational (non-actionable) announcement.

        offset > 0 → a lead-time heads-up; offset 0 → the due-day announce for a
        non-nagging reminder.
        """
        if offset > 0:
            message = f"⏰ {self.name} — {self._humanize_offset(offset)}"
        elif self.schedule_type == "yearly":
            age = self._yearly_age(now)
            message = f"🎂 {self.name} is today"
            if age is not None:
                message += f" — turning {age}"
            message += "!"
        elif self.prompt_messages:
            message = random.choice(self.prompt_messages)
        else:
            message = f"🔔 {self.name}"
        _LOGGER.info("Announcing for %s: %s", self.name, message)
        await self._announce(message)

    async def _announce(self, message: str) -> None:
        """Deliver a non-actionable announcement (prefer unified_notifications)."""
        if self._use_unified_notifications():
            data = {
                "method": "all",
                "who": "all",
                "severity": "INFO",
                "title": "🔔 Reminder",
                "message": message,
                "tag": f"ar_{self.entry_id}",
            }
            if self.alexa_devices:
                data["alexa_device"] = self.alexa_devices[0]
            try:
                await self.hass.services.async_call(
                    "script", "unified_notifications", data, blocking=False
                )
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("Announcement failed for %s: %s", self.name, e)
            return
        # Fallbacks when the script isn't present.
        if self.mobile_service:
            await self._send_mobile_announce(message)
        if self.alexa_devices:
            await self._send_alexa_announce(message, self.alexa_devices, 0.4)

    async def _send_prompt(self, now: datetime) -> None:
        """Send a reminder prompt."""
        # Select random prompt message
        if self.prompt_messages:
            prompt = random.choice(self.prompt_messages)
        else:
            prompt = f"Did you complete: {self.name}?"
        
        _LOGGER.info(
            "Sending prompt for %s (retries=%s, escalated=%s)",
            self.name,
            self._state[STATE_RETRIES_TODAY],
            self._state[STATE_ESCALATED],
        )
        
        # Determine volume and device rotation for Alexa
        if self._state[STATE_ESCALATED] and self.alexa_devices:
            volume = self.escalation_volume
            # Rotate devices during escalation
            device_idx = self._state[STATE_ESCALATIONS_TODAY] % len(self.alexa_devices)
            alexa_target = [self.alexa_devices[device_idx]]
        else:
            volume = 0.6
            alexa_target = self.alexa_devices
        
        # Deliver the prompt. Preferred (default) channel: delegate to the proven
        # script.unified_notifications, which owns the voice (single Echo) + mobile
        # + actionable round-trip and calls back into our mark_done / dismiss
        # services. This closes the ack loop the built-in sends leave open and
        # provides Alexa voice-ack (which _send_alexa_actionable never implemented).
        # Soft dependency: fall back to the built-in mobile/Alexa sends if the
        # script is not present, so the integration stays shareable.
        if self._use_unified_notifications():
            await self._send_via_unified_notifications(prompt, volume)
        else:
            # Send to mobile if configured
            if self.mobile_service and self.actionable:
                await self._send_mobile_actionable(prompt)
            elif self.mobile_service:
                await self._send_mobile_announce(prompt)

            # Send to Alexa if configured
            if alexa_target:
                if self.actionable:
                    await self._send_alexa_actionable(prompt, alexa_target, volume)
                else:
                    await self._send_alexa_announce(prompt, alexa_target, volume)

        # Update state
        self._state[STATE_LAST_PROMPT] = now.isoformat()
        if self._state[STATE_ESCALATED]:
            self._state[STATE_ESCALATIONS_TODAY] += 1
        else:
            self._state[STATE_RETRIES_TODAY] += 1
        
        await self._save_state()
        
        # Notify switch entity
        async_dispatcher_send(
            self.hass,
            SIGNAL_REMINDER_UPDATE.format(self.entry_id),
        )

    def _use_unified_notifications(self) -> bool:
        """Whether to delegate delivery to script.unified_notifications.

        Default channel when the script exists (soft dependency).
        """
        return self.hass.services.has_service("script", "unified_notifications")

    async def _send_via_unified_notifications(self, message: str, volume: float) -> None:
        """Delegate delivery to script.unified_notifications (default channel).

        Voice on a single Echo + mobile + actionable; the Done / Not-yet buttons
        (and Alexa voice "yes") call back into our mark_done / dismiss services,
        so the acknowledgement loop is owned entirely by the script. Non-blocking
        so the escalation timer is never held by the ack wait.
        """
        # Escalation stays at TIME-SENSITIVE unless this reminder explicitly
        # opts in to DND-bypassing CRITICAL — nothing overrides Do Not Disturb
        # by default.
        if self._state[STATE_ESCALATED] and self.allow_critical:
            severity = "CRITICAL"
        else:
            severity = "TIME-SENSITIVE"
        data = {
            "method": "all",
            "who": "all",
            "alert_volume": volume,
            "severity": severity,
            "title": "🔔 Reminder",
            "message": message,
            "tag": f"ar_{self.entry_id}",
        }
        # Target a configured Echo if one is set; otherwise let the notification
        # script choose its own default device.
        if self.alexa_devices:
            data["alexa_device"] = self.alexa_devices[0]
        if self.actionable:
            data.update(
                {
                    "confirm_text": "Done",
                    "confirm_action": [
                        {
                            "action": f"{DOMAIN}.mark_done",
                            "data": {"entry_id": self.entry_id},
                        }
                    ],
                    "dismiss_text": "Not yet",
                    "dismiss_action": [
                        {
                            "action": f"{DOMAIN}.dismiss",
                            "data": {"entry_id": self.entry_id},
                        }
                    ],
                }
            )
        try:
            await self.hass.services.async_call(
                "script", "unified_notifications", data, blocking=False
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "unified_notifications delivery failed for %s: %s", self.name, e
            )

    async def _send_mobile_actionable(self, message: str) -> None:
        """Send actionable notification to mobile."""
        try:
            service_parts = self.mobile_service.split(".")
            if len(service_parts) == 2:
                domain, service = service_parts
            else:
                domain, service = "notify", self.mobile_service
            
            await self.hass.services.async_call(
                domain,
                service,
                {
                    "message": message,
                    "title": "Reminder",
                    "data": {
                        "actions": [
                            {
                                "action": f"{DOMAIN}_done_{self.entry_id}",
                                "title": "Yes, Done"
                            },
                            {
                                "action": f"{DOMAIN}_dismiss_{self.entry_id}",
                                "title": "No, Not Yet"
                            },
                        ]
                    }
                },
            )
        except Exception as e:
            _LOGGER.error("Failed to send mobile notification for %s: %s", self.name, e)

    async def _send_mobile_announce(self, message: str) -> None:
        """Send simple announcement to mobile."""
        try:
            service_parts = self.mobile_service.split(".")
            if len(service_parts) == 2:
                domain, service = service_parts
            else:
                domain, service = "notify", self.mobile_service
            
            await self.hass.services.async_call(
                domain,
                service,
                {
                    "message": message,
                    "title": "Reminder",
                },
            )
        except Exception as e:
            _LOGGER.error("Failed to send mobile announcement for %s: %s", self.name, e)

    async def _send_alexa_actionable(self, message: str, devices: list[str], volume: float) -> None:
        """Send actionable notification to Alexa."""
        # TODO: Implement Alexa actionable notifications
        # For now, fall back to announce
        await self._send_alexa_announce(message, devices, volume)

    async def _send_alexa_announce(self, message: str, devices: list[str], volume: float) -> None:
        """Send announcement to Alexa."""
        for device in devices:
            try:
                await self.hass.services.async_call(
                    "notify",
                    "alexa_media",
                    {
                        "message": message,
                        "target": device,
                        "data": {
                            "type": "announce",
                            "method": "all",
                        }
                    },
                )
            except Exception as e:
                _LOGGER.error("Failed to send Alexa notification for %s: %s", self.name, e)

    # ────────────────────────────────────────────────────────────────────────────
    # Presence Change Handler
    # ────────────────────────────────────────────────────────────────────────────

    async def _on_presence_change(self, event: Event) -> None:
        """Handle presence entity state change."""
        if self.catchup_on_arrival:
            _LOGGER.debug("Presence changed for %s, checking for catch-up", self.name)
            await self._on_timer_tick(dt_util.now())

    # ────────────────────────────────────────────────────────────────────────────
    # Public Service Methods
    # ────────────────────────────────────────────────────────────────────────────

    async def async_mark_done(self) -> None:
        """Mark reminder as done for today."""
        today = dt_util.now().date().isoformat()
        
        _LOGGER.info("Marking reminder done: %s", self.name)
        
        # Update state
        self._state[STATE_LAST_DONE] = today
        self._state[STATE_LAST_PROMPT] = dt_util.now().isoformat()
        self._state[STATE_RETRIES_TODAY] = 0
        self._state[STATE_ESCALATED] = False
        self._state[STATE_ESCALATIONS_TODAY] = 0
        self._state[STATE_AUTO_SKIPPED] = False
        
        await self._save_state()
        
        # Send acknowledgment
        ack_msg = random.choice(self.ack_messages)
        await self._send_ack(ack_msg)

        # Run the reminder's own completion action(s): a native HA action
        # sequence stored on the reminder — no bridge automation required.
        if self.on_complete:
            try:
                # Bound the action so a hanging on_complete (delay, wait_template,
                # slow blocking call) can't stall completion / the ack loop.
                await asyncio.wait_for(
                    Script(
                        self.hass, self.on_complete, self.name, DOMAIN
                    ).async_run(context=Context()),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                _LOGGER.error("on_complete timed out for %s", self.name)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error("on_complete failed for %s: %s", self.name, e)

        # Also fire a generic completion event for any external listeners.
        self.hass.bus.async_fire(
            EVENT_COMPLETED, {"entry_id": self.entry_id, "name": self.name}
        )

        # Refresh the aggregate to-do list; one-time reminders self-remove.
        async_dispatcher_send(self.hass, SIGNAL_REMINDERS_UPDATED)
        if self.schedule_type == "once":
            self._self_remove()
        
        # Notify switch entity
        async_dispatcher_send(
            self.hass,
            SIGNAL_REMINDER_UPDATE.format(self.entry_id),
        )

    async def async_dismiss(self) -> None:
        """Dismiss reminder (not ready yet)."""
        _LOGGER.info("Reminder dismissed: %s", self.name)
        
        now = dt_util.now()
        
        # Update state
        self._state[STATE_LAST_PROMPT] = now.isoformat()
        
        # Increment appropriate counter
        if self._state[STATE_ESCALATED]:
            self._state[STATE_ESCALATIONS_TODAY] += 1
        else:
            self._state[STATE_RETRIES_TODAY] += 1
        
        await self._save_state()
        
        # Send dismissal acknowledgment
        dismiss_msg = random.choice(self.dismiss_messages)
        await self._send_ack(dismiss_msg)
        
        # Notify switch entity
        async_dispatcher_send(
            self.hass,
            SIGNAL_REMINDER_UPDATE.format(self.entry_id),
        )

    async def async_skip_today(self) -> None:
        """Skip this reminder for today."""
        today = dt_util.now().date().isoformat()
        
        _LOGGER.info("Skipping reminder for today: %s", self.name)
        
        self._state[STATE_LAST_DONE] = today
        self._state[STATE_RETRIES_TODAY] = 0
        self._state[STATE_ESCALATED] = False
        self._state[STATE_ESCALATIONS_TODAY] = 0
        self._state[STATE_AUTO_SKIPPED] = False
        
        await self._save_state()
        
        # Notify switch entity
        async_dispatcher_send(
            self.hass,
            SIGNAL_REMINDER_UPDATE.format(self.entry_id),
        )

    async def _auto_skip(self) -> None:
        """Auto-skip after max escalations."""
        today = dt_util.now().date().isoformat()
        
        _LOGGER.warning("Auto-skipping reminder after max escalations: %s", self.name)
        
        self._state[STATE_LAST_DONE] = today
        self._state[STATE_AUTO_SKIPPED] = True
        self._state[STATE_RETRIES_TODAY] = 0
        self._state[STATE_ESCALATED] = False
        self._state[STATE_ESCALATIONS_TODAY] = 0
        
        await self._save_state()
        
        # Notify switch entity
        async_dispatcher_send(
            self.hass,
            SIGNAL_REMINDER_UPDATE.format(self.entry_id),
        )

    async def async_force_prompt(self) -> None:
        """Force an immediate prompt (or announce, for non-nagging reminders)."""
        _LOGGER.info("Forcing prompt for reminder: %s", self.name)
        now = dt_util.now()
        if not self.nag:
            await self._send_announcement(now, offset=0)
        else:
            await self._send_prompt(now)

    # ────────────────────────────────────────────────────────────────────────────
    # Helper Methods
    # ────────────────────────────────────────────────────────────────────────────

    async def _send_ack(self, message: str) -> None:
        """Send acknowledgment message via Alexa."""
        if not self.alexa_devices:
            return
        
        for device in self.alexa_devices:
            try:
                await self.hass.services.async_call(
                    "notify",
                    "alexa_media",
                    {
                        "message": message,
                        "target": device,
                        "data": {
                            "type": "tts",
                        }
                    },
                )
            except Exception as e:
                _LOGGER.error("Failed to send ack for %s: %s", self.name, e)

    # ────────────────────────────────────────────────────────────────────────────
    # Properties
    # ────────────────────────────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        """Return if reminder is enabled."""
        return self._enabled

    @property
    def state_dict(self) -> dict[str, Any]:
        """Return current state dictionary."""
        return dict(self._state)
