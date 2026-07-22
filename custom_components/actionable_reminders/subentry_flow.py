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
from homeassistant.util import dt as dt_util

from .const import (
    CONF_REMINDER_NAME,
    CONF_SCHEDULE_TYPE,
    CONF_SCHEDULE_TIME,
    CONF_ONCE_DATE,
    CONF_ANNIVERSARY_DATE,
    CONF_DUE_TEMPLATE,
    CONF_CONDITION_MODE,
    CONF_ACCUM_SOURCE,
    CONF_ACCUM_LIMIT,
    CONF_ACCUM_RESET_ON_DONE,
    CONF_THRESHOLD_ENTITY,
    CONF_THRESHOLD_BELOW,
    CONF_THRESHOLD_ABOVE,
    CONF_THRESHOLD_HYSTERESIS,
    CONF_ON_COMPLETE,
    CONF_LEAD_TIMES,
    CONF_NAG,
    CONF_MANDATORY,
    CONF_ALLOW_CRITICAL,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_MONTHLY_TYPE,
    CONF_SCHEDULE_MONTHLY_DAY,
    CONF_SCHEDULE_MONTHLY_WEEK,
    CONF_SCHEDULE_MONTHLY_WEEKDAY,
    CONF_INTERVAL_EVERY,
    CONF_INTERVAL_UNIT,
    CONF_INTERVAL_ANCHOR,
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
        self._migrate_legacy_schedule()
        return await self.async_step_basics()

    def _migrate_legacy_schedule(self) -> None:
        """Map a legacy schedule type onto the unified 'repeating' form for editing.

        daily/weekly/monthly/yearly/interval all become 'repeating' with an
        every/unit/anchor — preserving weekday and monthly-pattern detail.
        """
        d = self._data
        st = d.get(CONF_SCHEDULE_TYPE)
        if st in (None, "repeating", "once", "condition"):
            return
        today = dt_util.now().date().isoformat()
        d[CONF_SCHEDULE_TYPE] = "repeating"
        if st == "daily":
            d[CONF_INTERVAL_EVERY] = 1
            d[CONF_INTERVAL_UNIT] = "days"
            d.setdefault(CONF_INTERVAL_ANCHOR, today)
        elif st == "weekly":
            d[CONF_INTERVAL_EVERY] = 1
            d[CONF_INTERVAL_UNIT] = "weeks"
            d.setdefault(CONF_INTERVAL_ANCHOR, today)
        elif st == "monthly":
            d[CONF_INTERVAL_EVERY] = 1
            d[CONF_INTERVAL_UNIT] = "months"
            d.setdefault(CONF_INTERVAL_ANCHOR, today)
        elif st == "yearly":
            d[CONF_INTERVAL_EVERY] = 1
            d[CONF_INTERVAL_UNIT] = "years"
            d.setdefault(CONF_INTERVAL_ANCHOR, d.get(CONF_ANNIVERSARY_DATE) or today)
        elif st == "interval":
            d.setdefault(CONF_INTERVAL_EVERY, 1)
            d.setdefault(CONF_INTERVAL_UNIT, "months")
            d.setdefault(CONF_INTERVAL_ANCHOR, today)

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
                CONF_SCHEDULE_TYPE, default=d.get(CONF_SCHEDULE_TYPE, "repeating")
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "Repeating (every N days / weeks / months / years)", "value": "repeating"},
                        {"label": "One-time", "value": "once"},
                        {"label": "Condition (template / accumulator / threshold)", "value": "condition"},
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
        today_iso = dt_util.now().date().isoformat()
        if user_input is not None:
            self._data.update(user_input)
            if stype == "condition":
                return await self.async_step_condition_detail()
            if stype == "once":
                return await self.async_step_behavior()
            # repeating — per-unit detail
            unit = self._data.get(CONF_INTERVAL_UNIT)
            if unit == "weeks":
                return await self.async_step_repeat_weekdays()
            if unit == "months":
                return await self.async_step_repeat_monthly()
            return await self.async_step_behavior()

        d = self._data
        time_field = {
            vol.Required(
                CONF_SCHEDULE_TIME, default=d.get(CONF_SCHEDULE_TIME, "09:00")
            ): selector.TimeSelector(),
        }
        if stype == "condition":
            schema = vol.Schema({
                vol.Required(
                    CONF_CONDITION_MODE, default=d.get(CONF_CONDITION_MODE, "template"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"label": "Template (Jinja)", "value": "template"},
                            {"label": "Accumulator (device data since done)", "value": "accumulator"},
                            {"label": "Threshold (live sensor crosses a value)", "value": "threshold"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            })
        elif stype == "once":
            schema = vol.Schema({
                vol.Required(
                    CONF_ONCE_DATE,
                    default=d.get(CONF_ONCE_DATE) or today_iso,
                ): selector.DateSelector(),
                **time_field,
            })
        else:  # repeating
            schema = vol.Schema({
                vol.Required(
                    CONF_INTERVAL_EVERY, default=d.get(CONF_INTERVAL_EVERY, 1),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=999, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_INTERVAL_UNIT, default=d.get(CONF_INTERVAL_UNIT, "weeks"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"label": "Day(s)", "value": "days"},
                            {"label": "Week(s)", "value": "weeks"},
                            {"label": "Month(s)", "value": "months"},
                            {"label": "Year(s)", "value": "years"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_INTERVAL_ANCHOR,
                    default=d.get(CONF_INTERVAL_ANCHOR) or today_iso,
                ): selector.DateSelector(),
                **time_field,
            })
        return self.async_show_form(
            step_id="schedule",
            data_schema=schema,
            description_placeholders={"schedule_type": stype.title()},
        )

    # ── step 2a: repeating detail (weekdays / monthly) ──────────────────────

    async def async_step_repeat_weekdays(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """For 'every N weeks' — which weekdays (blank = the anchor's weekday)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_behavior()
        d = self._data
        schema = vol.Schema({
            vol.Optional(
                CONF_SCHEDULE_DAYS, default=d.get(CONF_SCHEDULE_DAYS, []),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": l, "value": k} for k, l in WEEKDAY_LABELS.items()],
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="repeat_weekdays",
            data_schema=schema,
            description_placeholders={"info": (
                "Which weekdays it lands on. Leave empty to use the start date's weekday."
            )},
        )

    async def async_step_repeat_monthly(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """For 'every N months' — a specific day-of-month or a week-pattern."""
        if user_input is not None:
            self._data.update(user_input)
            if self._data.get(CONF_SCHEDULE_MONTHLY_TYPE) == "week_pattern":
                return await self.async_step_monthly()
            return await self.async_step_behavior()
        d = self._data
        schema = vol.Schema({
            vol.Required(
                CONF_SCHEDULE_MONTHLY_TYPE,
                default=d.get(CONF_SCHEDULE_MONTHLY_TYPE, "day"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "A specific day of the month (1-31)", "value": "day"},
                        {"label": "A week pattern (e.g. 1st & 3rd Wednesday)", "value": "week_pattern"},
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
            step_id="repeat_monthly",
            data_schema=schema,
            description_placeholders={"info": (
                "Pick a specific day, or a week pattern. Day 31 fires on the last day of "
                "shorter months."
            )},
        )

    async def async_step_monthly(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_behavior()

        d = self._data
        _wk = d.get(CONF_SCHEDULE_MONTHLY_WEEK, ["first"])
        if isinstance(_wk, str):
            _wk = [_wk]
        schema = vol.Schema({
            vol.Required(
                CONF_SCHEDULE_MONTHLY_WEEK, default=_wk
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": l, "value": k} for k, l in MONTHLY_WEEK_LABELS.items()],
                    multiple=True,
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

    # ── step 2b: condition detail (template / accumulator / threshold) ───────

    async def async_step_condition_detail(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        mode = self._data.get(CONF_CONDITION_MODE, "template")
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_behavior()

        d = self._data
        if mode == "accumulator":
            schema = vol.Schema({
                vol.Required(
                    CONF_ACCUM_SOURCE,
                    description={"suggested_value": d.get(CONF_ACCUM_SOURCE)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number", "counter"])
                ),
                vol.Required(
                    CONF_ACCUM_LIMIT,
                    description={"suggested_value": d.get(CONF_ACCUM_LIMIT)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, step="any", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_ACCUM_RESET_ON_DONE,
                    default=d.get(CONF_ACCUM_RESET_ON_DONE, True),
                ): bool,
            })
            info = (
                "Due when the source climbs by the limit since the last completion. "
                "Reset-on-done ON = the source is a lifetime/monotonic value and the "
                "engine re-baselines on completion (no external reset needed). "
                "OFF = the source resets itself, so due = value ≥ limit."
            )
        elif mode == "threshold":
            schema = vol.Schema({
                vol.Required(
                    CONF_THRESHOLD_ENTITY,
                    description={"suggested_value": d.get(CONF_THRESHOLD_ENTITY)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(
                    CONF_THRESHOLD_BELOW,
                    description={"suggested_value": d.get(CONF_THRESHOLD_BELOW)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(step="any", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_THRESHOLD_ABOVE,
                    description={"suggested_value": d.get(CONF_THRESHOLD_ABOVE)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(step="any", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_THRESHOLD_HYSTERESIS,
                    description={"suggested_value": d.get(CONF_THRESHOLD_HYSTERESIS)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, step="any", mode=selector.NumberSelectorMode.BOX)
                ),
            })
            info = (
                "Due when the sensor drops to/below 'below' (or rises to/above 'above') "
                "— set one. Hysteresis is the recovery buffer before it clears, to stop flapping."
            )
        else:  # template
            schema = vol.Schema({
                vol.Required(
                    CONF_DUE_TEMPLATE,
                    description={"suggested_value": d.get(CONF_DUE_TEMPLATE)},
                ): selector.TemplateSelector(),
            })
            info = (
                "Jinja template — the reminder is due whenever it renders true. "
                "Variables days_since_done and last_done are available."
            )
        return self.async_show_form(
            step_id="condition_detail",
            data_schema=schema,
            description_placeholders={"info": info},
        )

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
            vol.Required(CONF_MANDATORY, default=d.get(CONF_MANDATORY, False)): bool,
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

        if stype == "repeating":
            config[CONF_INTERVAL_EVERY] = int(d.get(CONF_INTERVAL_EVERY, 1))
            config[CONF_INTERVAL_UNIT] = d.get(CONF_INTERVAL_UNIT, "weeks")
            config[CONF_INTERVAL_ANCHOR] = d.get(CONF_INTERVAL_ANCHOR)
            unit = config[CONF_INTERVAL_UNIT]
            if unit == "weeks":
                config[CONF_SCHEDULE_DAYS] = d.get(CONF_SCHEDULE_DAYS, [])
            elif unit == "months":
                mtype = d.get(CONF_SCHEDULE_MONTHLY_TYPE, "day")
                config[CONF_SCHEDULE_MONTHLY_TYPE] = mtype
                if mtype == "day":
                    config[CONF_SCHEDULE_MONTHLY_DAY] = d.get(CONF_SCHEDULE_MONTHLY_DAY, 1)
                else:
                    config[CONF_SCHEDULE_MONTHLY_WEEK] = d.get(CONF_SCHEDULE_MONTHLY_WEEK)
                    config[CONF_SCHEDULE_MONTHLY_WEEKDAY] = d.get(CONF_SCHEDULE_MONTHLY_WEEKDAY)
        elif stype == "weekly":
            config[CONF_SCHEDULE_DAYS] = d.get(CONF_SCHEDULE_DAYS, [])
        elif stype == "interval":
            config[CONF_INTERVAL_EVERY] = int(d.get(CONF_INTERVAL_EVERY, 1))
            config[CONF_INTERVAL_UNIT] = d.get(CONF_INTERVAL_UNIT, "months")
            config[CONF_INTERVAL_ANCHOR] = d.get(CONF_INTERVAL_ANCHOR)
        elif stype == "once":
            config[CONF_ONCE_DATE] = d.get(CONF_ONCE_DATE)
        elif stype == "yearly":
            config[CONF_ANNIVERSARY_DATE] = d.get(CONF_ANNIVERSARY_DATE)
        elif stype == "condition":
            mode = d.get(CONF_CONDITION_MODE, "template")
            config[CONF_CONDITION_MODE] = mode
            if mode == "accumulator":
                config[CONF_ACCUM_SOURCE] = d.get(CONF_ACCUM_SOURCE)
                config[CONF_ACCUM_LIMIT] = d.get(CONF_ACCUM_LIMIT)
                config[CONF_ACCUM_RESET_ON_DONE] = d.get(CONF_ACCUM_RESET_ON_DONE, True)
            elif mode == "threshold":
                config[CONF_THRESHOLD_ENTITY] = d.get(CONF_THRESHOLD_ENTITY)
                for k in (CONF_THRESHOLD_BELOW, CONF_THRESHOLD_ABOVE, CONF_THRESHOLD_HYSTERESIS):
                    if d.get(k) not in (None, ""):
                        config[k] = d.get(k)
            else:
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
            CONF_NAG, CONF_MANDATORY, CONF_ALLOW_CRITICAL, CONF_OPTIONAL, CONF_UNTIL_DONE, CONF_LEAD_TIMES,
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
