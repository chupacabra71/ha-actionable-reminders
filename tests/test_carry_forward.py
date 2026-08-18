"""until_done: an occurrence nobody answered survives to the next day.

Before this existed, `_is_scheduled` matched recurring patterns on the
occurrence's own date only, so an unanswered Sunday reminder went quiet until
the NEXT Sunday. These lock in that it now carries, and — just as important —
that the ways of legitimately closing an occurrence still close it.

Calendar anchors (2026): Aug 16 = Sunday, 17 = Monday, 18 = Tuesday,
19 = Wednesday, 21 = Friday.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from conftest import const, make_runner

SUNDAY = date(2026, 8, 16)
MONDAY = date(2026, 8, 17)
TUESDAY = date(2026, 8, 18)
WEDNESDAY = date(2026, 8, 19)
FRIDAY = date(2026, 8, 21)

CARRY = const.STATE_CARRY_FROM
LAST_DONE = const.STATE_LAST_DONE
RESET_DAY = const.STATE_RESET_DAY


def run(coro):
    return asyncio.run(coro)


def at(d: date, hour: int = 9, minute: int = 30) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute)


def weekly_sunday(**overrides):
    cfg = {
        "schedule_type": "weekly",
        "schedule_days": ["sun"],
        "schedule_time": "09:00",
    }
    cfg.update(overrides)
    return make_runner(**cfg)


def roll_over_to(runner, previous: date, today: date):
    """Simulate the tick crossing midnight from `previous` into `today`."""
    runner._state[RESET_DAY] = previous.isoformat()
    run(runner._check_daily_reset(at(today)))


# ── the gap this closes ────────────────────────────────────────────────────────

def test_unanswered_weekly_occurrence_carries_to_the_next_day():
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._state[CARRY] == SUNDAY.isoformat()
    assert r._is_scheduled(at(MONDAY)) is True


def test_carried_occurrence_stays_due_every_following_day():
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)

    for day in (MONDAY, TUESDAY, WEDNESDAY, FRIDAY):
        assert r._is_scheduled(at(day)) is True, f"{day} should still be due"


def test_without_until_done_the_occurrence_is_still_lost():
    """The opt-out must preserve the old behaviour exactly."""
    r = weekly_sunday(until_done=False)
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._state[CARRY] is None
    assert r._is_scheduled(at(MONDAY)) is False


def test_multi_day_gap_finds_the_stepped_over_occurrence():
    """HA down, or simply away — the scan must not only look at yesterday."""
    r = make_runner(schedule_type="weekly", schedule_days=["wed"], schedule_time="09:00")
    roll_over_to(r, MONDAY, FRIDAY)

    assert r._state[CARRY] == WEDNESDAY.isoformat()


def test_monthly_occurrence_carries_too():
    r = make_runner(
        schedule_type="monthly",
        schedule_monthly_type="day",
        schedule_monthly_day=16,
        schedule_time="09:00",
    )
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._state[CARRY] == SUNDAY.isoformat()


def test_the_carry_anchor_does_not_drift_forward():
    """A second rollover must keep pointing at the ORIGINAL missed date."""
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)
    roll_over_to(r, MONDAY, TUESDAY)

    assert r._state[CARRY] == SUNDAY.isoformat()


# ── the ways an occurrence legitimately closes ─────────────────────────────────

def test_completed_occurrence_does_not_carry():
    r = weekly_sunday()
    r._state[LAST_DONE] = SUNDAY.isoformat()
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._state[CARRY] is None
    assert r._is_scheduled(at(MONDAY)) is False


def test_marking_done_clears_an_existing_carry(frozen_time):
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)
    frozen_time.set(MONDAY)

    run(r.async_mark_done())

    assert r._state[CARRY] is None
    assert r._is_scheduled(at(TUESDAY)) is False


def test_explicit_skip_drops_the_carry(frozen_time):
    """Skip is the way out of a carry — otherwise until_done is inescapable."""
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)
    frozen_time.set(MONDAY)

    run(r.async_skip_today(confirmed=True))

    assert r._state[CARRY] is None
    assert r._is_scheduled(at(TUESDAY)) is False
    assert "skip" in r.journal


def test_reschedule_supersedes_the_carry(frozen_time):
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)
    frozen_time.set(MONDAY)

    run(r.async_reschedule_next(FRIDAY.isoformat()))

    assert r._state[CARRY] is None
    assert r._state[const.STATE_RESCHEDULE_DATE] == FRIDAY.isoformat()


def test_dismiss_is_a_within_day_defer_and_keeps_the_carry(frozen_time):
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)
    frozen_time.set(MONDAY)

    run(r.async_dismiss())

    assert r._state[CARRY] == SUNDAY.isoformat()


def test_non_nagging_announce_closes_the_occurrence(frozen_time):
    """nag=False auto-completes on announce, so it must clear the marker."""
    r = weekly_sunday(nag=False)
    roll_over_to(r, SUNDAY, MONDAY)
    frozen_time.set(MONDAY)

    async def _announce(now, offset=0):
        r.acks.append("announced")

    r._send_announcement = _announce
    run(r._handle_due_reminder(at(MONDAY)))

    assert r._state[CARRY] is None
    assert r._state[LAST_DONE] == MONDAY.isoformat()


# ── auto-skip: gives up on today, not on the chore ─────────────────────────────

def test_auto_skip_leaves_the_chore_outstanding(frozen_time):
    frozen_time.set(SUNDAY)
    r = weekly_sunday()

    run(r._auto_skip())

    assert r._state[LAST_DONE] == SUNDAY.isoformat(), "still closes out today"
    assert r._state[CARRY] == SUNDAY.isoformat(), "but the chore is not done"
    assert r._is_scheduled(at(MONDAY)) is True


def test_auto_skip_without_until_done_gives_up_for_good(frozen_time):
    frozen_time.set(SUNDAY)
    r = weekly_sunday(until_done=False)

    run(r._auto_skip())

    assert r._state[CARRY] is None
    assert r._is_scheduled(at(MONDAY)) is False


# ── condition reminders re-arm on their own ────────────────────────────────────

def test_condition_reminders_never_carry():
    r = make_runner(
        schedule_type="condition",
        condition_mode="template",
        due_template="{{ true }}",
    )
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._state[CARRY] is None


def test_condition_auto_skip_never_carries(frozen_time):
    frozen_time.set(SUNDAY)
    r = make_runner(schedule_type="condition", condition_mode="template",
                    due_template="{{ true }}")

    run(r._auto_skip())

    assert r._state[CARRY] is None


# ── the carry does not bypass the other gates ──────────────────────────────────

def test_carried_occurrence_still_waits_for_its_time_of_day():
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._is_scheduled(at(MONDAY, hour=8, minute=0)) is False
    assert r._is_scheduled(at(MONDAY, hour=9, minute=30)) is True


def test_carried_occurrence_is_not_due_during_quiet_hours():
    r = weekly_sunday(schedule_time="00:30", quiet_start="22:00", quiet_end="08:00")
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._is_scheduled(at(MONDAY, hour=1, minute=0)) is True
    assert r._is_due(at(MONDAY, hour=1, minute=0)) is False, "quiet hours still gate it"


def test_carried_occurrence_is_due(frozen_time):
    frozen_time.set(MONDAY)
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)

    assert r._is_due(at(MONDAY)) is True


# ── display ────────────────────────────────────────────────────────────────────

def test_next_due_date_reports_the_carried_date_not_the_next_pattern_date(frozen_time):
    frozen_time.set(MONDAY)
    r = weekly_sunday()
    roll_over_to(r, SUNDAY, MONDAY)

    assert r.next_due_date == SUNDAY
    assert r.urgency > 1.0, "an overdue carry should rank as overdue"
    assert r.status == "overdue"


# ── existing subentries carry a now-removed `optional` key ─────────────────────

def test_stale_optional_key_from_older_subentries_is_ignored():
    """No migration needed: `optional` was removed, and every read goes through
    config.get(), so a leftover key in stored subentry data is simply never
    looked up."""
    r = weekly_sunday(optional=True, some_other_removed_flag="whatever")

    assert not hasattr(r, "optional")
    assert r.until_done is True
    assert r._is_scheduled(at(SUNDAY)) is True
