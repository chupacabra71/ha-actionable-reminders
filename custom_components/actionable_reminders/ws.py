"""WebSocket API behind the reminders card.

HA cannot deep-link into a config-subentry reconfigure flow, so the only way to
edit a reminder was the integration page's gear icon — one reminder at a time,
five screens each. The card lists and edits reminders in place through two
commands:

  actionable_reminders/list    every reminder, its live status, and its editable
                               config, plus the category suggestions.
  actionable_reminders/update  {entry_id, changes} — validated by the SAME schema
                               and applied by the SAME function as the
                               update_reminder service, so the two cannot drift.

The payload builders are plain functions over a runner so they can be tested
without a websocket connection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .categories import category_options, normalize_category
from .const import DOMAIN, STATE_SNOOZE_UNTIL

_LOGGER = logging.getLogger(__name__)

WS_LIST = f"{DOMAIN}/list"
WS_UPDATE = f"{DOMAIN}/update"

# Stored keys that are not configuration: the legacy in-entry runtime state.
# Everything else in the subentry is what the wizard / update_reminder write.
_NOT_CONFIG = frozenset({"state"})


def editable_config(data: Any) -> dict[str, Any]:
    """The subentry's stored config, minus runtime state, as a plain dict."""
    return {k: v for k, v in dict(data or {}).items() if k not in _NOT_CONFIG}


def reminder_payload(runner, entity_id: str | None = None) -> dict[str, Any]:
    """One reminder as the card sees it: identity, live status, and config."""
    next_due = runner.next_due_date
    return {
        "entry_id": runner.entry_id,
        "entity_id": entity_id,
        "name": runner.name,
        "category": normalize_category(runner.category),
        "enabled": runner.is_enabled,
        "schedule_type": runner.schedule_type,
        "condition_mode": (
            runner.condition_mode if runner.schedule_type == "condition" else None
        ),
        "status": runner.status,
        "urgency": round(runner.urgency, 3),
        "summary": runner.summary,
        "next_due_date": next_due.isoformat() if next_due else None,
        "days_until_due": runner.days_until_due,
        "snoozed_until": runner.state_dict.get(STATE_SNOOZE_UNTIL),
        "mandatory": bool(runner.mandatory),
        "config": editable_config(runner._subentry.data),
    }


def list_payload(
    runners: Iterable[Any],
    entity_ids: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """The whole list response. `entity_ids` maps entry_id → switch entity_id."""
    entity_ids = entity_ids or {}
    reminders = []
    for runner in runners:
        try:
            reminders.append(reminder_payload(runner, entity_ids.get(runner.entry_id)))
        except Exception as err:  # noqa: BLE001
            # One broken reminder must not blank the whole card.
            _LOGGER.warning("Could not describe reminder %s: %s", runner.name, err)
    return {
        "reminders": reminders,
        "categories": category_options(r["category"] for r in reminders),
    }


def async_register_websocket(
    hass: HomeAssistant,
    validate_update: Callable[[dict[str, Any]], dict[str, Any]],
    apply_update: Callable[[HomeAssistant, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> None:
    """Register the commands. Called once from async_setup.

    The update schema and merge are injected from __init__ rather than imported,
    so this module never imports the package root.
    """

    @websocket_api.websocket_command({vol.Required("type"): WS_LIST})
    @callback
    def ws_list(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
        hub = hass.data.get(DOMAIN, {}).get("hub") or {}
        runners = list((hub.get("reminders") or {}).values())
        entity_ids: dict[str, str | None] = {}
        try:
            ent_reg = er.async_get(hass)
            for r in runners:
                entity_ids[r.entry_id] = ent_reg.async_get_entity_id(
                    "switch", DOMAIN, f"{DOMAIN}_{r.uid}"
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Entity lookup for reminders list failed: %s", err)
        connection.send_result(msg["id"], list_payload(runners, entity_ids))

    @websocket_api.websocket_command({
        vol.Required("type"): WS_UPDATE,
        vol.Required("entry_id"): str,
        vol.Required("changes"): dict,
    })
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_update(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
        changes = {k: v for k, v in msg["changes"].items() if k != "entry_id"}
        try:
            data = validate_update({"entry_id": msg["entry_id"], **changes})
        except vol.Invalid as err:
            connection.send_error(msg["id"], "invalid_format", str(err))
            return
        try:
            result = await apply_update(hass, data)
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "update_failed", str(err))
            return
        connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_update)
