# Actionable Reminders

A source-agnostic reminder engine for Home Assistant. Define reminders once (in
the UI), and let the engine handle *when* to prompt, how hard to nag, and how to
track completion — driven either by its own schedules or by any automation via
services.

> Custom integration, config-flow based. Domain: `actionable_reminders`.

## Why

Recurring, one-time, and app/condition-driven reminders usually end up scattered
across calendars, phone reminders, and one-off automations. This integration is
a single engine that any input can feed:

- **Recurring** — give a reminder a daily / weekly / monthly schedule.
- **App / condition-driven** — an automation calls `force_prompt` when a
  condition is met (e.g. HVAC filter runtime over threshold) and `mark_done`
  when it's resolved.
- **One-time** — `force_prompt` a reminder on demand.

## Features

- **Config-flow UI** — a hub entry for global defaults, plus one config entry
  per reminder. No YAML.
- **Schedules** — daily, weekly, monthly (day-of-month or nth-weekday).
- **Escalating nagging** — retry interval → max retries → escalation (louder,
  faster) → auto-skip → restart the next morning. Per-reminder overrides of the
  hub defaults.
- **Presence + quiet hours** — gate prompts on presence sensors and a quiet
  window; optional **catch-up on arrival** so a missed reminder fires when you
  get home.
- **`until_done`** — an occurrence nobody answered carries to the following
  days until it's marked done, instead of being lost until the next scheduled
  date. `mandatory` is the within-a-day counterpart: it never auto-gives-up.
- **Self-resolving conditions** — when a condition reminder's anchor clears on
  its own (you did the thing without answering the prompt), the completion is
  recorded and the delivered notification is retracted, rather than the reminder
  just going quiet with nothing logged.
- **Varied messaging** — lists of prompt / ack / dismiss messages.
- **Per-reminder state tracking** — last prompt, last done, retries today,
  escalation state, auto-skip.
- **Audit journal** — every close-out is logged with how it happened:
  `done`, `skip`, `dismiss`, `auto_skip`, or `resolved` (cleared by itself).
- Each reminder is exposed as a **switch** entity.

## Services

| Service | Data | Purpose |
|---|---|---|
| `actionable_reminders.mark_done` | `entry_id` | Mark the reminder done for today |
| `actionable_reminders.dismiss` | `entry_id` | Dismiss the current prompt |
| `actionable_reminders.skip_today` | `entry_id` | Skip today |
| `actionable_reminders.force_prompt` | `entry_id` | Prompt now — the hook for external / condition-driven inputs |
| `actionable_reminders.snooze` | `entry_id`, `duration` | Defer until the duration elapses (refused for mandatory) |
| `actionable_reminders.reschedule_next` | `entry_id`, `date` | Move the next due date (scheduled reminders only) |
| `actionable_reminders.set_accumulator_baseline` | `entry_id`, `baseline` | Re-anchor an accumulator's counter without completing |
| `actionable_reminders.create_reminder` | `name`, `schedule_type`, … | Create a reminder by voice / automation; returns the new `entry_id` |

### Creating reminders programmatically

`create_reminder` builds the same reminder object the config wizard writes and
returns the new `entry_id` in its response. Examples:

```yaml
# One-time
action: actionable_reminders.create_reminder
data:
  name: Call the plumber
  schedule_type: once
  date: "2026-08-01"
  time: "10:00"

# Repeating — every 2 weeks on Saturday
action: actionable_reminders.create_reminder
data:
  name: Water the ferns
  schedule_type: repeating
  every: 2
  unit: weeks
  weekdays: [sat]

# Yearly anniversary / birthday
action: actionable_reminders.create_reminder
data:
  name: Mom's Birthday
  schedule_type: yearly
  date: "1955-04-01"

# Condition (due while a template is truthy)
action: actionable_reminders.create_reminder
data:
  name: Refill water softener
  schedule_type: condition
  due_template: "{{ is_state('binary_sensor.softener_low','on') }}"
  response_variable: new_reminder   # -> {entry_id, name}
```

Prompt messages are rendered as templates, so a condition reminder can say
which entity tripped it instead of listing everything it watches:

```yaml
message: >
  {% set dark = expand('sensor.a','sensor.b')
       | selectattr('state','in',['unavailable','unknown'])
       | map(attribute='name') | list %}
  Data source dark: {{ dark | join(', ') }}.
```

They render against the same `days_since_done` / `last_done` variables as
`due_template`. A message with no `{{` or `{%` in it is used as-is, and one
that fails to render falls back to its own text rather than going silent.

## Notifications

- **Mobile** — actionable notifications with Done / Not-Yet buttons.
- **Alexa** — announce is implemented; actionable (voice-capture) is not yet
  built in (`_send_alexa_actionable` currently falls back to announce). Voice
  acknowledgement can be provided by routing prompts through an external
  notification script that owns the Alexa actionable round-trip, with its
  confirm action calling `actionable_reminders.mark_done`.

## Installation (HACS)

Add this repository as a custom repository (category: Integration), install,
restart Home Assistant, then add the **Actionable Reminders** integration to
create the hub, and add reminders from the integration's options.

## Status

Version 0.3.2. Actively used; the Alexa actionable path is the main open item
(see Notifications above).
