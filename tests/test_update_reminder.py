"""An edit changes what you named and nothing else.

`update_reminder` merges sparse changes onto a stored reminder. The failure
that matters is not a rejected call — it is a call that quietly takes something
with it: a unit change that leaves last month's weekday list behind, a switch to
a one-time reminder that keeps the old due_template, an edit to the prompt time
that drops the presence sensors. Those all still load, still show a switch, and
are wrong only at the hour the reminder was supposed to fire.

So these pin the carry-through and the strips, not the happy path.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from conftest import const
import importlib

rc = importlib.import_module("ar.reminder_config")
build = rc.build_updated_config

TODAY = date(2026, 9, 1)


def weekly(**extra):
    """A repeating reminder with a full set of overrides to protect."""
    cfg = {
        const.CONF_REMINDER_NAME: "Water the ferns",
        const.CONF_SCHEDULE_TYPE: "repeating",
        const.CONF_SCHEDULE_TIME: "09:00",
        const.CONF_INTERVAL_EVERY: 2,
        const.CONF_INTERVAL_UNIT: "weeks",
        const.CONF_INTERVAL_ANCHOR: "2026-07-04",
        const.CONF_SCHEDULE_DAYS: ["sat"],
        const.CONF_PROMPT_MESSAGES: ["The ferns need water."],
        const.CONF_PRESENCE_SENSORS: ["binary_sensor.someone_home"],
        const.CONF_QUIET_START: "22:00",
        const.CONF_RETRY_INTERVAL: 45,
    }
    cfg.update(extra)
    return cfg


# ── carry-through ─────────────────────────────────────────────────────────────

def test_an_unnamed_field_is_carried_through():
    out = build(weekly(), {"time": time(7, 30)}, TODAY)
    assert out[const.CONF_SCHEDULE_TIME] == "07:30"
    # Everything the caller did not mention survives verbatim.
    for key in (
        const.CONF_PRESENCE_SENSORS,
        const.CONF_QUIET_START,
        const.CONF_RETRY_INTERVAL,
        const.CONF_PROMPT_MESSAGES,
        const.CONF_SCHEDULE_DAYS,
        const.CONF_INTERVAL_ANCHOR,
    ):
        assert out[key] == weekly()[key]


def test_the_input_is_not_mutated():
    current = weekly()
    build(current, {"name": "Ferns"}, TODAY)
    assert current[const.CONF_REMINDER_NAME] == "Water the ferns"


def test_a_date_or_time_object_is_stored_as_a_string():
    out = build(weekly(), {"anchor": date(2026, 10, 5), "time": time(6, 5)}, TODAY)
    assert out[const.CONF_INTERVAL_ANCHOR] == "2026-10-05"
    assert out[const.CONF_SCHEDULE_TIME] == "06:05"


# ── switching schedule type ───────────────────────────────────────────────────

def test_switching_type_drops_the_old_types_schedule_keys():
    out = build(weekly(), {"schedule_type": "once", "date": "2026-09-15"}, TODAY)
    assert out[const.CONF_SCHEDULE_TYPE] == "once"
    assert out[const.CONF_ONCE_DATE] == "2026-09-15"
    # The weekly schedule is gone, not merely ignored.
    for key in (
        const.CONF_INTERVAL_EVERY,
        const.CONF_INTERVAL_UNIT,
        const.CONF_INTERVAL_ANCHOR,
        const.CONF_SCHEDULE_DAYS,
    ):
        assert key not in out
    # ...but the reminder's identity and delivery settings are not schedule.
    assert out[const.CONF_PRESENCE_SENSORS] == ["binary_sensor.someone_home"]
    assert out[const.CONF_RETRY_INTERVAL] == 45


def test_switching_away_from_a_condition_drops_its_template():
    current = {
        const.CONF_REMINDER_NAME: "Refill softener",
        const.CONF_SCHEDULE_TYPE: "condition",
        const.CONF_CONDITION_MODE: "template",
        const.CONF_DUE_TEMPLATE: "{{ is_state('binary_sensor.low','on') }}",
    }
    out = build(current, {"schedule_type": "repeating", "unit": "months"}, TODAY)
    assert const.CONF_DUE_TEMPLATE not in out
    assert const.CONF_CONDITION_MODE not in out
    # A repeating schedule with nothing said about it still has to be complete.
    assert out[const.CONF_INTERVAL_EVERY] == 1
    assert out[const.CONF_INTERVAL_ANCHOR] == TODAY.isoformat()


def test_switching_to_once_without_a_date_is_refused():
    with pytest.raises(ValueError, match="needs 'date'"):
        build(weekly(), {"schedule_type": "once"}, TODAY)


def test_a_legacy_schedule_type_is_left_alone_when_not_mentioned():
    # Reminders written before the unified wizard still store daily/weekly/
    # monthly. Editing the time of one must not force it through a migration.
    current = {
        const.CONF_SCHEDULE_TYPE: "weekly",
        const.CONF_SCHEDULE_DAYS: ["mon"],
        const.CONF_SCHEDULE_TIME: "09:00",
    }
    out = build(current, {"time": "18:00"}, TODAY)
    assert out[const.CONF_SCHEDULE_TYPE] == "weekly"
    assert out[const.CONF_SCHEDULE_DAYS] == ["mon"]


def test_an_unknown_schedule_type_is_refused():
    with pytest.raises(ValueError, match="Unsupported schedule_type"):
        build(weekly(), {"schedule_type": "fortnightly"}, TODAY)


# ── unit changes inside a repeating reminder ──────────────────────────────────

def test_changing_the_unit_drops_the_previous_units_detail():
    out = build(weekly(), {"unit": "months"}, TODAY)
    # Saturdays belonged to the weekly form; keeping them is how a monthly
    # reminder ends up still firing on a weekday nobody asked for.
    assert const.CONF_SCHEDULE_DAYS not in out
    assert out[const.CONF_SCHEDULE_MONTHLY_TYPE] == "day"
    # No day given, so it takes the anchor's — 2026-07-04.
    assert out[const.CONF_SCHEDULE_MONTHLY_DAY] == 4


def test_a_monthly_week_pattern_replaces_the_day_of_month():
    current = build(weekly(), {"unit": "months", "monthly_day": 15}, TODAY)
    out = build(current, {"monthly_week": ["first", "third"], "monthly_weekday": "wed"}, TODAY)
    assert out[const.CONF_SCHEDULE_MONTHLY_TYPE] == "week_pattern"
    assert out[const.CONF_SCHEDULE_MONTHLY_WEEK] == ["first", "third"]
    assert out[const.CONF_SCHEDULE_MONTHLY_WEEKDAY] == "wed"
    assert const.CONF_SCHEDULE_MONTHLY_DAY not in out


def test_a_day_of_month_replaces_the_week_pattern():
    current = build(
        weekly(), {"unit": "months", "monthly_week": "last", "monthly_weekday": "fri"}, TODAY
    )
    out = build(current, {"monthly_day": 1}, TODAY)
    assert out[const.CONF_SCHEDULE_MONTHLY_TYPE] == "day"
    assert out[const.CONF_SCHEDULE_MONTHLY_DAY] == 1
    assert const.CONF_SCHEDULE_MONTHLY_WEEK not in out
    assert const.CONF_SCHEDULE_MONTHLY_WEEKDAY not in out


def test_going_back_to_days_drops_the_monthly_pattern():
    monthly = build(weekly(), {"unit": "months", "monthly_day": 15}, TODAY)
    out = build(monthly, {"unit": "days", "every": 3}, TODAY)
    for key in (
        const.CONF_SCHEDULE_MONTHLY_TYPE,
        const.CONF_SCHEDULE_MONTHLY_DAY,
        const.CONF_SCHEDULE_DAYS,
    ):
        assert key not in out
    assert out[const.CONF_INTERVAL_EVERY] == 3


def test_an_unknown_weekday_is_refused():
    with pytest.raises(ValueError, match="Unknown weekday"):
        build(weekly(), {"weekdays": ["sat", "caturday"]}, TODAY)


# ── condition modes ───────────────────────────────────────────────────────────

def condition(mode="template", **extra):
    cfg = {
        const.CONF_REMINDER_NAME: "A/C filter",
        const.CONF_SCHEDULE_TYPE: "condition",
        const.CONF_CONDITION_MODE: mode,
    }
    cfg.update(extra)
    return cfg


def test_switching_condition_mode_drops_the_other_modes_keys():
    current = condition(
        "accumulator",
        **{
            const.CONF_ACCUM_SOURCE: "sensor.hvac_runtime",
            const.CONF_ACCUM_LIMIT: 300,
            const.CONF_ACCUM_RESET_ON_DONE: True,
        },
    )
    out = build(
        current,
        {"condition_mode": "threshold", "threshold_entity": "sensor.filter_life", "threshold_below": 10},
        TODAY,
    )
    assert out[const.CONF_THRESHOLD_ENTITY] == "sensor.filter_life"
    for key in (const.CONF_ACCUM_SOURCE, const.CONF_ACCUM_LIMIT, const.CONF_ACCUM_RESET_ON_DONE):
        assert key not in out


def test_retargeting_an_accumulator_keeps_the_rest_of_the_mode():
    current = condition(
        "accumulator",
        **{
            const.CONF_ACCUM_SOURCE: "sensor.old_runtime",
            const.CONF_ACCUM_LIMIT: 300,
            const.CONF_ACCUM_RESET_ON_DONE: False,
        },
    )
    out = build(current, {"accumulator_source": "sensor.new_runtime"}, TODAY)
    assert out[const.CONF_ACCUM_SOURCE] == "sensor.new_runtime"
    assert out[const.CONF_ACCUM_LIMIT] == 300
    assert out[const.CONF_ACCUM_RESET_ON_DONE] is False


def test_a_half_configured_condition_is_refused():
    # The one failure that looks like nothing at all: it loads, and is never due.
    with pytest.raises(ValueError, match="accumulator_limit"):
        build(condition(), {"condition_mode": "accumulator", "accumulator_source": "sensor.x"}, TODAY)
    with pytest.raises(ValueError, match="threshold_below"):
        build(condition(), {"condition_mode": "threshold", "threshold_entity": "sensor.x"}, TODAY)
    with pytest.raises(ValueError, match="due_template"):
        build(condition(), {"time": "10:00"}, TODAY)


# ── messages ──────────────────────────────────────────────────────────────────

def test_message_and_messages_write_the_same_key():
    assert build(weekly(), {"message": "Ferns are thirsty."}, TODAY)[
        const.CONF_PROMPT_MESSAGES
    ] == ["Ferns are thirsty."]
    assert build(weekly(), {"messages": ["One.", "Two."]}, TODAY)[
        const.CONF_PROMPT_MESSAGES
    ] == ["One.", "Two."]


def test_an_empty_message_empties_the_pool():
    out = build(weekly(), {"message": ""}, TODAY)
    assert out[const.CONF_PROMPT_MESSAGES] == []


# ── clearing ──────────────────────────────────────────────────────────────────

def test_clear_sends_an_override_back_to_the_hub_default():
    out = build(weekly(), {"clear": ["retry_interval", "presence_sensors"]}, TODAY)
    assert const.CONF_RETRY_INTERVAL not in out
    assert const.CONF_PRESENCE_SENSORS not in out
    assert out[const.CONF_QUIET_START] == "22:00"


def test_setting_a_field_beats_clearing_it_in_the_same_call():
    out = build(weekly(), {"clear": ["retry_interval"], "retry_interval": 10}, TODAY)
    assert out[const.CONF_RETRY_INTERVAL] == 10


def test_a_schedule_field_cannot_be_cleared():
    # Clearing 'anchor' would leave a repeating reminder that cannot say when
    # it is due — reachable from the wizard only by deleting the reminder.
    with pytest.raises(ValueError, match="cannot be cleared"):
        build(weekly(), {"clear": ["anchor"]}, TODAY)
