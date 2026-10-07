"""A reminder that was snoozed or left unanswered must come back.

Pinned after "Hose Down A/C Units" was snoozed on 2026-09-23 at 16:46 and
silently checked off at 18:00 — its single condition template ended
`and 10 <= now().hour < 18`, and the self-resolve path read the window
closing as the chore being done. The 14-day clock restarted and nobody heard
about it again for two weeks. Each section below is one way a reminder could
be lost, re-fire wrongly, or have its state quietly corrupted.
"""

from __future__ import annotations

import asyncio
import types
from datetime import date, datetime, timedelta

import pytest

from conftest import const, dt_util, make_runner, reminder_mod

TUESDAY = date(2026, 8, 18)
SUNDAY = date(2026, 8, 16)
MONDAY = date(2026, 8, 17)

classify = reminder_mod.classify_voice_reply
parse_duration = reminder_mod.parse_spoken_duration
CONFIRM = reminder_mod.RESOLVE_CONFIRM


def run(coro):
    return asyncio.run(coro)


def at(d: date, hour: int = 9, minute: int = 30) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute)


# ── need vs. window: only "needed" may close a reminder ───────────────────────

@pytest.fixture
def templates(monkeypatch):
    """Route each template source to a scripted (result, entities) pair."""

    table: dict[str, tuple] = {}

    class FakeInfo:
        def __init__(self, result, entities):
            self._result, self.entities = result, set(entities)

        def result(self):
            if isinstance(self._result, Exception):
                raise self._result
            return self._result

    class FakeTemplate:
        def __init__(self, source, hass):
            self.source = source

        def async_render_to_info(self, variables=None, **kw):
            result, entities = table[self.source]
            return FakeInfo(result, entities)

    monkeypatch.setattr(reminder_mod, "Template", FakeTemplate)
    return table


def ac_runner(templates, need=True, window=True, need_entities=(), **cfg):
    templates["NEED"] = (need, need_entities)
    templates["WINDOW"] = (window, ())
    r = make_runner(
        schedule_type="condition", condition_mode="template",
        due_template="NEED", window_template="WINDOW", real_condition=True, **cfg,
    )
    r._state[const.STATE_PROMPT_OPEN] = True
    return r


def settle(r, when):
    run(r._check_condition_resolved(when))
    run(r._check_condition_resolved(when + CONFIRM))


def test_window_closing_is_not_a_completion(templates, frozen_time):
    """The 2026-09-23 failure, exactly: needed, but 18:00 closed the window."""
    frozen_time.set(TUESDAY, hour=18)
    r = ac_runner(templates, need=True, window=False)

    settle(r, at(TUESDAY, 18, 0))

    assert r._state[const.STATE_LAST_DONE] is None
    assert r.journal == []
    assert r._state[const.STATE_PROMPT_OPEN] is True


def test_window_gates_asking(templates, frozen_time):
    frozen_time.set(TUESDAY)
    r = ac_runner(templates, need=True, window=False)
    assert r._eval_condition() is False
    templates["WINDOW"] = (True, ())
    assert r._eval_condition() is True


def test_needed_shows_due_on_the_dashboard_outside_the_window(templates, frozen_time):
    frozen_time.set(TUESDAY, hour=21)
    r = ac_runner(templates, need=True, window=False)
    assert r.urgency == 1.0


def test_broken_window_fails_open(templates, frozen_time):
    frozen_time.set(TUESDAY)
    r = ac_runner(templates, need=True)
    templates["WINDOW"] = (ValueError("boom"), ())
    assert r._eval_condition() is True


def test_need_false_with_a_dark_input_is_unknown_not_done(templates, frozen_time):
    """Consuela offline → every lifespan defaults to 100 → 'not due'. Not done."""
    frozen_time.set(TUESDAY)
    r = ac_runner(templates, need=False, need_entities=["sensor.consuela_filter_lifespan"])
    r.hass.states.set("sensor.consuela_filter_lifespan", "unavailable")

    assert r._eval_need() is None
    settle(r, at(TUESDAY))
    assert r._state[const.STATE_LAST_DONE] is None


