"""A prompt message may be a template, so it can name what tripped it.

The "a data source went dark" reminder read fifteen entities in its
due_template and then announced a fixed sentence telling you to go check three
different things — the engine knew which one was unavailable and had no way to
say so. These pin the render path: that it happens at all, that it cannot take
the prompt down with it, and that what it produces is fit for a spoken,
length-capped payload.

`Template` is a stub here (conftest replaces the HA surface), so each test
installs a fake standing in for the render — the wiring is what is under test,
not Jinja.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from conftest import const, dt_util, make_runner, reminder_mod

TUESDAY = date(2026, 8, 18)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_template(monkeypatch):
    """Install a Template stand-in and record what it was handed.

    `render` is what the test wants back (or an exception to raise); `seen`
    collects the (source, variables) pairs so a test can assert the extras
    reached the render.
    """

    class Recorder:
        def __init__(self) -> None:
            self.render = ""
            self.seen: list[tuple[str, dict]] = []

        def install(self) -> None:
            recorder = self

            class FakeTemplate:
                def __init__(self, source, hass):
                    self.source = source

                def async_render(self, variables=None, **kwargs):
                    recorder.seen.append((self.source, variables or {}))
                    if isinstance(recorder.render, Exception):
                        raise recorder.render
                    return recorder.render

            monkeypatch.setattr(reminder_mod, "Template", FakeTemplate)

    rec = Recorder()
    rec.install()
    return rec


def prompt_runner(message: str, **overrides):
    cfg = {"schedule_type": "condition", "condition_mode": "template",
           "prompt_messages": [message]}
    cfg.update(overrides)
    r = make_runner(**cfg)
    r.sent: list[str] = []

    async def _send_via_unified_notifications(prompt, volume):
        r.sent.append(prompt)

    r._send_via_unified_notifications = _send_via_unified_notifications
    r._use_unified_notifications = lambda: True
    return r


# ── the render ────────────────────────────────────────────────────────────────

def test_a_templated_prompt_is_rendered(fake_template, frozen_time):
    frozen_time.set(TUESDAY)
    fake_template.render = "Data source dark: Consuela."
    r = prompt_runner("Data source dark: {{ whatever }}.")

    run(r._send_prompt(dt_util.now()))

    assert "Data source dark: Consuela." in r.sent[0]


def test_a_plain_message_never_builds_a_template(fake_template, frozen_time):
    frozen_time.set(TUESDAY)
    r = prompt_runner("Did you give the dogs their meds?")

    run(r._send_prompt(dt_util.now()))

    assert fake_template.seen == [], "no braces, no render"
    assert "Did you give the dogs their meds?" in r.sent[0]


def test_the_render_reads_the_same_extras_as_the_condition(fake_template, frozen_time):
    frozen_time.set(TUESDAY)
    fake_template.render = "ok"
    r = prompt_runner("{{ days_since_done }} days")
    r._state[const.STATE_LAST_DONE] = "2026-08-11"

    run(r._send_prompt(dt_util.now()))

    _, variables = fake_template.seen[0]
    assert variables["days_since_done"] == 7
    assert variables["last_done"] == "2026-08-11"


# ── what the render must not do ───────────────────────────────────────────────

def test_a_broken_template_falls_back_to_its_own_text(fake_template, frozen_time):
    frozen_time.set(TUESDAY)
    fake_template.render = ValueError("undefined 'sensor'")
    r = prompt_runner("Dark: {{ boom }}.")

    run(r._send_prompt(dt_util.now()))

    assert r.sent, "a bad template must not swallow the prompt"
    assert "Dark: {{ boom }}." in r.sent[0]


def test_block_whitespace_is_collapsed(fake_template, frozen_time):
    frozen_time.set(TUESDAY)
    # What a real {% set %}/{% for %} message renders to: the newlines and
    # indentation the blocks sat on. Spoken aloud and squeezed through a
    # 255-character payload, those are pure cost.
    fake_template.render = "\n  \n  Data source dark:   Consuela.\n  "
    r = prompt_runner("{% set x = 1 %}Data source dark: {{ x }}.")

    run(r._send_prompt(dt_util.now()))

    assert "Data source dark: Consuela." in r.sent[0]
    assert "\n" not in r.sent[0]


def test_an_announcement_renders_too(fake_template, frozen_time):
    frozen_time.set(TUESDAY)
    fake_template.render = "Data source dark: Consuela."
    r = prompt_runner("Data source dark: {{ whatever }}.", nag=False)
    announced: list[str] = []
    r._announce = lambda message: _record(announced, message)

    run(r._send_announcement(dt_util.now()))

    assert announced == ["Data source dark: Consuela."]


async def _record(sink: list, message: str) -> None:
    sink.append(message)
