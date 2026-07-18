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
- **`until_done` / `optional`** — keep prompting until acknowledged, or allow
  auto-skip.
- **Varied messaging** — lists of prompt / ack / dismiss messages.
- **Per-reminder state tracking** — last prompt, last done, retries today,
  escalation state, auto-skip.
- Each reminder is exposed as a **switch** entity.

## Services

| Service | Data | Purpose |
|---|---|---|
| `actionable_reminders.mark_done` | `entry_id` | Mark the reminder done for today |
| `actionable_reminders.dismiss` | `entry_id` | Dismiss the current prompt |
| `actionable_reminders.skip_today` | `entry_id` | Skip today |
| `actionable_reminders.force_prompt` | `entry_id` | Prompt now — the hook for external / condition-driven inputs |

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