def test_need_render_error_is_unknown_and_reported(templates, frozen_time):
    frozen_time.set(TUESDAY)
    r = ac_runner(templates)
    templates["NEED"] = (ValueError("undefined"), ())

    assert r._eval_need() is None
    settle(r, at(TUESDAY))
    assert r._state[const.STATE_LAST_DONE] is None
    assert "async_call" in r.hass.tasks[0] or r.hass.tasks, "persistent notification raised"


def test_a_blip_does_not_resolve(templates, frozen_time):
    frozen_time.set(TUESDAY)
    r = ac_runner(templates, need=False)

    run(r._check_condition_resolved(at(TUESDAY)))
    templates["NEED"] = (True, ())
    run(r._check_condition_resolved(at(TUESDAY) + CONFIRM))
    templates["NEED"] = (False, ())
    run(r._check_condition_resolved(at(TUESDAY) + CONFIRM * 2))

    assert r._state[const.STATE_LAST_DONE] is None, "the streak restarted"


def test_a_real_resolution_is_recorded_and_announced(templates, frozen_time):
    """Rain credit resets days-since-rinse: the need clears, legitimately."""
    frozen_time.set(TUESDAY)
    r = ac_runner(templates, need=False)

    settle(r, at(TUESDAY))

    assert r._state[const.STATE_LAST_DONE] == TUESDAY.isoformat()
    assert r._state[const.STATE_LAST_COMPLETED] == TUESDAY.isoformat()
    assert r.journal == ["resolved"]
    assert any(p.get("tag") == f"ar_resolved_{r.entry_id}" for p in r.payloads)


# ── days_since_done counts completions only ───────────────────────────────────

def test_skip_does_not_restart_the_interval_clock(frozen_time):
    frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="condition", condition_mode="template")
    r._state[const.STATE_LAST_COMPLETED] = "2026-08-01"

    run(r.async_skip_today(confirmed=True))

    assert r._state[const.STATE_LAST_DONE] == TUESDAY.isoformat()
    assert r._template_extras()["days_since_done"] == 17


def test_auto_skip_does_not_restart_the_interval_clock(frozen_time):
    frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="condition", condition_mode="template")
    r._state[const.STATE_LAST_COMPLETED] = "2026-08-01"

    run(r._auto_skip())

    assert r._template_extras()["days_since_done"] == 17


def test_done_restarts_it(frozen_time):
    frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="condition", condition_mode="template")
    r._state[const.STATE_LAST_COMPLETED] = "2026-08-01"

    run(r.async_mark_done())

    assert r._template_extras()["days_since_done"] == 0


def test_old_store_seeds_last_completed_from_last_done():
    r = make_runner(schedule_type="daily")
    r._state.pop(const.STATE_LAST_COMPLETED, None)
    r._state[const.STATE_LAST_DONE] = "2026-09-23"
    r._post_load_migrate()
    assert r._state[const.STATE_LAST_COMPLETED] == "2026-09-23"


# ── snooze always comes back, on time ─────────────────────────────────────────

def test_expired_snooze_skips_the_nag_gap(frozen_time):
    """A 10-minute snooze used to wait out the 2-hour gap picked at the prompt."""
    now = frozen_time.set(TUESDAY, hour=11)
    r = make_runner(schedule_type="daily", schedule_time="09:00")
    r._state[const.STATE_LAST_PROMPT] = now.isoformat()
    r._state[const.STATE_NEXT_NAG_MINUTES] = 120

    run(r.async_snooze(timedelta(minutes=10)))
    assert r._is_due(now + timedelta(minutes=5)) is False
    assert r._is_due(now + timedelta(minutes=11)) is True


def test_snooze_past_midnight_survives_a_pattern_that_no_longer_matches(frozen_time):
    """Sunday-only, until_done off, snoozed late Sunday → still asked Monday."""
    now = frozen_time.set(SUNDAY, hour=21, minute=0)
    r = make_runner(schedule_type="weekly", schedule_days=["sun"],
                    schedule_time="09:00", until_done=False)
    r._state[const.STATE_LAST_PROMPT] = now.isoformat()
    run(r.async_snooze(timedelta(hours=24)))

    r._state[const.STATE_RESET_DAY] = SUNDAY.isoformat()
    run(r._check_daily_reset(at(MONDAY, 0, 1)))

    assert r._is_due(at(MONDAY, 20, 0)) is False, "still snoozed"
    assert r._is_due(at(MONDAY, 21, 1)) is True


