"""Field-level edits to a reminder's stored config.

The wizard is the only way to change a reminder that HA gives you for free, and
it is a five-screen walk that always rewrites the whole object. That leaves
automations, scripts and voice with no way to move a due date, retarget an
accumulator, or flip a reminder to mandatory — every such change had to be done
by hand, in the UI, one reminder at a time.

`build_updated_config` is the merge behind the `update_reminder` service: it
takes the reminder's current config and a sparse set of changes and returns the
config that should replace it. Everything not named is carried through
untouched, so a caller that only wants to move `time` cannot accidentally drop
the reminder's presence sensors or quiet hours.

Kept free of Home Assistant imports on purpose — the merge is where the sharp
edges live (switching schedule type has to drop the previous type's keys, or a
reminder ends up carrying two contradictory schedules), so it is worth being
able to test it without a running HA.
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, time as time_cls
from typing import Any

from .const import (
    CONF_REMINDER_NAME,
    CONF_ENABLED,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_TIME,
    CONF_ONCE_DATE,
    CONF_ANNIVERSARY_DATE,
    CONF_DUE_TEMPLATE,
    CONF_CONDITION_MODE,
    CONF_ACCUM_SOURCE,
    CONF_ACCUM_LIMIT,
    CONF_ACCUM_RESET_ON_DONE,
    CONF_THRESHOLD_ENTITY,
    CONF_THRESHOLD_BELOW,
    CONF_THRESHOLD_ABOVE,
    CONF_THRESHOLD_HYSTERESIS,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_MONTHLY_TYPE,
    CONF_SCHEDULE_MONTHLY_DAY,
    CONF_SCHEDULE_MONTHLY_WEEK,
    CONF_SCHEDULE_MONTHLY_WEEKDAY,
    CONF_INTERVAL_EVERY,
    CONF_INTERVAL_UNIT,
    CONF_INTERVAL_ANCHOR,
    CONF_PROMPT_MESSAGES,
    CONF_MOBILE_SERVICE,
    CONF_ALEXA_DEVICES,
    CONF_ACTIONABLE,
    CONF_ESCALATION_VOLUME,
    CONF_RETRY_INTERVAL,
    CONF_MAX_RETRIES,
    CONF_ESCALATION_INTERVAL,
    CONF_MAX_ESCALATIONS,
    CONF_RESPONSE_WINDOW,
    CONF_NAG_MIN_GAP,
    CONF_NAG_MAX_GAP,
    CONF_NAG_FRACTION,
    CONF_PRESENCE_SENSORS,
    CONF_CATCHUP_ON_ARRIVAL,
    CONF_QUIET_START,
    CONF_QUIET_END,
    CONF_UNTIL_DONE,
    CONF_LEAD_TIMES,
    CONF_ALLOW_CRITICAL,
    CONF_ANNOUNCE_WHEN_AWAY,
    CONF_NAG,
    CONF_MANDATORY,
    DEFAULT_SCHEDULE_TIME,
    MONTHLY_WEEKS,
    WEEKDAYS,
)

# The schedule types the wizard writes. Legacy stored types (daily/weekly/
# monthly/interval/yearly-as-schedule) are still readable — an update that does
# not touch schedule_type leaves whatever is there alone — but a caller can only
# switch INTO one of these.
SCHEDULE_TYPES = ("once", "repeating", "yearly", "condition")
INTERVAL_UNITS = ("days", "weeks", "months", "years")
CONDITION_MODES = ("template", "accumulator", "threshold")

# Every key that describes "when is this due", grouped by the type that owns it.
# Switching type drops all of them and rebuilds only the new type's — otherwise
# a reminder that used to be a template condition keeps its due_template, and
# the next reader has two answers to the same question.
_ONCE_KEYS = (CONF_ONCE_DATE,)
_YEARLY_KEYS = (CONF_ANNIVERSARY_DATE,)
_REPEATING_KEYS = (
    CONF_INTERVAL_EVERY,
    CONF_INTERVAL_UNIT,
    CONF_INTERVAL_ANCHOR,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_MONTHLY_TYPE,
    CONF_SCHEDULE_MONTHLY_DAY,
    CONF_SCHEDULE_MONTHLY_WEEK,
    CONF_SCHEDULE_MONTHLY_WEEKDAY,
)
_CONDITION_KEYS = (
    CONF_CONDITION_MODE,
    CONF_DUE_TEMPLATE,
    CONF_ACCUM_SOURCE,
    CONF_ACCUM_LIMIT,
    CONF_ACCUM_RESET_ON_DONE,
    CONF_THRESHOLD_ENTITY,
    CONF_THRESHOLD_BELOW,
    CONF_THRESHOLD_ABOVE,
    CONF_THRESHOLD_HYSTERESIS,
)
ALL_SCHEDULE_KEYS = frozenset(
    _ONCE_KEYS + _YEARLY_KEYS + _REPEATING_KEYS + _CONDITION_KEYS
)

_ACCUM_KEYS = (CONF_ACCUM_SOURCE, CONF_ACCUM_LIMIT, CONF_ACCUM_RESET_ON_DONE)
_THRESHOLD_KEYS = (
    CONF_THRESHOLD_ENTITY,
    CONF_THRESHOLD_BELOW,
    CONF_THRESHOLD_ABOVE,
    CONF_THRESHOLD_HYSTERESIS,
)

# Service field -> stored config key. Most names match already; the ones that
# don't are the friendly names create_reminder established, kept identical here
# so the two services read the same way.
FIELD_MAP: dict[str, str] = {
    "name": CONF_REMINDER_NAME,
    "enabled": CONF_ENABLED,
    "time": CONF_SCHEDULE_TIME,
    "every": CONF_INTERVAL_EVERY,
    "unit": CONF_INTERVAL_UNIT,
    "anchor": CONF_INTERVAL_ANCHOR,
    "weekdays": CONF_SCHEDULE_DAYS,
    "monthly_day": CONF_SCHEDULE_MONTHLY_DAY,
    "monthly_week": CONF_SCHEDULE_MONTHLY_WEEK,
    "monthly_weekday": CONF_SCHEDULE_MONTHLY_WEEKDAY,
    "condition_mode": CONF_CONDITION_MODE,
    "due_template": CONF_DUE_TEMPLATE,
    "accumulator_source": CONF_ACCUM_SOURCE,
    "accumulator_limit": CONF_ACCUM_LIMIT,
    "accumulator_reset_on_done": CONF_ACCUM_RESET_ON_DONE,
    "threshold_entity": CONF_THRESHOLD_ENTITY,
    "threshold_below": CONF_THRESHOLD_BELOW,
    "threshold_above": CONF_THRESHOLD_ABOVE,
    "threshold_hysteresis": CONF_THRESHOLD_HYSTERESIS,
    "mandatory": CONF_MANDATORY,
    "nag": CONF_NAG,
    "until_done": CONF_UNTIL_DONE,
    "allow_critical": CONF_ALLOW_CRITICAL,
    "announce_when_away": CONF_ANNOUNCE_WHEN_AWAY,
    "lead_times": CONF_LEAD_TIMES,
    "actionable": CONF_ACTIONABLE,
    "mobile_service": CONF_MOBILE_SERVICE,
    "alexa_devices": CONF_ALEXA_DEVICES,
    "escalation_volume": CONF_ESCALATION_VOLUME,
    "retry_interval": CONF_RETRY_INTERVAL,
    "max_retries": CONF_MAX_RETRIES,
    "escalation_interval": CONF_ESCALATION_INTERVAL,
    "max_escalations": CONF_MAX_ESCALATIONS,
    "response_window": CONF_RESPONSE_WINDOW,
    "nag_min_gap": CONF_NAG_MIN_GAP,
    "nag_max_gap": CONF_NAG_MAX_GAP,
    "nag_fraction": CONF_NAG_FRACTION,
    "presence_sensors": CONF_PRESENCE_SENSORS,
    "catchup_on_arrival": CONF_CATCHUP_ON_ARRIVAL,
    "quiet_start": CONF_QUIET_START,
    "quiet_end": CONF_QUIET_END,
}

# Fields `clear` may remove, sending them back to the hub default. Deliberately
# only the optional overrides: clearing a schedule field would leave a reminder
# that cannot say when it is due, which is not a state worth being able to
# reach from a service call.
CLEARABLE = frozenset({
    "mobile_service",
    "alexa_devices",
    "presence_sensors",
    "quiet_start",
    "quiet_end",
    "retry_interval",
    "max_retries",
    "escalation_interval",
    "max_escalations",
    "response_window",
    "nag_min_gap",
    "nag_max_gap",
    "nag_fraction",
    "escalation_volume",
    "lead_times",
    "weekdays",
    "threshold_below",
    "threshold_above",
    "threshold_hysteresis",
})

# Fields stored as an ISO date / an HH:MM string, whatever type they arrive as.
_DATE_FIELDS = ("anchor", "date")
_TIME_FIELDS = ("time", "quiet_start", "quiet_end")


def _as_date_str(value: Any) -> str:
    """Normalize a date (object or string) to YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date_cls):
        return value.isoformat()
    return str(value)


