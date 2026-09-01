"""Nobody answering is not an answer, and must not be acknowledged out loud.

A household kept hearing "OK, I'll remind you about the clean water tank in a
while" with no question audible before it. Every one of those was a timeout:
the response window closed, the engine ran its dismiss path, and the dismiss
path spoke its acknowledgement regardless of who — if anyone — had replied. It
arrives a full response window after the ask, so it reads as the reminder
talking to itself; and when the prompt never reached the speaker at all, the
acknowledgement is the only thing the room ever hears.

The dismiss itself is correct and stays: silence keeps the reminder alive.
"""

from __future__ import annotations

import asyncio
from datetime import date

from conftest import const, make_runner

TUESDAY = date(2026, 8, 18)
LAST_PROMPT = const.STATE_LAST_PROMPT


def run(coro):
    return asyncio.run(coro)


def dismissible(**overrides):
    cfg = {"name": "Emilio - Fill Clean Water Tank",
           "dismiss_messages": ["No problem, I'll remind you about {subject} again later."]}
    cfg.update(overrides)
    return make_runner(**cfg)


# ── the fix ───────────────────────────────────────────────────────────────────

def test_a_timeout_is_not_acknowledged_aloud(frozen_time):
    frozen_time.set(TUESDAY)
    r = dismissible()

    run(r.async_dismiss(source="timeout"))

    assert r.acks == [], "nobody replied, so there is nobody to reply to"


def test_a_real_answer_is_still_acknowledged(frozen_time):
    frozen_time.set(TUESDAY)
    for source in ("voice", "mobile", None):
        r = dismissible()
        run(r.async_dismiss(source=source))
        assert len(r.acks) == 1, f"a {source or 'direct'} dismiss is a person answering"


# ── what a timeout must still do ──────────────────────────────────────────────

def test_a_timeout_still_records_and_still_re_nags(frozen_time):
    now = frozen_time.set(TUESDAY)
    r = dismissible()
    r._state[LAST_PROMPT] = None

    run(r.async_dismiss(source="timeout"))

    assert r.journal == ["dismiss"], "the timeout is still auditable"
    assert r._state[LAST_PROMPT] == now.isoformat(), "gap clock restarts, so it comes back"
    assert r.saves == 1
