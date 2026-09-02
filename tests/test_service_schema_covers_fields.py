"""Every field update_reminder claims to merge must be accepted by its schema.

announce_when_away was added to the config, the wizard, the merge map and
services.yaml — but not to the voluptuous schema the service is registered
with. voluptuous rejects unknown keys, so the call came back `400: Bad Request`
with nothing naming the field, and every other layer looked correct. Nothing in
the suite noticed, because the schema is built inside async_setup and no unit
test can reach it.

So this reads the source. It is a crude test for a crude failure: a key that
exists everywhere except the one gate in front of it.
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import PKG_DIR

# Keys the merge map carries that the update service deliberately does not take
# as same-named fields: the message pair is exclusive and renamed, and entry_id
# is the target rather than a field.
NOT_SERVICE_FIELDS = {"prompt_messages", "entry_id"}


def _field_map_keys() -> set[str]:
    src = (PKG_DIR / "reminder_config.py").read_text()
    block = src.split("FIELD_MAP", 1)[1]
    block = block[block.index("{") : block.index("}") + 1]
    return set(re.findall(r'^\s*"([a-z_]+)":', block, re.M)) - NOT_SERVICE_FIELDS


def _schema_keys() -> set[str]:
    src = (PKG_DIR / "__init__.py").read_text()
    return set(re.findall(r'vol\.(?:Optional|Required|Exclusive)\(\s*"([a-z_]+)"', src))


def test_every_mergeable_field_is_accepted_by_a_service_schema():
    missing = sorted(_field_map_keys() - _schema_keys())
    assert not missing, (
        "these fields can be merged but not passed — the service will answer "
        f"400 Bad Request with no mention of the key: {missing}"
    )


def test_the_field_that_caught_this_is_covered():
    assert "announce_when_away" in _field_map_keys()
    assert "announce_when_away" in _schema_keys()


def test_the_source_scrape_actually_finds_things():
    # A regex that silently matched nothing would make the guard above vacuous.
    assert len(_field_map_keys()) > 20
    assert len(_schema_keys()) > 20
    assert "mandatory" in _field_map_keys() and "mandatory" in _schema_keys()