def _as_time_str(value: Any) -> str:
    """Normalize a time (object or string) to HH:MM.

    Both forms are already in the store — the wizard's TimeSelector writes
    HH:MM:SS, create_reminder writes HH:MM — and the engine parses either, so
    this only has to be one of them consistently.
    """
    if isinstance(value, (datetime, time_cls)):
        return value.strftime("%H:%M")
    return str(value)


def _parse_iso_date(value: Any) -> date_cls | None:
    try:
        return date_cls.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def build_updated_config(
    current: dict[str, Any], changes: dict[str, Any], today: date_cls
) -> dict[str, Any]:
    """Merge `changes` onto a reminder's `current` config.

    Args:
        current: the reminder subentry's stored data.
        changes: sparse service data, keyed by service field name (no entry_id).
        today: the date to anchor a new repeating schedule to when none is given.

    Returns:
        The complete config to store. Unmentioned keys are carried through.

    Raises:
        ValueError: the requested combination cannot describe a reminder (e.g.
            switching to a one-time reminder without saying which date).
    """
    cfg = dict(current)

    # 1. Clears first, so setting and clearing the same field in one call
    #    resolves to the set — the more specific of the two intents.
    for field in changes.get("clear") or []:
        if field not in CLEARABLE:
            raise ValueError(
                f"'{field}' cannot be cleared. Clearable fields: "
                f"{', '.join(sorted(CLEARABLE))}"
            )
        cfg.pop(FIELD_MAP[field], None)

    # 2. Schedule type. Changing it drops the outgoing type's keys wholesale;
    #    keeping it (or not mentioning it) leaves them to be edited in place.
    old_type = cfg.get(CONF_SCHEDULE_TYPE)
    new_type = changes.get("schedule_type", old_type)
    if new_type is None:
        raise ValueError("Reminder has no schedule_type and none was given")
    if "schedule_type" in changes and new_type not in SCHEDULE_TYPES:
        raise ValueError(
            f"Unsupported schedule_type: {new_type} "
            f"(expected one of {', '.join(SCHEDULE_TYPES)})"
        )
    switching = new_type != old_type
    if switching:
        for key in ALL_SCHEDULE_KEYS:
            cfg.pop(key, None)
    cfg[CONF_SCHEDULE_TYPE] = new_type

    # 3. Straight field writes.
    for field, value in changes.items():
        if field not in FIELD_MAP:
            continue
        if field in _TIME_FIELDS:
            value = _as_time_str(value)
        elif field in _DATE_FIELDS:
            value = _as_date_str(value)
        cfg[FIELD_MAP[field]] = value

    # 4. Messages. `message` is the single-message shorthand the wizard and
    #    create_reminder use; `messages` is the rotating pool set_messages
    #    writes. Both land in the same key.
    if "messages" in changes:
        cfg[CONF_PROMPT_MESSAGES] = [str(m) for m in changes["messages"]]
    elif "message" in changes:
        cfg[CONF_PROMPT_MESSAGES] = [str(changes["message"])] if changes["message"] else []

    # 5. `date` means different things per type, so it is resolved after the
    #    type is known rather than mapped blindly.
    if "date" in changes:
        if new_type == "once":
            cfg[CONF_ONCE_DATE] = _as_date_str(changes["date"])
        elif new_type == "yearly":
            cfg[CONF_ANNIVERSARY_DATE] = _as_date_str(changes["date"])
        else:
            raise ValueError(
                f"'date' applies to once and yearly reminders; this one is "
                f"'{new_type}' (use 'anchor' for repeating)"
            )

    cfg.setdefault(CONF_SCHEDULE_TIME, DEFAULT_SCHEDULE_TIME)

    if new_type == "once":
        _finish_once(cfg)
    elif new_type == "yearly":
        _finish_yearly(cfg)
    elif new_type == "repeating":
        _finish_repeating(cfg, changes, today)
    elif new_type == "condition":
        _finish_condition(cfg)

    return cfg


