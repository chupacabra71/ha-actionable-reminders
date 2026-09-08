"""A busy reminder must not be able to bury a quiet one.

Two failures shared one cause. Every switchboard caller that names no group
lands in the shared default thread, so on the phone the A/C filter — eight
prompts a day, every day — stacked on top of a birthday that arrived once.
And an announcement asked for INFO, which the switchboard turns into the iOS
`active` interruption level: a Focus filter or the scheduled summary may hold
it for hours, while the engine has already stamped the occurrence done and
will never send it again.

Both are properties of the payload, so both are asserted on the payload the
real builders hand to the switchboard rather than on a restatement of it.
"""

from __future__ import annotations

import asyncio

from conftest import const, dt_util, make_runner


def run(coro):
    return asyncio.run(coro)


def sending_runner(**overrides):
    """A runner whose outbound switchboard payloads are captured."""
    cfg = {"name": "A/C Filter Downstairs",
           "prompt_messages": ["The downstairs A/C filter is due for a change."]}
    cfg.update(overrides)
    r = make_runner(**cfg)
    r.payloads = []
    r._use_unified_notifications = lambda: True
    r._notify_detached = r.payloads.append
    return r


# ── every reminder gets its own thread ────────────────────────────────────────

def test_a_prompt_names_its_own_group(frozen_time):
    r = sending_runner()

    run(r._send_prompt(dt_util.now()))

    assert r.payloads[0]["group"] == "REMINDER-A-C-FILTER-DOWNSTAIRS"


def test_an_announcement_names_the_same_group_as_its_prompts(frozen_time):
    """One reminder, one thread — whichever path sent it."""
    r = sending_runner()

    run(r._announce("The downstairs A/C filter is due for a change."))

    assert r.payloads[0]["group"] == "REMINDER-A-C-FILTER-DOWNSTAIRS"


def test_two_reminders_do_not_share_a_thread(frozen_time):
    noisy = sending_runner()
    quiet = sending_runner(name="Sister's Birthday", schedule_type="yearly",
                           anniversary_date="1969-09-08")

    assert noisy.notification_group != quiet.notification_group, (
        "a shared group is exactly how the filter buried the birthday"
    )


def test_a_group_survives_punctuation(frozen_time):
    """Names are free text; the group has to stay a single stable token."""
    r = sending_runner(name="Vacuums - Check Rollers & Filters")

    assert r.notification_group == "REMINDER-VACUUMS-CHECK-ROLLERS-FILTERS"


def test_a_nameless_reminder_still_has_a_group(frozen_time):
    """No name is no reason to fall back to the shared default thread."""
    r = sending_runner(name="🔔")

    assert r.notification_group == "REMINDERS"


# ── a birthday is announced once and cannot be deferred ───────────────────────

def test_a_birthday_announcement_is_time_sensitive(frozen_time):
    r = sending_runner(name="Sister's Birthday", schedule_type="yearly",
                       anniversary_date="1969-09-08")

    run(r._announce("🎂 Sister's Birthday is today!"))

    assert r.payloads[0]["severity"] == "TIME-SENSITIVE", (
        "INFO becomes iOS `active`, which a Focus filter may hold past the day"
    )


def test_a_birthday_lead_time_is_time_sensitive_too(frozen_time):
    """The heads-up is as one-shot as the day itself — same level."""
    r = sending_runner(name="Sister's Birthday", schedule_type="yearly",
                       anniversary_date="1969-09-08", lead_times=[1])

    run(r._send_announcement(dt_util.now(), offset=1))

    assert r.payloads[0]["severity"] == "TIME-SENSITIVE"


def test_an_ordinary_announcement_stays_informational(frozen_time):
    """Nothing else earns a Do Not Disturb bypass."""
    r = sending_runner(name="Consuela - Maintenance", schedule_type="condition")

    run(r._announce("Consuela has a wear part under ten percent."))

    assert r.payloads[0]["severity"] == "INFO"


def test_a_nagging_prompt_is_unchanged(frozen_time):
    """Prompts were already breaking through; the group is all that is new."""
    r = sending_runner()

    run(r._send_prompt(dt_util.now()))

    assert r.payloads[0]["severity"] == "TIME-SENSITIVE"
