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

import calendar
import random
from datetime import datetime, time as dt_time, timedelta
import logging
from typing import Any

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SIGNAL_REMINDER_UPDATE,
    CONF_REMINDER_NAME,
    CONF_ENABLED,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_TIME,
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
    STATE_RETRIES_TODAY,
    STATE_ESCALATED,
    STATE_ESCALATIONS_TODAY,
    STATE_AUTO_SKIPPED,
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
    DEFAULT_ENABLED,
    DEFAULT_ACK_MESSAGES,
    DEFAULT_DISMISS_MESSAGES,
    WEEKDAYS,
    MONTHLY_WEEKS,
)

_LOGGER = logging.getLogger(__name__)

# Tick interval for reminder checking (every minute)
TICK_INTERVAL = timedelta(minutes=1)


# ═══════════════════════════════════════════════════════════════════════════════
# ReminderRunner Class
# ═══════════════════════════════════════════════════════════════════════════════

class ReminderRunner:
    """Manages the lifecycle and logic for an individual reminder."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        hub_config: dict[str, Any],
    ) -> None:
        """Initialize the reminder runner.
        
        Args:
            hass: Home Assistant instance
            entry: Config entry for this reminder
            hub_config: Global configuration from hub (for defaults)
        """
        self.hass = hass
        self._entry = entry
        self._hub_config = hub_config
        
        # Core identification
        self.entry_id = entry.entry_id
        self.name = entry.data.get(CONF_REMINDER_NAME, entry.title)
        
        # State tracking (persisted in entry data)
        self._state = {
            STATE_LAST_PROMPT: None,
            STATE_LAST_DONE: None,
            STATE_RETRIES_TODAY: 0,
            STATE_ESCALATED: False,
            STATE_ESCALATIONS_TODAY: 0,
            STATE_AUTO_SKIPPED: False,
        }
        
        # Control flags
        self._enabled = True
        self._started = False
        
        # Timer and event listeners
        self._timer_remove = None
        self._presence_remove = []
        
        # Apply configuration from entry
        self._apply_config(entry.data)
        
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
        
        # Save final state
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
        """Load persisted state from entry data."""
        state_data = self._entry.data.get("state", {})
        
        if state_data:
            self._state.update(state_data)
            _LOGGER.debug("Loaded state for %s: %s", self.name, self._state)
        else:
            _LOGGER.debug("No persisted state for %s", self.name)

    async def _save_state(self) -> None:
        """Save current state to entry data."""
        new_data = {
            **self._entry.data,
            "state": self._state,
        }
        
        self.hass.config_entries.async_update_entry(
            self._entry,
            data=new_data,
        )
        
        _LOGGER.debug("Saved state for %s: %s", self.name, self._state)

    # ────────────────────────────────────────────────────────────────────────────
    # Timer Tick Logic
    # ────────────────────────────────────────────────────────────────────────────

    @callback
    async def _on_timer_tick(self, now: datetime) -> None:
        """Handle timer tick (runs every minute)."""
        if not self._enabled:
            return
        
        # Reset daily state at midnight
        await self._check_daily_reset(now)
        
        # Check if reminder is due for prompting
        if self._is_due(now):
            await self._handle_due_reminder(now)

    async def _check_daily_reset(self, now: datetime) -> None:
        """Reset daily counters at midnight."""
        today = now.date().isoformat()
        last_done = self._state[STATE_LAST_DONE]
        
        # If last_done is from a different day, reset counters
        if last_done and last_done != today:
            self._state[STATE_RETRIES_TODAY] = 0
            self._state[STATE_ESCALATED] = False
            self._state[STATE_ESCALATIONS_TODAY] = 0
            self._state[STATE_AUTO_SKIPPED] = False
            await self._save_state()

    def _is_due(self, now: datetime) -> bool:
        """Check if reminder is currently due for prompting."""
        today = now.date().isoformat()
        
        # Already done or auto-skipped today?
        if self._state[STATE_LAST_DONE] == today or self._state[STATE_AUTO_SKIPPED]:
            # Check if auto-skip should be cleared (past earliest retry time)
            if self._state[STATE_AUTO_SKIPPED]:
                if self._past_earliest_retry_time(now):
                    # New day started after earliest retry time, clear auto-skip
                    return False
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
        # Parse schedule time
        hour, minute = map(int, self.schedule_time.split(":"))
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
        
        return False

    def _is_scheduled_monthly(self, now: datetime) -> bool:
        """Check if current date matches monthly schedule."""
        if self.schedule_monthly_type == "day":
            # Specific day of month (1-31)
            return now.day == self.schedule_monthly_day
        
        elif self.schedule_monthly_type == "week_pattern":
            # Week pattern (e.g., "first Wednesday")
            target_weekday = WEEKDAYS.index(self.schedule_monthly_weekday)
            
            # Get all instances of target weekday in this month
            year, month = now.year, now.month
            cal = calendar.monthcalendar(year, month)
            
            # Find weeks containing target weekday
            weeks_with_day = [week for week in cal if week[target_weekday] != 0]
            
            if self.schedule_monthly_week == "last":
                target_day = weeks_with_day[-1][target_weekday]
            else:
                # first, second, third, fourth
                week_idx = MONTHLY_WEEKS.index(self.schedule_monthly_week)
                if week_idx < len(weeks_with_day):
                    target_day = weeks_with_day[week_idx][target_weekday]
                else:
                    return False  # Pattern doesn't exist this month
            
            return now.day == target_day
        
        return False

    def _in_quiet_hours(self, now: datetime) -> bool:
        """Check if current time is in quiet hours."""
        if not self.quiet_start or not self.quiet_end:
            return False
        
        start_hour, start_min = map(int, self.quiet_start.split(":"))
        end_hour, end_min = map(int, self.quiet_end.split(":"))
        
        start_time = dt_time(start_hour, start_min)
        end_time = dt_time(end_hour, end_min)
        current_time = now.time()
        
        # Handle quiet hours that wrap midnight
        if end_time > start_time:
            return start_time <= current_time < end_time
        else:
            return current_time >= start_time or current_time < end_time

    def _presence_satisfied(self) -> bool:
        """Check if presence requirements are satisfied."""
        if not self.presence_sensors:
            return True
        
        states = [self.hass.states.get(e) for e in self.presence_sensors]
        home_states = [s.state == STATE_ON for s in states if s]
        
        if not home_states:
            return False
        
        # At least one person home
        return any(home_states)

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
        hour, minute = map(int, self.earliest_retry_time.split(":"))
        earliest = dt_time(hour, minute)
        return now.time() >= earliest

    # ────────────────────────────────────────────────────────────────────────────
    # Due Reminder Handling
    # ────────────────────────────────────────────────────────────────────────────

    async def _handle_due_reminder(self, now: datetime) -> None:
        """Handle a reminder that is due for prompting."""
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
        alexa_device = (
            self.alexa_devices[0]
            if self.alexa_devices
            else "media_player.living_room_echo"
        )
        severity = "CRITICAL" if self._state[STATE_ESCALATED] else "TIME-SENSITIVE"
        data = {
            "method": "all",
            "who": "all",
            "alexa_device": alexa_device,
            "alert_volume": volume,
            "severity": severity,
            "title": "🔔 Reminder",
            "message": message,
            "tag": f"ar_{self.entry_id}",
        }
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

    @callback
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
        """Force an immediate prompt."""
        _LOGGER.info("Forcing prompt for reminder: %s", self.name)
        await self._send_prompt(dt_util.now())

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