def _finish_once(cfg: dict[str, Any]) -> None:
    if not cfg.get(CONF_ONCE_DATE):
        raise ValueError("A one-time reminder needs 'date'")


def _finish_yearly(cfg: dict[str, Any]) -> None:
    if not cfg.get(CONF_ANNIVERSARY_DATE):
        raise ValueError("A yearly reminder needs 'date' (the anniversary)")


def _finish_repeating(
    cfg: dict[str, Any], changes: dict[str, Any], today: date_cls
) -> None:
    """Normalize the every/unit/anchor block and the unit's own detail.

    The weekday list belongs to weeks and the monthly pattern to months, so a
    unit change has to take the other one's keys with it. Leaving them behind is
    how a reminder switched from weeks to months keeps firing on Saturdays.
    """
    unit = cfg.get(CONF_INTERVAL_UNIT) or "weeks"
    if unit not in INTERVAL_UNITS:
        raise ValueError(
            f"Unsupported unit: {unit} (expected one of {', '.join(INTERVAL_UNITS)})"
        )
    cfg[CONF_INTERVAL_UNIT] = unit

    try:
        every = int(cfg.get(CONF_INTERVAL_EVERY, 1))
    except (TypeError, ValueError):
        every = 1
    if every < 1:
        raise ValueError("'every' must be at least 1")
    cfg[CONF_INTERVAL_EVERY] = every

    anchor = cfg.get(CONF_INTERVAL_ANCHOR) or today.isoformat()
    cfg[CONF_INTERVAL_ANCHOR] = _as_date_str(anchor)

    if unit != "weeks":
        cfg.pop(CONF_SCHEDULE_DAYS, None)
    else:
        days = cfg.get(CONF_SCHEDULE_DAYS)
        if days is None:
            cfg[CONF_SCHEDULE_DAYS] = []
        else:
            if isinstance(days, str):
                days = [days]
            bad = [d for d in days if d not in WEEKDAYS]
            if bad:
                raise ValueError(f"Unknown weekday(s): {', '.join(map(str, bad))}")
            cfg[CONF_SCHEDULE_DAYS] = list(days)

    if unit != "months":
        for key in (
            CONF_SCHEDULE_MONTHLY_TYPE,
            CONF_SCHEDULE_MONTHLY_DAY,
            CONF_SCHEDULE_MONTHLY_WEEK,
            CONF_SCHEDULE_MONTHLY_WEEKDAY,
        ):
            cfg.pop(key, None)
        return

    # Months: a specific day-of-month, or a week pattern. Whichever the caller
    # named this time wins; otherwise keep what the reminder already used.
    if "monthly_day" in changes:
        mtype = "day"
    elif "monthly_week" in changes or "monthly_weekday" in changes:
        mtype = "week_pattern"
    else:
        mtype = cfg.get(CONF_SCHEDULE_MONTHLY_TYPE)
        if mtype not in ("day", "week_pattern"):
            mtype = "day"
    cfg[CONF_SCHEDULE_MONTHLY_TYPE] = mtype

    if mtype == "day":
        cfg.pop(CONF_SCHEDULE_MONTHLY_WEEK, None)
        cfg.pop(CONF_SCHEDULE_MONTHLY_WEEKDAY, None)
        anchor_date = _parse_iso_date(cfg[CONF_INTERVAL_ANCHOR])
        day = cfg.get(CONF_SCHEDULE_MONTHLY_DAY)
        if day is None:
            day = anchor_date.day if anchor_date else 1
        day = int(day)
        if not 1 <= day <= 31:
            raise ValueError("'monthly_day' must be between 1 and 31")
        cfg[CONF_SCHEDULE_MONTHLY_DAY] = day
        return

    cfg.pop(CONF_SCHEDULE_MONTHLY_DAY, None)
    weeks = cfg.get(CONF_SCHEDULE_MONTHLY_WEEK) or ["first"]
    if isinstance(weeks, str):
        weeks = [weeks]
    bad = [w for w in weeks if w not in MONTHLY_WEEKS]
    if bad:
        raise ValueError(f"Unknown week pattern(s): {', '.join(map(str, bad))}")
    cfg[CONF_SCHEDULE_MONTHLY_WEEK] = list(weeks)
    weekday = cfg.get(CONF_SCHEDULE_MONTHLY_WEEKDAY) or "mon"
    if weekday not in WEEKDAYS:
        raise ValueError(f"Unknown weekday: {weekday}")
    cfg[CONF_SCHEDULE_MONTHLY_WEEKDAY] = weekday


