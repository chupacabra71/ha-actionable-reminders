"""Shared constants for Actionable Reminders.

This module defines all configuration keys and default values used throughout
the integration. Constants are organized by scope:
- Integration-level (domain, entry type)
- Hub-level (global defaults)
- Per-reminder (individual reminder configuration)
- State tracking (runtime state)
- Default values
"""

# ═══════════════════════════════════════════════════════════════════════════════
# Integration Identifiers
# ═══════════════════════════════════════════════════════════════════════════════

DOMAIN = "actionable_reminders"
CONF_TYPE_HUB = "hub"                     # Hub entry type
CONF_TYPE_REMINDER = "reminder"           # Individual reminder type (legacy standalone entry)
SUBENTRY_TYPE_REMINDER = "reminder"       # Reminder subentry type (current model)


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatcher Signals
# ═══════════════════════════════════════════════════════════════════════════════

# Dispatcher signals (use .format(entry_id) when dispatching)
SIGNAL_REMINDER_UPDATE = f"{DOMAIN}_reminder_update_{{}}"

# Fired (no args) when the set of reminders changes or any reminder updates, so
# aggregate entities (the to-do list) can re-render.
SIGNAL_REMINDERS_UPDATED = f"{DOMAIN}_reminders_updated"

# Fired on the bus when any reminder is marked done: {entry_id, name}. Lets
# external automations react to completion (e.g. reset an HVAC-filter counter).
EVENT_COMPLETED = f"{DOMAIN}_completed"

# Calendar source
CONF_REMINDERS_CALENDAR = "reminders_calendar"                   # Calendar entity whose events become reminders
SERVICE_CALENDAR_ACK = "calendar_ack"                            # Ack a calendar-sourced reminder (data: event_key)

# Master switch — global kill switch for ALL reminder/calendar notifications
CONF_MASTER_ENABLED = "master_enabled"                           # Hub-level: when False, nothing prompts
SIGNAL_MASTER_UPDATED = f"{DOMAIN}_master_updated"               # Fired when the master switch toggles


# ═══════════════════════════════════════════════════════════════════════════════
# Hub-Level Configuration (Global Defaults)
# ═══════════════════════════════════════════════════════════════════════════════

# Retry and escalation defaults
CONF_DEFAULT_RETRY_INTERVAL = "default_retry_interval"           # Minutes between retries
CONF_DEFAULT_MAX_RETRIES = "default_max_retries"                 # Retries before escalation
CONF_DEFAULT_ESCALATION_INTERVAL = "default_escalation_interval" # Minutes between escalated retries
CONF_DEFAULT_MAX_ESCALATIONS = "default_max_escalations"         # Escalations before auto-skip
CONF_EARLIEST_RETRY_TIME = "earliest_retry_time"                 # Time to restart after auto-skip (HH:MM)

# Notification defaults
CONF_DEFAULT_MOBILE_SERVICE = "default_mobile_service"           # Default notify service
CONF_DEFAULT_ALEXA_DEVICES = "default_alexa_devices"             # Default Alexa devices (list)
CONF_DEFAULT_ACTIONABLE = "default_actionable"                   # Default actionable flag

# Presence and quiet hours defaults
CONF_DEFAULT_PRESENCE_SENSORS = "default_presence_sensors"       # Default presence sensors (list)
CONF_DEFAULT_QUIET_START = "default_quiet_start"                 # Default quiet hours start (HH:MM)
CONF_DEFAULT_QUIET_END = "default_quiet_end"                     # Default quiet hours end (HH:MM)


# ═══════════════════════════════════════════════════════════════════════════════
# Per-Reminder Configuration
# ═══════════════════════════════════════════════════════════════════════════════

# Basic settings
CONF_REMINDER_NAME = "reminder_name"                             # Display name
CONF_ENABLED = "enabled"                                         # Active flag

# Schedule settings
CONF_SCHEDULE_TYPE = "schedule_type"                             # "daily", "weekly", "monthly"
CONF_SCHEDULE_TIME = "schedule_time"                             # Time (HH:MM)
CONF_ONCE_DATE = "once_date"                                     # One-time: target date (YYYY-MM-DD)
CONF_ANNIVERSARY_DATE = "anniversary_date"                       # Yearly: date; MM-DD recurs, YYYY for age
CONF_DUE_TEMPLATE = "due_template"                               # Condition: due while this Jinja renders truthy
CONF_ON_COMPLETE = "on_complete"                                 # Native HA action sequence to run on completion
CONF_SCHEDULE_DAYS = "schedule_days"                             # Weekly: ["mon", "tue", ...]
CONF_SCHEDULE_MONTHLY_TYPE = "schedule_monthly_type"             # "day" or "week_pattern"
CONF_SCHEDULE_MONTHLY_DAY = "schedule_monthly_day"               # Day of month (1-31)
CONF_SCHEDULE_MONTHLY_WEEK = "schedule_monthly_week"             # "first", "second", "third", "fourth", "last"
CONF_SCHEDULE_MONTHLY_WEEKDAY = "schedule_monthly_weekday"       # "mon", "tue", etc.

# Messages
CONF_PROMPT_MESSAGES = "prompt_messages"                         # List of prompt messages
CONF_ACK_MESSAGES = "ack_messages"                               # List of acknowledgment messages
CONF_DISMISS_MESSAGES = "dismiss_messages"                       # List of dismissal messages

