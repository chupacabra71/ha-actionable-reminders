"""To-do list platform for Actionable Reminders.

Exposes a single aggregate to-do list ("Reminders") on the hub — one item per
enabled reminder. Checking an item off marks that reminder done for today (the
same ack path as a voice "yes" or a mobile tap); items reappear when the
reminder next comes due. This is the primary at-a-glance / Assist-voice surface.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ONCE_DATE,
    CONF_PROMPT_MESSAGES,
    CONF_REMINDER_NAME,
    CONF_SCHEDULE_TIME,
    CONF_SCHEDULE_TYPE,
    SUBENTRY_TYPE_REMINDER,
    DOMAIN,
    SIGNAL_REMINDERS_UPDATED,
    STATE_LAST_DONE,
)

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

    _attr_has_entity_name = False
    _attr_name = "Reminders"
    _attr_icon = "mdi:bell-ring"
    _attr_should_poll = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    )

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the list."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_reminders_todo"

    @property
    def device_info(self) -> dict | None:
        """No hub device — hub-level entity with no owning subentry.

        See ReminderMasterSwitch.device_info; the parent entry carries no
        device to avoid the "Devices that don't belong to a sub-entry" section.
        """
        return None

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
            if not getattr(runner, "nag", True):
                continue  # announce-only (e.g. birthdays) aren't to-do tasks
            done_today = runner.state_dict.get(STATE_LAST_DONE) == today
            # Condition reminders only appear as tasks while actually due.
            if getattr(runner, "schedule_type", "") == "condition":
                if not (runner.is_condition_due() or done_today):
                    continue
            items.append(
                TodoItem(
                    uid=entry_id,
                    summary=runner.name,
                    status=(
                        TodoItemStatus.COMPLETED
                        if done_today
                        else TodoItemStatus.NEEDS_ACTION
                    ),
                    due=runner.next_due_date,
                )
            )
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Check an item off from the UI/voice -> mark that reminder done."""
        runner = self._runners().get(item.uid)
        if runner is None:
            return
        # Persist an edited due date (one-time reminders only -> once_date).
        if item.due is not None and getattr(runner, "schedule_type", None) == "once":
            new_date = (
                item.due.date().isoformat()
                if isinstance(item.due, datetime)
                else item.due.isoformat()
            )
            if new_date != runner.once_date:
                self.hass.config_entries.async_update_subentry(
                    self._entry,
                    runner._subentry,
                    data={**runner._subentry.data, CONF_ONCE_DATE: new_date},
                )
        if item.status == TodoItemStatus.COMPLETED:
            await runner.async_mark_done()
        self.async_write_ha_state()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Quick-add from the list -> create a one-time reminder.

        The item's due (date or datetime) becomes the one-time target; with no
        due, it targets today at the current time so it surfaces promptly.
        """
        now = dt_util.now()
        once_date = now.date().isoformat()
        schedule_time = now.strftime("%H:%M")
        due = item.due
        if isinstance(due, datetime):
            once_date = due.date().isoformat()
            schedule_time = due.strftime("%H:%M")
        elif isinstance(due, date):
            once_date = due.isoformat()

        data = {
            CONF_REMINDER_NAME: item.summary,
            CONF_PROMPT_MESSAGES: [item.summary],
            CONF_SCHEDULE_TYPE: "once",
            CONF_SCHEDULE_TIME: schedule_time,
            CONF_ONCE_DATE: once_date,
        }
        # Adding the subentry fires the hub update-listener → reload → the new
        # runner and switch are created.
        self.hass.config_entries.async_add_subentry(
            self._entry,
            ConfigSubentry(
                data=MappingProxyType(data),
                subentry_type=SUBENTRY_TYPE_REMINDER,
                title=item.summary,
                unique_id=None,
            ),
        )
        self.async_write_ha_state()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items -> remove those reminder subentries (listener reloads)."""
        for uid in uids:
            # async_remove_subentry raises UnknownSubEntry on a stale id.
            if uid in self._entry.subentries:
                self.hass.config_entries.async_remove_subentry(self._entry, uid)
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
