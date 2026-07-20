"""Reminder subentry flow — the add/edit wizard.

One flow drives both creating a reminder (``async_step_user``) and editing an
existing one (``async_step_reconfigure``). Steps:

  basics → schedule (+ monthly detail) → behavior → [Save | Advanced] → advanced

Common reminders are two short screens; advanced settings are opt-in and, when
skipped, inherit the hub defaults.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_REMINDER_NAME,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_TIME,
    CONF_ONCE_DATE,
    CONF_ANNIVERSARY_DATE,
    CONF_DUE_TEMPLATE,
    CONF_ON_COMPLETE,
    CONF_LEAD_TIMES,
    CONF_NAG,
    CONF_ALLOW_CRITICAL,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_MONTHLY_TYPE,
    CONF_SCHEDULE_MONTHLY_DAY,
    CONF_SCHEDULE_MONTHLY_WEEK,
    CONF_SCHEDULE_MONTHLY_WEEKDAY,
    CONF_PROMPT_MESSAGES,
    CONF_ACK_MESSAGES,
    CONF_DISMISS_MESSAGES,
    CONF_MOBILE_SERVICE,
    CONF_ALEXA_DEVICES,
    CONF_ACTIONABLE,
    CONF_ESCALATION_VOLUME,
    CONF_RETRY_INTERVAL,
    CONF_MAX_RETRIES,
    CONF_ESCALATION_INTERVAL,
    CONF_MAX_ESCALATIONS,
    CONF_PRESENCE_SENSORS,
    CONF_CATCHUP_ON_ARRIVAL,
    CONF_QUIET_START,
    CONF_QUIET_END,
    DEFAULT_NAG,
    DEFAULT_ALLOW_CRITICAL,
    DEFAULT_OPTIONAL,
    DEFAULT_UNTIL_DONE,
    DEFAULT_ACTIONABLE,
    DEFAULT_ESCALATION_VOLUME,
    DEFAULT_CATCHUP_ON_ARRIVAL,
    DEFAULT_ACK_MESSAGES,
    DEFAULT_DISMISS_MESSAGES,
    CONF_OPTIONAL,
    CONF_UNTIL_DONE,
    WEEKDAY_LABELS,
    MONTHLY_WEEK_LABELS,
)

_LOGGER = logging.getLogger(__name__)


def _notify_services(hass) -> list[str]:
    return sorted(
        f"notify.{s}"
        for s in hass.services.async_services().get("notify", {})
        if s != "persistent_notification"
    )


def _parse_lead_times(text: str) -> list[int]:
    """Parse '30, 7, 1' → [30, 7, 1]; drop anything non-numeric."""
    out: list[int] = []
    for tok in str(text or "").replace(";", ",").split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.append(int(tok))
    return out


class ReminderSubentryFlow(ConfigSubentryFlow):
    """Add / edit a reminder as a subentry of the hub."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._editing = False

    # ── entry points ────────────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new reminder."""
        return await self.async_step_basics()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing reminder — seed the wizard from its current config."""
        self._editing = True
        subentry = self._get_reconfigure_subentry()
        self._data = dict(subentry.data)
        msgs = self._data.get(CONF_PROMPT_MESSAGES) or []
        self._data["message"] = msgs[0] if msgs else ""
        return await self.async_step_basics()

    # ── step 1: basics ──────────────────────────────────────────────────────

    async def async_step_basics(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_schedule()

        d = self._data
        schema = vol.Schema({
            vol.Required(
                CONF_REMINDER_NAME,
                description={"suggested_value": d.get(CONF_REMINDER_NAME)},
            ): str,
            vol.Required(
                CONF_SCHEDULE_TYPE, default=d.get(CONF_SCHEDULE_TYPE, "daily")
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "Daily", "value": "daily"},
                        {"label": "Weekly", "value": "weekly"},
                        {"label": "Monthly", "value": "monthly"},
                        {"label": "Yearly", "value": "yearly"},
                        {"label": "One-time", "value": "once"},
                        {"label": "Condition (template)", "value": "condition"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                "message",
                description={"suggested_value": d.get("message")},
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        })
        return self.async_show_form(step_id="basics", data_schema=schema)

    # ── step 2: schedule detail ─────────────────────────────────────────────

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        stype = self._data[CONF_SCHEDULE_TYPE]
        if user_input is not None:
            self._data.update(user_input)
            if stype == "monthly" and self._data.get(CONF_SCHEDULE_MONTHLY_TYPE) == "week_pattern":
                return await self.async_step_monthly()
            return await self.async_step_behavior()

        d = self._data
        time_field = {
            vol.Required(
                CONF_SCHEDULE_TIME, default=d.get(CONF_SCHEDULE_TIME, "09:00")
            ): selector.TimeSelector(),
        }
        if stype == "daily":
            schema = vol.Schema(time_field)
        elif stype == "weekly":
            schema = vol.Schema({
                **time_field,
                vol.Required(
                    CONF_SCHEDULE_DAYS,
                    default=d.get(CONF_SCHEDULE_DAYS, ["mon", "tue", "wed", "thu", "fri"]),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"label": l, "value": k} for k, l in WEEKDAY_LABELS.items()],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            })
        elif stype == "condition":
            schema = vol.Schema({
                vol.Required(
                    CONF_DUE_TEMPLATE,
                    description={"suggested_value": d.get(CONF_DUE_TEMPLATE)},
                ): selector.TemplateSelector(),
            })
        elif stype == "yearly":
            schema = vol.Schema({
                vol.Required(
                    CONF_ANNIVERSARY_DATE,
                    description={"suggested_value": d.get(CONF_ANNIVERSARY_DATE)},
                ): selector.DateSelector(),
                **time_field,
            })
        elif stype == "once":
            schema = vol.Schema({
                vol.Required(
                    CONF_ONCE_DATE,
                    description={"suggested_value": d.get(CONF_ONCE_DATE)},
                ): selector.DateSelector(),
                **time_field,
            })
        else:  # monthly
            schema = vol.Schema({
                **time_field,
                vol.Required(
                    CONF_SCHEDULE_MONTHLY_TYPE,
                    default=d.get(CONF_SCHEDULE_MONTHLY_TYPE, "day"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"label": "Specific day (1-31)", "value": "day"},
                            {"label": "Week pattern (e.g. first Wednesday)", "value": "week_pattern"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SCHEDULE_MONTHLY_DAY,
                    default=d.get(CONF_SCHEDULE_MONTHLY_DAY, 1),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=31, mode=selector.NumberSelectorMode.BOX)
                ),
            })
        return self.async_show_form(
            step_id="schedule",
            data_schema=schema,
            description_placeholders={"schedule_type": stype.title()},
        )

    async def async_step_monthly(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_behavior()

        d = self._data
        schema = vol.Schema({
            vol.Required(
                CONF_SCHEDULE_MONTHLY_WEEK, default=d.get(CONF_SCHEDULE_MONTHLY_WEEK, "first")
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": l, "value": k} for k, l in MONTHLY_WEEK_LABELS.items()],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_SCHEDULE_MONTHLY_WEEKDAY, default=d.get(CONF_SCHEDULE_MONTHLY_WEEKDAY, "mon")
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": l, "value": k} for k, l in WEEKDAY_LABELS.items()],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(step_id="monthly", data_schema=schema)

    # ── step 3: behavior ────────────────────────────────────────────────────

    async def async_step_behavior(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            lead_text = user_input.pop("lead_times_text", "")
            self._data.update(user_input)
            self._data[CONF_LEAD_TIMES] = _parse_lead_times(lead_text)
            return await self.async_step_menu()

        d = self._data
        lead_default = ", ".join(str(x) for x in d.get(CONF_LEAD_TIMES, []))
        schema = vol.Schema({
            vol.Required(CONF_NAG, default=d.get(CONF_NAG, DEFAULT_NAG)): bool,
            vol.Required(
                CONF_ALLOW_CRITICAL, default=d.get(CONF_ALLOW_CRITICAL, DEFAULT_ALLOW_CRITICAL)
            ): bool,
            vol.Optional("lead_times_text", default=lead_default): selector.TextSelector(),
            vol.Required(CONF_OPTIONAL, default=d.get(CONF_OPTIONAL, DEFAULT_OPTIONAL)): bool,
            vol.Required(CONF_UNTIL_DONE, default=d.get(CONF_UNTIL_DONE, DEFAULT_UNTIL_DONE)): bool,
        })
        return self.async_show_form(
            step_id="behavior",
            data_schema=schema,
            description_placeholders={
                "info": (
                    "Nag = keep prompting until acknowledged (off = one announce). "
                    "Lead-times = days before to give a heads-up (e.g. 30, 7, 1). "
                    "Allow critical = permit DND-bypassing alerts during escalation."
                )
            },
        )

    # ── step 4: menu (save or advanced) ─────────────────────────────────────

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=["save", "advanced"],
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self._finish()

    # ── step 5: advanced (optional) ─────────────────────────────────────────

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self._finish()

        d = self._data
        notify_services = _notify_services(self.hass)
        mobile = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services, mode=selector.SelectSelectorMode.DROPDOWN
                )
            )
            if notify_services
            else str
        )
        schema = vol.Schema({
            vol.Optional(
                CONF_MOBILE_SERVICE, description={"suggested_value": d.get(CONF_MOBILE_SERVICE)}
            ): mobile,
            vol.Optional(
                CONF_ALEXA_DEVICES, default=d.get(CONF_ALEXA_DEVICES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player", multiple=True)
            ),
            vol.Required(
                CONF_ACTIONABLE, default=d.get(CONF_ACTIONABLE, DEFAULT_ACTIONABLE)
            ): bool,
            vol.Required(
                CONF_ESCALATION_VOLUME, default=d.get(CONF_ESCALATION_VOLUME, DEFAULT_ESCALATION_VOLUME)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.1, mode=selector.NumberSelectorMode.SLIDER
                )
            ),
            vol.Optional(
                CONF_RETRY_INTERVAL, description={"suggested_value": d.get(CONF_RETRY_INTERVAL)}
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            vol.Optional(
                CONF_MAX_RETRIES, description={"suggested_value": d.get(CONF_MAX_RETRIES)}
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
            vol.Optional(
                CONF_ESCALATION_INTERVAL, description={"suggested_value": d.get(CONF_ESCALATION_INTERVAL)}
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            vol.Optional(
                CONF_MAX_ESCALATIONS, description={"suggested_value": d.get(CONF_MAX_ESCALATIONS)}
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=20)),
            vol.Optional(
                CONF_PRESENCE_SENSORS, default=d.get(CONF_PRESENCE_SENSORS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor", device_class="presence", multiple=True
                )
            ),
            vol.Required(
                CONF_CATCHUP_ON_ARRIVAL, default=d.get(CONF_CATCHUP_ON_ARRIVAL, DEFAULT_CATCHUP_ON_ARRIVAL)
            ): bool,
            vol.Optional(
                CONF_QUIET_START, description={"suggested_value": d.get(CONF_QUIET_START)}
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_QUIET_END, description={"suggested_value": d.get(CONF_QUIET_END)}
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_ON_COMPLETE, description={"suggested_value": d.get(CONF_ON_COMPLETE)}
            ): selector.ActionSelector(),
        })
        return self.async_show_form(
            step_id="advanced",
            data_schema=schema,
            description_placeholders={
                "info": "All optional — leave a field empty to use the hub default."
            },
        )

    # ── finish ──────────────────────────────────────────────────────────────

    def _build_config(self) -> dict[str, Any]:
        """Assemble the reminder config dict from accumulated wizard data."""
        d = self._data
        stype = d[CONF_SCHEDULE_TYPE]
        config: dict[str, Any] = {
            CONF_REMINDER_NAME: d[CONF_REMINDER_NAME],
            CONF_SCHEDULE_TYPE: stype,
            CONF_SCHEDULE_TIME: d.get(CONF_SCHEDULE_TIME, "09:00"),
            CONF_PROMPT_MESSAGES: [d["message"]] if d.get("message") else [],
            CONF_ACK_MESSAGES: d.get(CONF_ACK_MESSAGES, DEFAULT_ACK_MESSAGES),
            CONF_DISMISS_MESSAGES: d.get(CONF_DISMISS_MESSAGES, DEFAULT_DISMISS_MESSAGES),
        }

        if stype == "weekly":
            config[CONF_SCHEDULE_DAYS] = d.get(CONF_SCHEDULE_DAYS, [])
        elif stype == "once":
            config[CONF_ONCE_DATE] = d.get(CONF_ONCE_DATE)
        elif stype == "yearly":
            config[CONF_ANNIVERSARY_DATE] = d.get(CONF_ANNIVERSARY_DATE)
        elif stype == "condition":
            config[CONF_DUE_TEMPLATE] = d.get(CONF_DUE_TEMPLATE)
        elif stype == "monthly":
            mtype = d.get(CONF_SCHEDULE_MONTHLY_TYPE, "day")
            config[CONF_SCHEDULE_MONTHLY_TYPE] = mtype
            if mtype == "day":
                config[CONF_SCHEDULE_MONTHLY_DAY] = d.get(CONF_SCHEDULE_MONTHLY_DAY, 1)
            else:
                config[CONF_SCHEDULE_MONTHLY_WEEK] = d.get(CONF_SCHEDULE_MONTHLY_WEEK)
                config[CONF_SCHEDULE_MONTHLY_WEEKDAY] = d.get(CONF_SCHEDULE_MONTHLY_WEEKDAY)

        # Behavior + advanced: copy through whatever the wizard collected.
        for key in (
            CONF_NAG, CONF_ALLOW_CRITICAL, CONF_OPTIONAL, CONF_UNTIL_DONE, CONF_LEAD_TIMES,
            CONF_MOBILE_SERVICE, CONF_ALEXA_DEVICES, CONF_ACTIONABLE, CONF_ESCALATION_VOLUME,
            CONF_RETRY_INTERVAL, CONF_MAX_RETRIES, CONF_ESCALATION_INTERVAL, CONF_MAX_ESCALATIONS,
            CONF_PRESENCE_SENSORS, CONF_CATCHUP_ON_ARRIVAL, CONF_QUIET_START, CONF_QUIET_END,
            CONF_ON_COMPLETE,
        ):
            if key in d and d[key] not in (None, ""):
                config[key] = d[key]
        return config

    def _finish(self) -> SubentryFlowResult:
        config = self._build_config()
        title = config[CONF_REMINDER_NAME]
        if self._editing:
            # NOT async_update_reload_and_abort: that raises ValueError when the
            # entry has update listeners, and the hub registers one. Updating the
            # subentry fires that listener, which schedules the reload for us.
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=title,
                data=config,
            )
        return self.async_create_entry(title=title, data=config)