# Notification settings (can override hub defaults)
CONF_MOBILE_SERVICE = "mobile_service"                           # Mobile notify service
CONF_ALEXA_DEVICES = "alexa_devices"                             # Alexa devices (list)
CONF_ACTIONABLE = "actionable"                                   # Actionable flag
CONF_ESCALATION_VOLUME = "escalation_volume"                     # Volume during escalation (0.0-1.0)

# Retry and escalation (can override hub defaults)
CONF_RETRY_INTERVAL = "retry_interval"                           # Minutes between retries
CONF_MAX_RETRIES = "max_retries"                                 # Retries before escalation
CONF_ESCALATION_INTERVAL = "escalation_interval"                 # Minutes between escalated retries
CONF_MAX_ESCALATIONS = "max_escalations"                         # Escalations before auto-skip

# Presence and quiet hours (can override hub defaults)
CONF_PRESENCE_SENSORS = "presence_sensors"                       # Presence sensors (list)
CONF_CATCHUP_ON_ARRIVAL = "catchup_on_arrival"                   # Trigger when arriving home
CONF_QUIET_START = "quiet_start"                                 # Quiet hours start (HH:MM)
CONF_QUIET_END = "quiet_end"                                     # Quiet hours end (HH:MM)

# Behavior flags
CONF_OPTIONAL = "optional"                                       # Optional reminder (can auto-skip)
CONF_UNTIL_DONE = "until_done"                                   # Keep prompting until marked done
CONF_LEAD_TIMES = "lead_times"                                   # Pre-notification offsets (days before due)
CONF_ALLOW_CRITICAL = "allow_critical"  # Opt-in: escalate to DND-bypassing CRITICAL
CONF_NAG = "nag"                                               # Post-due nag-until-done (False = single announce)


# ═══════════════════════════════════════════════════════════════════════════════
# State Tracking (Runtime state stored in entry.data["state"])
# ═══════════════════════════════════════════════════════════════════════════════

STATE_LAST_PROMPT = "last_prompt_ts"                             # ISO timestamp of last prompt
STATE_LAST_DONE = "last_done_date"                               # Date last marked done (YYYY-MM-DD)
STATE_LAST_LEAD_DATE = "last_lead_date"                          # Date a lead-time announce last fired
STATE_RETRIES_TODAY = "retries_today"                            # Number of retry attempts today
STATE_ESCALATED = "escalated"                                    # Whether currently escalated
STATE_ESCALATIONS_TODAY = "escalations_today"                    # Number of escalations today
STATE_AUTO_SKIPPED = "auto_skipped"                              # Auto-skipped for today
STATE_RESET_DAY = "reset_day"                                    # Date (YYYY-MM-DD) daily counters last reset


# ═══════════════════════════════════════════════════════════════════════════════
# Default Values
# ═══════════════════════════════════════════════════════════════════════════════

# Hub defaults
DEFAULT_RETRY_INTERVAL = 30                                      # 30 minutes
DEFAULT_MAX_RETRIES = 5                                          # 5 retries before escalation
DEFAULT_ESCALATION_INTERVAL = 15                                 # 15 minutes during escalation
DEFAULT_MAX_ESCALATIONS = 5                                      # 5 escalations before auto-skip
DEFAULT_EARLIEST_RETRY_TIME = "10:00"                            # Restart at 10 AM after auto-skip
DEFAULT_ACTIONABLE = True                                        # Actionable by default
DEFAULT_MASTER_ENABLED = True                                    # Master switch on by default
DEFAULT_LEAD_TIMES = []                                          # No pre-notifications by default
DEFAULT_ALLOW_CRITICAL = False  # Never bypass DND unless explicitly opted in
DEFAULT_NAG = True                                             # Nag until done by default
DEFAULT_QUIET_START = "22:00"                                    # 10 PM
DEFAULT_QUIET_END = "08:00"                                      # 8 AM

# Per-reminder defaults
DEFAULT_SCHEDULE_TYPE = "daily"
DEFAULT_SCHEDULE_TIME = "09:00"
DEFAULT_ENABLED = True
DEFAULT_ESCALATION_VOLUME = 0.8                                  # 80% volume during escalation
DEFAULT_CATCHUP_ON_ARRIVAL = True
DEFAULT_OPTIONAL = False                                         # Mandatory by default
DEFAULT_UNTIL_DONE = True                                        # Keep prompting until done

# Message defaults
DEFAULT_PROMPT_MESSAGE = "Did you complete: {reminder_name}?"
DEFAULT_ACK_MESSAGES = [
    "Thank you!",
    "Got it, thanks!",
    "Great, marked as done!",
]
DEFAULT_DISMISS_MESSAGES = [
    "Okay, I'll remind you again soon.",
    "No problem, checking back later.",
    "Alright, talk to you in a bit.",
]

# Weekday mapping
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_LABELS = {
    "mon": "Monday",
    "tue": "Tuesday", 
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}

# Monthly week patterns
MONTHLY_WEEKS = ["first", "second", "third", "fourth", "last"]
MONTHLY_WEEK_LABELS = {
    "first": "First",
    "second": "Second",
    "third": "Third",
    "fourth": "Fourth",
    "last": "Last",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Services
# ═══════════════════════════════════════════════════════════════════════════════

SERVICE_MARK_DONE = "mark_done"
SERVICE_DISMISS = "dismiss"
SERVICE_SKIP_TODAY = "skip_today"
SERVICE_FORCE_PROMPT = "force_prompt"