"""Mirror each reminder's category onto its switch as an HA label.

Labels make categories usable everywhere HA already filters — the entities
page, automations' label targets, auto-entities — without anyone having to
learn this integration's attribute. Which labels change is decided by the pure
categories.plan_label_update; this module only does the registry I/O.

Nothing here may break setup: a label is a convenience, a reminder that fails
to load is not. Every registry call is guarded and logged instead.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, label_registry as lr

from .categories import label_name, plan_label_update
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_LABEL_ICON = "mdi:bell-outline"


def async_sync_category_labels(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reconcile every reminder switch's managed label with its category.

    Runs on each hub setup, which every add / edit / remove goes through, so
    this is the one reconcile path — the same shape as the orphan prunes.
    """
    try:
        ent_reg = er.async_get(hass)
        label_reg = lr.async_get(hass)
        runners = hass.data[DOMAIN]["hub"]["reminders"].values()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Skipping category label sync: %s", err)
        return

    for runner in list(runners):
        try:
            _sync_one(ent_reg, label_reg, runner)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not sync category label for %s: %s", runner.name, err)


def _sync_one(ent_reg, label_reg, runner) -> None:
    # Same unique_id ReminderSwitch registers under.
    entity_id = ent_reg.async_get_entity_id("switch", DOMAIN, f"{DOMAIN}_{runner.uid}")
    if not entity_id:
        # Not registered yet (first load of a brand-new reminder races the
        # platform); the next reload picks it up.
        return
    reg_entry = ent_reg.async_get(entity_id)
    if reg_entry is None:
        return

    current_ids = set(reg_entry.labels)
    current = {}
    for label_id in current_ids:
        label = label_reg.async_get_label(label_id)
        current[label_id] = label.name if label else None

    wanted = label_name(runner.category)
    remove, add = plan_label_update(current, wanted)
    new_ids = current_ids - remove
    if add and wanted:
        label = label_reg.async_get_label_by_name(wanted) or label_reg.async_create(
            wanted, icon=_LABEL_ICON
        )
        new_ids.add(label.label_id)

    if new_ids != current_ids:
        ent_reg.async_update_entity(entity_id, labels=new_ids)
        _LOGGER.debug("Labels for %s: %s", entity_id, sorted(new_ids))
