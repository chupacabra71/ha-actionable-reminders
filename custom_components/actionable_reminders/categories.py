"""Reminder categories: normalisation, suggestions, and label planning.

A category is free text with no engine meaning — it exists so ~50 reminders can
be found again. Everything here is pure (no Home Assistant imports) so the
rules that decide which labels an entity keeps can be tested without a running
HA; the registry I/O lives in labels.py.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .const import CATEGORY_LABEL_PREFIX, DEFAULT_CATEGORIES

UNCATEGORIZED = "Uncategorized"


def normalize_category(value: object) -> str:
    """Trim and collapse whitespace; None/blank → "" (uncategorized).

    Free text typed on a phone arrives as "Pool ", " pool" and "Pool" — without
    this they become three groups and three labels.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())


def category_options(used: Iterable[object]) -> list[str]:
    """Suggestions for the category picker: defaults ∪ categories in use.

    De-duplicated case-insensitively; a spelling already in use wins over the
    default's, so a reminder filed under "Yard & lawn" doesn't sit beside an
    empty "Yard & Lawn".
    """
    seen: dict[str, str] = {}
    for raw in list(used) + list(DEFAULT_CATEGORIES):
        cat = normalize_category(raw)
        if cat and cat.casefold() not in seen:
            seen[cat.casefold()] = cat
    return sorted(seen.values(), key=str.casefold)


def label_name(category: object) -> str | None:
    """The managed label for a category, or None when uncategorized."""
    cat = normalize_category(category)
    return f"{CATEGORY_LABEL_PREFIX}{cat}" if cat else None


def is_managed_label(name: str | None) -> bool:
    return bool(name) and str(name).startswith(CATEGORY_LABEL_PREFIX)


def plan_label_update(
    current: Mapping[str, str | None], wanted: str | None
) -> tuple[set[str], bool]:
    """Decide which labels to drop from an entity and whether to add `wanted`.

    Args:
        current: the entity's label_id → label name (None if the label vanished).
        wanted: the managed label name it should carry, or None for none.

    Returns:
        (label_ids to remove, whether the wanted label still needs adding).

    Only labels carrying the managed prefix are ever removed — a user's own
    labels on the same entity are invisible to this. Names compare
    case-insensitively because HA's label registry does.
    """
    wanted_key = wanted.casefold() if wanted else None
    remove: set[str] = set()
    has_wanted = False
    for label_id, name in current.items():
        if not is_managed_label(name):
            continue
        if wanted_key and str(name).casefold() == wanted_key:
            has_wanted = True
            continue
        remove.add(label_id)
    return remove, bool(wanted) and not has_wanted
