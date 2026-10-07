"""The card's list payload: everything it needs, nothing that isn't config.

The card renders straight from this, so a missing key is a blank cell and a
leaked runtime key is a field the editor would try to write back.
"""

from __future__ import annotations

import importlib
import types

from conftest import const, make_runner

ws = importlib.import_module("ar.ws")

KEYS = {
    "entry_id", "entity_id", "name", "category", "enabled", "schedule_type",
    "condition_mode", "status", "urgency", "summary", "next_due_date",
    "days_until_due", "snoozed_until", "mandatory", "config",
}


def _runner(name="Clean pool filter", **config):
    data = {const.CONF_REMINDER_NAME: name, **config}
    r = make_runner(name=name, **dict(data))
    r._subentry = types.SimpleNamespace(data=types.MappingProxyType(data))
    return r


def test_payload_has_every_field_the_card_reads(frozen_time):
    r = _runner(**{
        const.CONF_SCHEDULE_TYPE: "once",
        const.CONF_ONCE_DATE: "2026-08-25",
        const.CONF_CATEGORY: " Pool ",
    })
    p = ws.reminder_payload(r, "switch.clean_pool_filter")
    assert set(p) == KEYS
    assert p["category"] == "Pool"
    assert p["entity_id"] == "switch.clean_pool_filter"
    assert p["next_due_date"] == "2026-08-25"
    assert p["condition_mode"] is None
    assert p["config"][const.CONF_ONCE_DATE] == "2026-08-25"


def test_config_excludes_legacy_runtime_state():
    r = _runner(**{const.CONF_SCHEDULE_TYPE: "daily", "state": {"retries_today": 3}})
    assert "state" not in ws.reminder_payload(r)["config"]


def test_condition_reports_its_mode_and_unknown_config_passes_through():
    r = _runner(**{
        const.CONF_SCHEDULE_TYPE: "condition",
        const.CONF_CONDITION_MODE: "template",
        const.CONF_DUE_TEMPLATE: "{{ true }}",
        "window_template": "{{ now().hour < 20 }}",
    })
    p = ws.reminder_payload(r)
    assert p["condition_mode"] == "template"
    assert p["config"]["window_template"] == "{{ now().hour < 20 }}"


def test_list_payload_carries_category_suggestions_and_entity_ids():
    a = _runner("Mow", **{const.CONF_SCHEDULE_TYPE: "daily", const.CONF_CATEGORY: "Boat"})
    a.entry_id = "A"
    b = _runner("Dust", **{const.CONF_SCHEDULE_TYPE: "daily"})
    b.entry_id = "B"
    out = ws.list_payload([a, b], {"A": "switch.mow"})
    assert [r["entry_id"] for r in out["reminders"]] == ["A", "B"]
    assert out["reminders"][0]["entity_id"] == "switch.mow"
    assert out["reminders"][1]["entity_id"] is None
    assert "Boat" in out["categories"] and "Yard & Lawn" in out["categories"]


def test_one_broken_reminder_does_not_blank_the_list():
    good = _runner("Mow", **{const.CONF_SCHEDULE_TYPE: "daily"})
    bad = _runner("Broken", **{const.CONF_SCHEDULE_TYPE: "daily"})
    del bad._subentry
    out = ws.list_payload([bad, good])
    assert [r["name"] for r in out["reminders"]] == ["Mow"]
