"""Categories: stored, surfaced, and mirrored as labels without touching yours.

A category has no engine meaning, so the ways it can go wrong are all about
bookkeeping: the same category typed three ways becoming three groups, an edit
that silently keeps the old category, the switch not carrying it, the service
schema rejecting it with a bare 400, and — the one that would actually annoy —
the label sync stripping a label the user put on the entity themselves.
"""

from __future__ import annotations

from datetime import date
import importlib

from conftest import const, make_runner

cats = importlib.import_module("ar.categories")
rc = importlib.import_module("ar.reminder_config")
switch_mod = importlib.import_module("ar.switch")

TODAY = date(2026, 9, 1)
P = const.CATEGORY_LABEL_PREFIX


# ── normalisation + suggestions ──────────────────────────────────────────────

def test_whitespace_variants_are_one_category():
    assert cats.normalize_category("  Yard   &  Lawn ") == "Yard & Lawn"
    assert cats.normalize_category(None) == ""
    assert cats.normalize_category("   ") == ""


def test_options_merge_defaults_with_used_and_keep_the_used_spelling():
    opts = cats.category_options(["yard & lawn", "Boat", "", None, "Boat "])
    assert "Boat" in opts and opts.count("Boat") == 1
    # The in-use spelling replaces the default's, not sits beside it.
    assert "yard & lawn" in opts and "Yard & Lawn" not in opts
    for default in ("House", "Pool", "Other"):
        assert default in opts
    assert opts == sorted(opts, key=str.casefold)


# ── config → runner → switch attribute ───────────────────────────────────────

def test_category_reaches_the_runner_and_the_switch_attribute():
    runner = make_runner(**{const.CONF_SCHEDULE_TYPE: "daily", const.CONF_CATEGORY: "Pool"})
    assert runner.category == "Pool"
    sw = switch_mod.ReminderSwitch(runner)
    assert sw._attr_extra_state_attributes["category"] == "Pool"


def test_missing_category_is_uncategorized_not_none():
    runner = make_runner(**{const.CONF_SCHEDULE_TYPE: "daily"})
    assert runner.category == ""
    assert switch_mod.ReminderSwitch(runner)._attr_extra_state_attributes["category"] == ""


# ── update_reminder merge ────────────────────────────────────────────────────

def _base():
    return {
        const.CONF_REMINDER_NAME: "Mow",
        const.CONF_SCHEDULE_TYPE: "once",
        const.CONF_ONCE_DATE: "2026-09-10",
        const.CONF_CATEGORY: "House",
    }


def test_update_sets_a_normalised_category_and_carries_the_rest():
    out = rc.build_updated_config(_base(), {"category": " Yard  & Lawn"}, TODAY)
    assert out[const.CONF_CATEGORY] == "Yard & Lawn"
    assert out[const.CONF_ONCE_DATE] == "2026-09-10"


def test_update_can_uncategorize():
    out = rc.build_updated_config(_base(), {"category": ""}, TODAY)
    assert out[const.CONF_CATEGORY] == ""


def test_an_unrelated_edit_keeps_the_category():
    out = rc.build_updated_config(_base(), {"name": "Mow the lawn"}, TODAY)
    assert out[const.CONF_CATEGORY] == "House"


# ── label planning ───────────────────────────────────────────────────────────

def test_label_name():
    assert cats.label_name("Pool") == f"{P}Pool"
    assert cats.label_name(" ") is None


def test_new_category_adds_its_label_and_drops_only_the_old_managed_one():
    current = {"l_old": f"{P}House", "l_user": "Outdoor", "l_user2": "Reminders-ish"}
    remove, add = cats.plan_label_update(current, f"{P}Pool")
    assert remove == {"l_old"}
    assert add is True


def test_already_labelled_is_a_no_op_case_insensitively():
    remove, add = cats.plan_label_update({"l1": f"{P}pool", "u": "Mine"}, f"{P}Pool")
    assert remove == set() and add is False


def test_uncategorized_strips_every_managed_label_and_adds_none():
    current = {"a": f"{P}House", "b": f"{P}Pool", "u": "Mine", "gone": None}
    remove, add = cats.plan_label_update(current, None)
    assert remove == {"a", "b"}
    assert add is False


def test_user_labels_are_never_removed():
    current = {"u1": "Garden", "u2": "Reminders", "gone": None}
    remove, _ = cats.plan_label_update(current, f"{P}Pool")
    assert remove == set()
