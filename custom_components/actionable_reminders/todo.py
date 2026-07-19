"""To-do list platform for Actionable Reminders.

Exposes a single aggregate to-do list ("Reminders") on the hub — one item per
enabled reminder. Checking an item off marks that reminder done for today (the
same ack path as a voice "yes" or a mobile tap); items reappear when the
reminder next comes due. This is the primary at-a-glance / Assist-voice surface.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_REMINDERS_UPDATED, STATE_LAST_DONE

_LOGGER = logging.getLogger(__name__)

# Light polling so completions made via voice/mobile surface in the list even
# without a per-reminder push; structural changes arrive via the dispatcher.
SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the aggregate Reminders to-do list on the hub entry."""
    async_add_entities([RemindersTodoList(hass, entry)])


class RemindersTodoList(TodoListEntity):
    """A single to-do list mirroring all reminders."""

    _attr_has_entity_name = True
    _attr_name = "Reminders"
    _attr_icon = "mdi:bell-ring"
    _attr_should_poll = True
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the list."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_reminders_todo"

    @property
    def device_info(self) -> dict:
        """Attach to the hub device."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Actionable Reminders",
        }

    def _runners(self) -> dict:
        """Return the live {entry_id: runner} registry, or empty."""
        hub = self.hass.data.get(DOMAIN, {}).get("hub")
        return hub.get("reminders", {}) if hub else {}

    @property
    def todo_items(self) -> list[TodoItem]:
        """Build the list live from the reminders (one item each)."""
        today = dt_util.now().date().isoformat()
        items: list[TodoItem] = []
        for entry_id, runner in self._runners().items():
            if not getattr(runner, "is_enabled", True):
                continue
            done_today = runner.state_dict.get(STATE_LAST_DONE) == today
            items.append(
                TodoItem(
                    uid=entry_id,
                    summary=runner.name,
                    status=(
                        TodoItemStatus.COMPLETED
                        if done_today
                        else TodoItemStatus.NEEDS_ACTION
                    ),
                )
            )
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Check an item off from the UI/voice -> mark that reminder done."""
        runner = self._runners().get(item.uid)
        if runner is None:
            return
        if item.status == TodoItemStatus.COMPLETED:
            await runner.async_mark_done()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Refresh when the set of reminders / their state changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_REMINDERS_UPDATED, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Re-render the list on a dispatcher ping."""
        self.async_write_ha_state()