def _finish_condition(cfg: dict[str, Any]) -> None:
    """Keep only the active condition mode's keys, and insist it is complete.

    A half-configured condition is the one failure that looks like nothing at
    all: the reminder loads, the switch appears, and it is simply never due.
    """
    mode = cfg.get(CONF_CONDITION_MODE) or "template"
    if mode not in CONDITION_MODES:
        raise ValueError(
            f"Unsupported condition_mode: {mode} "
            f"(expected one of {', '.join(CONDITION_MODES)})"
        )
    cfg[CONF_CONDITION_MODE] = mode

    if mode == "template":
        for key in _ACCUM_KEYS + _THRESHOLD_KEYS:
            cfg.pop(key, None)
        if not cfg.get(CONF_DUE_TEMPLATE):
            raise ValueError("A template condition needs 'due_template'")
    elif mode == "accumulator":
        cfg.pop(CONF_DUE_TEMPLATE, None)
        for key in _THRESHOLD_KEYS:
            cfg.pop(key, None)
        if not cfg.get(CONF_ACCUM_SOURCE):
            raise ValueError("An accumulator condition needs 'accumulator_source'")
        if cfg.get(CONF_ACCUM_LIMIT) in (None, ""):
            raise ValueError("An accumulator condition needs 'accumulator_limit'")
        cfg.setdefault(CONF_ACCUM_RESET_ON_DONE, True)
    else:  # threshold
        cfg.pop(CONF_DUE_TEMPLATE, None)
        for key in _ACCUM_KEYS:
            cfg.pop(key, None)
        if not cfg.get(CONF_THRESHOLD_ENTITY):
            raise ValueError("A threshold condition needs 'threshold_entity'")
        if cfg.get(CONF_THRESHOLD_BELOW) in (None, "") and cfg.get(
            CONF_THRESHOLD_ABOVE
        ) in (None, ""):
            raise ValueError(
                "A threshold condition needs 'threshold_below' or 'threshold_above'"
            )
