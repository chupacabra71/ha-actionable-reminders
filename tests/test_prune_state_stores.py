"""Deleting a reminder must take its state store with it.

Each reminder keeps runtime state in its own Store. Removing a reminder dropped
the subentry but left the file, so every reminder ever deleted leaked one. This
function deletes files, so the guards matter more than the happy path.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from conftest import const, init_mod

PREFIX = f"{const.DOMAIN}_state_"


def run(coro):
    return asyncio.run(coro)


class FakeSubentry:
    def __init__(self, subentry_id, unique_id=None, subentry_type=None):
        self.subentry_id = subentry_id
        self.unique_id = unique_id
        self.subentry_type = subentry_type or const.SUBENTRY_TYPE_REMINDER


class FakeEntry:
    def __init__(self, subentries):
        self.subentries = {s.subentry_id: s for s in subentries}


class Recorder:
    """Captures which store keys the prune removed."""

    def __init__(self, hass):
        self.removed: list[str] = []
        hass._recorder = self


def make_hass(filenames, *, listdir_raises=None):
    removed: list[str] = []

    async def _executor(fn, *args):
        if listdir_raises:
            raise listdir_raises
        return list(filenames)

    hass = types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *p: "/config/.storage"),
        async_add_executor_job=_executor,
        removed=removed,
    )
    return hass


@pytest.fixture
def patched_store(monkeypatch):
    """Replace Store so nothing touches a real filesystem."""
    removed: list[str] = []

    class FakeStore:
        def __init__(self, hass, version, key):
            self.key = key

        async def async_remove(self):
            removed.append(self.key)

    monkeypatch.setattr(init_mod, "Store", FakeStore)
    return removed


def test_store_for_a_deleted_reminder_is_removed(patched_store):
    entry = FakeEntry([FakeSubentry("alive1"), FakeSubentry("alive2")])
    hass = make_hass([PREFIX + "alive1", PREFIX + "alive2", PREFIX + "deleted"])

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == [PREFIX + "deleted"]


def test_live_reminders_are_never_touched(patched_store):
    entry = FakeEntry([FakeSubentry("alive1"), FakeSubentry("alive2")])
    hass = make_hass([PREFIX + "alive1", PREFIX + "alive2"])

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == []


def test_a_reminder_keyed_by_unique_id_survives(patched_store):
    """The runner keys its store on `unique_id or subentry_id`; a migrated
    reminder's file is named for its legacy id, and must not look orphaned."""
    entry = FakeEntry([FakeSubentry("sub_abc", unique_id="legacy_xyz")])
    hass = make_hass([PREFIX + "legacy_xyz", PREFIX + "stale"])

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == [PREFIX + "stale"]


def test_never_prunes_against_an_empty_keep_set(patched_store):
    """No subentries is indistinguishable from a partial load. Pruning there
    would delete every reminder's state."""
    entry = FakeEntry([])
    hass = make_hass([PREFIX + "a", PREFIX + "b", PREFIX + "c"])

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == []


def test_non_reminder_subentries_do_not_count_as_reminders(patched_store):
    entry = FakeEntry([FakeSubentry("other", subentry_type="something_else")])
    hass = make_hass([PREFIX + "orphan"])

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == [], "keep set was empty — must not prune blind"


def test_other_domain_stores_are_left_alone(patched_store):
    entry = FakeEntry([FakeSubentry("alive")])
    hass = make_hass([
        PREFIX + "alive",
        f"{const.DOMAIN}_journal",
        f"{const.DOMAIN}_calendar_source",
        "core.config_entries",
        "some_other_integration_state_xyz",
    ])

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == []


def test_suffixed_files_are_left_alone(patched_store):
    """HA's own .corrupt backups are not ours to reason about."""
    entry = FakeEntry([FakeSubentry("alive")])
    hass = make_hass([PREFIX + "alive", PREFIX + "gone.corrupt.1755000000"])

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == []


def test_unreadable_storage_dir_is_survivable(patched_store):
    entry = FakeEntry([FakeSubentry("alive")])
    hass = make_hass([], listdir_raises=OSError("permission denied"))

    run(init_mod._prune_orphan_state_stores(hass, entry))

    assert patched_store == []
