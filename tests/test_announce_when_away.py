"""A household chore is not addressed to one person's whereabouts.

The switchboard's voice gate follows the person a notification is addressed to,
so nothing is spoken on Alexa while they are out. That is right for anything
personal and wrong for a chore — the dogs still need their meds, and someone
else is standing in the kitchen. Opting in flips that one notification to
"speak to whoever IS home", which the switchboard decides; all the engine does
is say so.

Both delivery paths are exercised through the real payload builders, hooking
_notify_detached (the last step before the service call) rather than restating
what the payload should look like.
"""

from __future__ import annotations

import asyncio

from conftest import const, dt_util, make_runner

VOICE_ANY = "voice_any_resident"


def run(coro):
    return asyncio.run(coro)


def sending_runner(**overrides):
    """A runner whose outbound switchboard payloads are captured."""
    cfg = {"name": "Dogs - Monthly Meds",
           "prompt_messages": ["The dogs are due for their monthly meds."]}
    cfg.update(overrides)
    r = make_runner(**cfg)
    r.payloads = []
    r._use_unified_notifications = lambda: True
    r._notify_detached = r.payloads.append
    return r


# ── the opt-in, on the ask path ───────────────────────────────────────────────

def test_a_chore_asks_to_be_heard_by_whoever_is_home(frozen_time):
    r = sending_runner(announce_when_away=True)

    run(r._send_prompt(dt_util.now()))

    assert r.payloads, "the prompt was never handed to the switchboard"
    assert r.payloads[0][VOICE_ANY] is True


def test_a_personal_reminder_leaves_the_gate_alone(frozen_time):
    r = sending_runner()

    run(r._send_prompt(dt_util.now()))

    assert VOICE_ANY not in r.payloads[0], (
        "absent, not False — a switchboard predating the field must see no new key"
    )


# ── and on the announce path ──────────────────────────────────────────────────

def test_an_announcement_carries_it_too(frozen_time):
    # A non-nagging reminder announces rather than asks, and a chore is as
    # likely to be one as the other.
    r = sending_runner(announce_when_away=True, nag=False)

    run(r._send_announcement(dt_util.now()))

    assert r.payloads and r.payloads[0][VOICE_ANY] is True


def test_an_announcement_without_the_flag_omits_it(frozen_time):
    r = sending_runner(nag=False)

    run(r._send_announcement(dt_util.now()))

    assert r.payloads and VOICE_ANY not in r.payloads[0]


# ── config plumbing ───────────────────────────────────────────────────────────

def test_it_defaults_to_off():
    assert make_runner(name="Recycling").announce_when_away is False
    assert const.DEFAULT_ANNOUNCE_WHEN_AWAY is False


def test_it_is_read_from_the_reminder_config():
    assert make_runner(name="Recycling", announce_when_away=True).announce_when_away is True