def test_expired_snooze_skips_the_time_of_day_gate(frozen_time):
    now = frozen_time.set(TUESDAY, hour=19)
    r = make_runner(schedule_type="daily", schedule_time="17:00")
    run(r.async_snooze(timedelta(hours=14)))  # → 09:00 tomorrow, before 17:00
    assert r._is_scheduled(now + timedelta(hours=14, minutes=1)) is True


def test_expired_snooze_is_cleared_once_asked(frozen_time):
    now = frozen_time.set(TUESDAY, hour=11)
    r = make_runner(schedule_type="daily")
    r._state[const.STATE_SNOOZE_UNTIL] = (now - timedelta(minutes=1)).isoformat()

    async def send(prompt, volume):
        pass

    r._use_unified_notifications = lambda: True
    r._send_via_unified_notifications = send
    run(r._send_prompt(now))

    assert r._state[const.STATE_SNOOZE_UNTIL] is None


def test_spoken_snooze_duration_is_honoured(frozen_time):
    now = frozen_time.set(TUESDAY, hour=16)
    r = make_runner(schedule_type="daily")
    run(r.async_handle_voice_response("snooze it for 24 hours"))
    until = datetime.fromisoformat(r._state[const.STATE_SNOOZE_UNTIL])
    assert until - now == timedelta(hours=24)


# ── an answer during a send is not overwritten ────────────────────────────────

def test_done_while_the_prompt_is_in_flight_stays_done(frozen_time):
    now = frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="daily")

    async def send_and_answer(prompt, volume):
        await r.async_mark_done()

    r._use_unified_notifications = lambda: True
    r._send_via_unified_notifications = send_and_answer
    run(r._send_prompt(now))

    assert r._state[const.STATE_PROMPT_OPEN] is False
    assert r._state[const.STATE_RETRIES_TODAY] == 0


# ── skip and reschedule close what they claim to ──────────────────────────────

def test_skip_clears_a_past_reschedule(frozen_time):
    frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="weekly", schedule_days=["sun"])
    r._state[const.STATE_RESCHEDULE_DATE] = "2026-08-17"

    run(r.async_skip_today(confirmed=True))
    frozen_time.set(TUESDAY + timedelta(days=1))

    assert r._state[const.STATE_RESCHEDULE_DATE] is None
    assert r._is_scheduled(at(TUESDAY + timedelta(days=1))) is False


def test_skipping_a_one_time_reminder_ends_it(frozen_time):
    frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="once", once_date=TUESDAY.isoformat())
    run(r.async_skip_today(confirmed=True))
    assert r._removing is True


def test_announce_only_clears_reschedule(frozen_time):
    now = frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="weekly", schedule_days=["sun"], nag=False)
    r._state[const.STATE_RESCHEDULE_DATE] = TUESDAY.isoformat()

    async def announce(*a, **k):
        pass

    r._send_announcement = announce
    run(r._handle_due_reminder(now))
    assert r._state[const.STATE_RESCHEDULE_DATE] is None


def test_past_reschedule_skips_time_gate(frozen_time):
    frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="weekly", schedule_days=["sun"], schedule_time="17:00")
    r._state[const.STATE_RESCHEDULE_DATE] = MONDAY.isoformat()
    assert r._is_scheduled(at(TUESDAY, 8, 0)) is True


# ── accumulator and threshold anchors ─────────────────────────────────────────

def accum(**kw):
    r = make_runner(schedule_type="condition", condition_mode="accumulator",
                    accumulator_source="input_number.runtime", accumulator_limit=100,
                    real_condition=True, **kw)
    return r


def test_dark_accumulator_is_unknown():
    r = accum()
    assert r._eval_need() is None


