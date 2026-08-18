"""A condition reminder whose anchor clears by itself is a completion.

Filling the robot's water tank clears the error code the reminder is anchored
on. The engine then simply stopped prompting — which left the prompt sitting on
the phone and recorded nothing, so the journal could not tell "you did it" from
"you were never reminded". These pin the close-out.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest

from conftest import const, make_runner

TUESDAY = date(2026, 8, 18)

PROMPT_OPEN = const.STATE_PROMPT_OPEN
LAST_DONE = const.STATE_LAST_DONE
CLEAR_SVC = const.CONF_CLEAR_NOTIFICATION_SERVICE


def run(coro):
    return asyncio.run(coro)


def at(d: date, hour: int = 9, minute: int = 30) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute)


def condition_runner(*, prompted=True, due=False, **overrides):
    cfg = {
        "schedule_type": "condition",
        "condition_mode": "template",
        "_hub_config": {CLEAR_SVC: "notify.mobile_app_all_devices"},
    }
    cfg.update(overrides)
    r = make_runner(**cfg)
    r.condition_due = due
    r._state[PROMPT_OPEN] = prompted
    return r


def clear_calls(r):
    return [c for c in r.hass.services.calls if c[2].get("message") == "clear_notification"]


# ── the close-out ──────────────────────────────────────────────────────────────

def test_anchor_clearing_itself_records_a_completion(frozen_time):
    frozen_time.set(TUESDAY)
    r = condition_runner()

    run(r._check_condition_resolved(at(TUESDAY)))

    assert r._state[LAST_DONE] == TUESDAY.isoformat()
    assert r.journal == ["resolved"], "distinct from a user-driven 'done'"
    assert r.status == "ok"


def test_self_resolve_retracts_the_notification(frozen_time):
    frozen_time.set(TUESDAY)
    r = condition_runner()

    run(r._check_condition_resolved(at(TUESDAY)))

    calls = clear_calls(r)
    assert len(calls) == 1
    domain, service, data = calls[0]
    assert (domain, service) == ("notify", "mobile_app_all_devices")
    assert data["data"]["tag"] == f"ar_{r.entry_id}"
    assert r._state[PROMPT_OPEN] is False


def test_self_resolve_fires_a_distinguishable_completion_event(frozen_time):
    frozen_time.set(TUESDAY)
    r = condition_runner()

    run(r._check_condition_resolved(at(TUESDAY)))

    events = [e for e in r.hass.bus.fired if e[0] == const.EVENT_COMPLETED]
    assert len(events) == 1
    assert events[0][1]["auto_resolved"] is True
    assert events[0][1]["name"] == r.name


def test_self_resolve_does_not_rerun_on_complete(frozen_time):
    """on_complete CAUSES the resolution (press the reset button); firing it
    afterwards would repeat a side effect that already happened. The stubbed
    Script would raise if it were invoked."""
    frozen_time.set(TUESDAY)
    r = condition_runner(
        on_complete=[{"action": "input_button.press",
                      "target": {"entity_id": "input_button.filter_replaced"}}]
    )

    run(r._check_condition_resolved(at(TUESDAY)))

    assert r.journal == ["resolved"]
    assert r.acks == [], "a self-resolve is silent — no spoken acknowledgement"


def test_accumulator_baseline_is_reanchored(frozen_time):
    """The source dropped — that is WHY it cleared. Keeping the old baseline
    would misreport progress from here on."""
    frozen_time.set(TUESDAY)
    r = make_runner(
        schedule_type="condition",
        condition_mode="accumulator",
        accumulator_source="sensor.filter_runtime",
        accumulator_limit=100,
        accumulator_reset_on_done=True,
        real_condition=True,
        _hub_config={CLEAR_SVC: "notify.mobile_app_all_devices"},
    )
    r._state[const.STATE_ACCUM_BASELINE] = 0
    r._state[PROMPT_OPEN] = True
    r.hass.states.set("sensor.filter_runtime", 3)   # externally reset

    assert r._eval_condition() is False
    run(r._check_condition_resolved(at(TUESDAY)))

    assert r._state[const.STATE_ACCUM_BASELINE] == 3
    assert r.journal == ["resolved"]


# ── what must NOT count as resolved ────────────────────────────────────────────

def test_quiet_hours_are_not_mistaken_for_resolution(frozen_time):
    """The critical distinction: not-due and resolved are different things.
    Detection reads the anchor, never _is_due."""
    frozen_time.set(TUESDAY)
    r = condition_runner(due=True, quiet_start="22:00", quiet_end="08:00")

    assert r._is_due(at(TUESDAY, hour=2)) is False, "quiet hours suppress it"
    run(r._check_condition_resolved(at(TUESDAY, hour=2)))

    assert r._state[LAST_DONE] is None
    assert r.journal == []


def test_still_due_does_not_resolve(frozen_time):
    frozen_time.set(TUESDAY)
    r = condition_runner(due=True)

    run(r._check_condition_resolved(at(TUESDAY)))

    assert r._state[LAST_DONE] is None
    assert clear_calls(r) == []


def test_no_outstanding_prompt_means_nothing_to_close(frozen_time):
    """Cleared before it ever asked — there is no completion to claim."""
    frozen_time.set(TUESDAY)
    r = condition_runner(prompted=False)

    run(r._check_condition_resolved(at(TUESDAY)))

    assert r._state[LAST_DONE] is None
    assert r.journal == []


def test_scheduled_reminders_never_self_resolve(frozen_time):
    frozen_time.set(TUESDAY)
    r = make_runner(schedule_type="weekly", schedule_days=["sun"],
                    _hub_config={CLEAR_SVC: "notify.mobile_app_all_devices"})
    r._state[PROMPT_OPEN] = True
    # Anchor reads false, so ONLY the schedule-type guard can stop a resolve.
    # Without this the test would pass on the early "still due" return instead.
    r.condition_due = False

    run(r._check_condition_resolved(at(TUESDAY)))

    assert r._state[LAST_DONE] is None
    assert r.journal == []


def test_already_answered_today_is_not_resolved_again(frozen_time):
    frozen_time.set(TUESDAY)
    r = condition_runner()
    r._state[LAST_DONE] = TUESDAY.isoformat()

    run(r._check_condition_resolved(at(TUESDAY)))

    assert r.journal == []


# ── the retraction on the other close paths ────────────────────────────────────

def test_marking_done_retracts_the_card(frozen_time):
    """Tapping the notification dismisses it on the device, but answering by
    voice or from the dashboard used to leave it stranded."""
    frozen_time.set(TUESDAY)
    r = condition_runner(due=True)

    run(r.async_mark_done())

    assert len(clear_calls(r)) == 1
    assert r._state[PROMPT_OPEN] is False


def test_auto_skip_retracts_the_card(frozen_time):
    frozen_time.set(TUESDAY)
    r = condition_runner(due=True)

    run(r._auto_skip())

    assert len(clear_calls(r)) == 1


def test_missing_clear_service_is_a_silent_noop(frozen_time):
    """A completion must never fail because no clear service is configured."""
    frozen_time.set(TUESDAY)
    r = condition_runner(_hub_config={})

    run(r._check_condition_resolved(at(TUESDAY)))

    assert clear_calls(r) == []
    assert r.journal == ["resolved"], "the completion is still recorded"
    assert r._state[LAST_DONE] == TUESDAY.isoformat()


def test_outstanding_prompt_survives_midnight(frozen_time):
    """The card outlives the day, so the flag that tracks it must too."""
    frozen_time.set(TUESDAY)
    r = condition_runner(due=True)
    r._state[const.STATE_RESET_DAY] = "2026-08-17"

    run(r._check_daily_reset(at(TUESDAY)))

    assert r._state[PROMPT_OPEN] is True


def test_sending_a_prompt_marks_one_outstanding(frozen_time):
    """The whole close-out hangs off this flag being set when we ask."""
    frozen_time.set(TUESDAY)
    r = condition_runner(prompted=False, due=True)
    r._notify_detached = lambda data: r.hass.services.calls.append(
        ("script", "unified_notifications", data)
    )

    run(r._send_prompt(at(TUESDAY)))

    assert r._state[PROMPT_OPEN] is True
    sent = [c for c in r.hass.services.calls if c[1] == "unified_notifications"]
    assert sent and sent[0][2]["tag"] == f"ar_{r.entry_id}", "same tag the clear uses"
