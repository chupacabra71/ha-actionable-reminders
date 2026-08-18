"""Test harness for the reminder scheduling engine.

The integration is not installed into a Home Assistant checkout, so importing it
the usual way would drag in all of `homeassistant`. The logic under test — date
matching, the until_done carry marker, due evaluation — never touches HA at
runtime; it only needs the handful of names reminder.py binds at module scope.
So those are stubbed here and the package is loaded straight out of
custom_components/ under a synthetic name, which also skips its __init__.py.

`dt_util.now()` is frozen through the `frozen_time` fixture: every assertion
about "yesterday" and "the next scheduled date" has to be reproducible, and the
real clock would make the weekly/monthly cases fail one day in seven.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "actionable_reminders"


# ── stub the Home Assistant surface reminder.py imports ────────────────────────
def _install_ha_stubs() -> None:
    def mod(name: str, **attrs) -> types.ModuleType:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    mod("homeassistant")
    mod("homeassistant.const", STATE_ON="on", STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown")
    mod("homeassistant.core", HomeAssistant=object, Event=object, Context=object)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.event",
        async_track_time_interval=lambda *a, **k: None,
        async_track_state_change_event=lambda *a, **k: None)
    mod("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None)
    mod("homeassistant.helpers.script", Script=object)
    mod("homeassistant.helpers.storage", Store=object)
    mod("homeassistant.helpers.template", Template=object)
    mod("homeassistant.util")

    # A clock the tests drive. `now()` reads a module-level value so a test can
    # advance the day without rebuilding the reminder.
    dt_stub = mod("homeassistant.util.dt")
    dt_stub._frozen = datetime(2026, 8, 18, 9, 30)
    dt_stub.now = lambda: dt_stub._frozen
    dt_stub.as_local = lambda d: d
    dt_stub.parse_datetime = lambda s: datetime.fromisoformat(s) if s else None
    sys.modules["homeassistant.util"].dt = dt_stub


_install_ha_stubs()

# Load the package under a synthetic name so relative imports resolve without
# executing the real __init__.py.
_pkg = types.ModuleType("ar")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules["ar"] = _pkg

const = importlib.import_module("ar.const")
reminder_mod = importlib.import_module("ar.reminder")
dt_util = sys.modules["homeassistant.util.dt"]

ReminderRunner = reminder_mod.ReminderRunner


class FakeStates:
    """Minimal entity-state store for accumulator/threshold conditions."""

    def __init__(self) -> None:
        self._states: dict = {}

    def set(self, entity_id: str, value) -> None:
        self._states[entity_id] = types.SimpleNamespace(state=str(value))

    def get(self, entity_id: str):
        return self._states.get(entity_id)


class FakeServices:
    """Records service calls so the notification clear can be asserted on."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._registered = {("script", "unified_notifications")}

    def has_service(self, domain, service):
        return (domain, service) in self._registered

    async def async_call(self, domain, service, data=None, **kw):
        self.calls.append((domain, service, data))


class FakeBus:
    def __init__(self) -> None:
        self.fired: list[tuple] = []

    def async_fire(self, event, data=None):
        self.fired.append((event, data))


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()
        self.services = FakeServices()
        self.states = FakeStates()
        self.data = {const.DOMAIN: {"hub": {"master_enabled": True}}}


def make_runner(**config):
    """Build a ReminderRunner exercising the real _apply_config.

    __init__ is bypassed because it constructs a Store and logs; everything the
    scheduler reads comes from _apply_config, which is pure. The I/O methods are
    replaced with recorders so the lifecycle calls (done / skip / auto-skip)
    can be driven without a live HA.
    """
    real_condition = config.pop("real_condition", False)
    r = ReminderRunner.__new__(ReminderRunner)
    r.hass = FakeHass()
    r.name = config.pop("name", "Test Reminder")
    r.entry_id = "test_entry"
    r.uid = "test_uid"
    r._hub_config = config.pop("_hub_config", {})
    r._removing = False
    r._tmpl_warned = False
    r._accum_warned = False
    r._thresh_latched = False
    r._display_fingerprint = None
    r._pending_notifications = set()
    r._state = {
        const.STATE_LAST_PROMPT: None,
        const.STATE_LAST_DONE: None,
        const.STATE_RETRIES_TODAY: 0,
        const.STATE_ESCALATED: False,
        const.STATE_ESCALATIONS_TODAY: 0,
        const.STATE_AUTO_SKIPPED: False,
        const.STATE_RESET_DAY: None,
        const.STATE_ACCUM_BASELINE: None,
        const.STATE_SNOOZE_UNTIL: None,
        const.STATE_RESCHEDULE_DATE: None,
        const.STATE_CARRY_FROM: None,
        const.STATE_PROMPT_OPEN: False,
    }
    r._apply_config(config)

    r.saves = 0
    r.journal: list[str] = []
    r.acks: list[str] = []

    async def _save_state():
        r.saves += 1

    async def _record_journal(action, context=None, source=None, **kw):
        r.journal.append(action)

    async def _send_ack(message):
        r.acks.append(message)

    def _self_remove():
        r._removing = True

    # The template path needs a real HA Template; tests drive the anchor
    # directly instead, which is what _is_scheduled actually consults.
    r.condition_due = True
    if not real_condition:
        r._eval_condition = lambda: r.condition_due

    r._save_state = _save_state
    r._record_journal = _record_journal
    r._send_ack = _send_ack
    r._self_remove = _self_remove
    return r


@pytest.fixture
def frozen_time():
    """Drive dt_util.now(). `set(d)` jumps to 09:30 on that date."""

    class Clock:
        def set(self, when: date | datetime, hour: int = 9, minute: int = 30):
            if isinstance(when, datetime):
                dt_util._frozen = when
            else:
                dt_util._frozen = datetime(when.year, when.month, when.day, hour, minute)
            return dt_util._frozen

        @property
        def now(self) -> datetime:
            return dt_util._frozen

    original = dt_util._frozen
    yield Clock()
    dt_util._frozen = original