def test_accumulator_follows_a_counter_that_went_backwards():
    r = accum()
    r._state[const.STATE_ACCUM_BASELINE] = 500.0
    r.hass.states.set("input_number.runtime", 10)
    assert r._eval_need() is False
    assert r._state[const.STATE_ACCUM_BASELINE] == 10.0
    r.hass.states.set("input_number.runtime", 111)
    assert r._eval_need() is True


def test_done_while_source_dark_reanchors_later(frozen_time):
    frozen_time.set(TUESDAY)
    r = accum()
    r._state[const.STATE_ACCUM_BASELINE] = 0.0
    run(r.async_mark_done())
    assert r._state[const.STATE_ACCUM_REANCHOR] is True

    r.hass.states.set("input_number.runtime", 150)
    assert r._eval_need() is False
    assert r._state[const.STATE_ACCUM_BASELINE] == 150.0


def test_threshold_latch_survives_a_reload():
    cfg = dict(schedule_type="condition", condition_mode="threshold",
               threshold_entity="sensor.salt", threshold_below=20,
               threshold_hysteresis=10, real_condition=True)
    r = make_runner(**cfg)
    r.hass.states.set("sensor.salt", 15)
    assert r._eval_need() is True
    persisted = dict(r._state)

    r2 = make_runner(**cfg)
    r2._state.update(persisted)
    r2._post_load_migrate()
    r2.hass.states.set("sensor.salt", 25)  # inside the band: still latched
    assert r2._eval_need() is True


# ── a reminder switched off does not resurrect old occurrences ────────────────

def test_disabled_tick_advances_the_day_without_carrying(frozen_time):
    frozen_time.set(MONDAY)
    r = make_runner(schedule_type="weekly", schedule_days=["sun"], enabled=False)
    r._state[const.STATE_RESET_DAY] = SUNDAY.isoformat()
    r._refresh_display = lambda: None

    run(r._on_timer_tick(at(MONDAY)))

    assert r._state[const.STATE_RESET_DAY] == MONDAY.isoformat()
    assert r._state[const.STATE_CARRY_FROM] is None


# ── lead heads-ups don't replace the prompt card ──────────────────────────────

def test_lead_announcement_uses_its_own_tag(frozen_time):
    now = frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="daily")
    tags = []

    async def announce(message, tag=None):
        tags.append(tag)

    r._announce = announce
    run(r._send_announcement(now, offset=7))
    run(r._send_announcement(now, offset=0))
    assert tags == [f"ar_lead_{r.entry_id}", None]


# ── spoken replies ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("done", "done"),
    ("I did it", "done"),
    ("yes, finished", "done"),
    ("already taken care of", "done"),
    ("not done yet", "dismiss"),
    ("no, I haven't done it", "dismiss"),
    ("I didn't finish", "dismiss"),
    ("haven't done that", "dismiss"),
    ("not yet", "dismiss"),
    ("nope", "dismiss"),
    ("incomplete", "reprompt"),
    ("skip it", "skip"),
    ("not today", "skip"),
    ("don't skip it", "dismiss"),
    ("snooze it", "snooze"),
    ("remind me later", "snooze"),
    ("not now, remind me in an hour", "snooze"),
    ("in two hours", "snooze"),
    ("music", "reprompt"),
    ("", "reprompt"),
])
def test_voice_reply_classification(text, expected):
    assert classify(text)[0] == expected


@pytest.mark.parametrize("text,expected", [
    ("snooze for 24 hours", timedelta(hours=24)),
    ("snooze for twenty four hours", timedelta(hours=24)),
    ("two hours", timedelta(hours=2)),
    ("an hour", timedelta(hours=1)),
    ("half an hour", timedelta(minutes=30)),
    ("15 minutes", timedelta(minutes=15)),
    ("a day", timedelta(days=1)),
    ("tomorrow", timedelta(days=1)),
    ("a couple of days", timedelta(days=2)),
    ("a week", timedelta(weeks=1)),
    ("1000 days", timedelta(days=30)),
    ("snooze it", None),
])
def test_spoken_duration(text, expected):
    assert parse_duration(text) == expected
