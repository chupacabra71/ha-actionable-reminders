# Actionable Reminders v2 — Design

Status: **draft for review** · Supersedes the v0.3.2 first attempt · Target: current Home Assistant (2026.x)

---

## 1. Purpose

A single, source-agnostic **reminder engine** for Home Assistant. Anything can
create a reminder — a schedule, a runtime/condition, a calendar event, a voice
command, another automation — and the engine owns the hard, common part:
deciding *when* to surface it, *how hard* to nag, and *tracking* completion,
with acknowledgement by **voice or tap**.

The guiding principle, learned the hard way: **the engine is a bus, not a
source.** Sources are thin and pluggable. Delivery is pluggable. The core only
knows about a normalized *Reminder*.

---

## 2. Requirements

### 2.1 Functional (from the operator, across the whole thread)

| # | Requirement |
|---|---|
| F1 | **Many input types, one system:** recurring (daily/weekly/monthly/interval/yearly), one-time, **app/condition-driven** (e.g. HVAC filter by *runtime hours*, vacuum maintenance), and calendar-driven. |
| F2 | **Dynamic creation:** any automation/app can create or raise a reminder at runtime via a service — not only pre-defined config. |
| F3 | **Single place to manage** everything; stop scattering across Google Calendar, phone, Node-RED, and one-off automations. |
| F4 | **Spouse-friendly entry:** she lives in **Google Calendar**, won't use dashboards. Adding a reminder must be possible *from her calendar*. |
| F5 | **Nagging that's hard to ignore:** persistent, **escalating**, surfaced at **natural moments** (arrival, TV on, kitchen motion) rather than a predictable clock alarm. |
| F6 | **Completion tracking** with acknowledgement. |
| F7 | **Voice notify + voice ack** (Alexa announce + spoken "yes"), plus mobile. |
| F8 | **Per-reminder completion side-effects** (e.g. "done" on the filter resets its runtime counter and stamps the date). |

### 2.2 Non-functional / HA-native

- **UI-managed** via config flow / options flow (no YAML for the user). HA best practice.
- **Native entities** so it plugs into dashboards, Assist voice, and automations.
- **Services + events** for programmatic control and observability.
- **Presence / quiet-hours / focus-DND aware**; never talk to an empty house or mid-sleep.
- **Restart-safe** state; **diagnostics**; **translations**; **HACS-installable**; **version-controlled**.
- Reuse the **proven delivery layer** (`script.unified_notifications`) rather than re-implementing voice/mobile actionable.

### 2.3 Non-goals

- Not a full task manager / project tool. Reminders, not Jira.
- No gamification (points/badges).
- Not trying to replace Google Calendar as *her* surface — we integrate with it.
- No dependence on room-level presence (it's unreliable here).

---

## 3. Learnings that shape the design (from v0.3.2 + this session's testing)

1. **Alexa voice-ack is device-specific.** It launches an Alexa *skill* on one device that speaks **and** listens. So actionable prompts must target a **single** device (living-room Echo). Ack still arrives as a global event, but the *prompt* is single-device.
2. **Group announce is bad for actionable:** targeting an "all devices" group gives **staggered** audio and **breaks** the ack (can't launch the skill on a group). A true Alexa **announcement** (`type: announce`) *is* synchronized but is **not** actionable.
3. **`unified_notifications` already solves delivery:** voice (single Echo) + mobile + actionable + focus/guest muting + missed-notification ledger + who-routing. Don't rebuild it; call it.
4. **No reliable room presence** → device routing is a **fixed** device, not presence-based. Person-level home/away *is* reliable.
5. **Contextual "opportunity" triggers beat clock times** for not-being-ignored: arrival-and-settled, TV-on, kitchen-motion — gated by a **cooldown** so passing a sensor 5× doesn't nag 5×.
6. **Chore Helper can't hold a runtime-based "due"** (blank/manual chores clear their dates every 10s). Date-recurrence tools can't model condition-driven reminders — the engine must model due-ness itself.
7. **v0.3.2's ack loop was never closed** — it emits mobile action buttons but nothing listens for them; Alexa actionable is a stub. The engine must own (or delegate) the full round-trip.
8. **"After completion" vs "fixed" recurrence matters** (a late dose should push the next one out, not clump). Recurrence needs an **anchor** choice.

---

## 4. Architecture

```
        SOURCES (pluggable adapters)              ENGINE CORE                 CHANNELS (pluggable delivery)
   ┌───────────────────────────────┐      ┌───────────────────────┐      ┌──────────────────────────────┐
   │ schedule   (daily/…/interval/ │      │  Reminder Store       │      │ unified_notifications (default)│
   │            once, anchor)      │─────▶│  (normalized model +  │─────▶│   → voice (1 Echo) + mobile    │
   │ condition  (template/entity   │ set/ │   runtime state)      │ due  │   → actionable, ack owned      │
   │            due, e.g. filter)  │ raise│                       │prompt│ alexa_announce (synchronized,  │
   │ service    (create/force)     │      │  Scheduler / Lifecycle│      │   awareness only)              │
   │ calendar   (Google/local)     │      │   • due detection     │      │ mobile / persistent (fallback) │
   │ todo       (HA to-do items)   │      │   • opportunity gate  │      └──────────────────────────────┘
   └───────────────────────────────┘      │   • cooldown          │                    │
                                          │   • escalation ladder │        ACK (done/dismiss/snooze/skip)
        SINKS (write-back)                │   • quiet/presence    │◀───────────────────┘
   ┌───────────────────────────────┐      │   • completion + hooks│      voice "yes" · mobile tap ·
   │ Google Calendar (derived      │◀─────│                       │      todo complete · button · service
   │   events she sees)            │      └───────────────────────┘
   │ on_complete actions (reset    │                 │
   │   filter runtime, etc.)       │        HA SURFACE: config-entry + subentries,
   └───────────────────────────────┘        sensor/button/todo/calendar entities, services, events
```

Three clean seams: **Source → Engine** (a source only has to make a reminder
*exist* and/or *become due*), **Engine → Channel** (a channel only has to
*deliver a prompt* and route an *ack* back), and **Engine → Sink** (write-backs
and completion side-effects).

---

## 5. The Reminder model (the one thing the core knows)

A normalized record. Config-flow reminders and service-created reminders are the
same shape; only their `source` differs.

```yaml
id:            stable unique id
name:          "Dogs – Flea Meds"
enabled:       true
source:        schedule | condition | service | calendar | todo

# ── Due specification (interpreted per source) ─────────────────────────────
schedule:                     # source=schedule
  type:   daily | weekly | monthly | interval | yearly | once
  time:   "18:00"             # preferred time-of-day (opportunity still applies)
  # per type: days[], day_of_month, nth_weekday, interval{n,unit}, once_datetime
  anchor: fixed | after_completion     # "every" vs "after" — late done pushes next out
condition:                    # source=condition
  due_template:   "{{ states('input_number.filter_runtime_upstairs')|float
                     > states('input_number.filter_runtime_threshold_upstairs')|float }}"
  resolved_template: null     # optional: auto-clear when true
calendar:                     # source=calendar
  entity_id: calendar.reminders
  match:     {on_calendar: true}   # or {keyword: "#remind"}

# ── Messaging (varied, rotated) ────────────────────────────────────────────
messages:
  prompt:  ["Time to give the dogs their flea meds.", "Dogs' flea meds are due."]
  ack:     ["Nice, logged it."]
  dismiss: ["Okay, I'll remind you again."]

# ── Delivery ───────────────────────────────────────────────────────────────
channel:          unified_notifications        # default
who:              all                           # seb | ida | all
actionable:       true
alexa_device:     media_player.living_room_echo # SINGLE device for voice-ack
announce_devices: []                            # optional synchronized awareness announce

# ── Surfacing / nagging policy (hub defaults, per-reminder overrides) ───────
opportunity_triggers:                           # "natural moments" to surface
  - {entity: person.sebastian, to: home, for: "00:20:00"}
  - {entity: person.ida,       to: home, for: "00:20:00"}
  - {entity: media_player.big_tv_samsung_smart, from: "off"}
  - {entity: binary_sensor.kitchen_motion_any,  to: "on"}
fixed_times:      ["19:00"]                      # clock fallback
cooldown:         "03:00:00"
escalation:
  retry_interval: 30m   max_retries: 5
  escalation_interval: 15m   max_escalations: 5   escalation_volume: 0.8
  on_exhaust: defer_next_day | keep_until_done   # optional reminders may auto-skip
availability:
  presence_required: true                         # someone home
  quiet_hours: {start: "21:30", end: "08:00"}
  respect_focus: true

# ── Completion semantics ───────────────────────────────────────────────────
until_done:   true
optional:     false
on_complete:                                      # generic side-effects (fixes the filter case)
  - action: input_button.press
    target: {entity_id: input_button.filter_replaced_upstairs}

# ── Runtime state (engine-owned, persisted) ────────────────────────────────
state:        idle | due | prompting | escalated | overdue | snoozed | done | skipped
next_due, last_prompt, last_done, retries_today, escalations_today, snoozed_until
```

Everything the operator asked for maps onto this **one** model:

| Input | How it's expressed |
|---|---|
| Flea meds (monthly, from last dose) | `source: schedule, type: monthly, anchor: after_completion` |
| One-time | `source: schedule, type: once` (or a calendar event) |
| **AC filter (runtime hrs)** | `source: condition, due_template: runtime>threshold, on_complete: press reset button` |
| Vacuum maintenance | `source: condition` |
| Wife's ad-hoc | `source: calendar` on the shared Google calendar |
| App-driven at runtime | `create_reminder` service → `source: service` |

---

## 6. Sources (input adapters)

Each source's only job: make a reminder **exist** and signal when it's **due**.

- **schedule** — computes `next_due` from the schedule + `anchor`. `after_completion` recomputes from `last_done`; `fixed` from an epoch. Handles `once`.
- **condition** — evaluates `due_template` on relevant state changes / a slow tick; due when true, auto-clears on `resolved_template`. This is the general home for **runtime/app-driven** reminders (filter, vacuum) — no Chore Helper needed.
- **service** — `create_reminder` / `raise_reminder` services let any automation spin one up dynamically (one-time or recurring). Satisfies F2.
- **calendar** — watches a HA calendar (Google Calendar, read-write). Events on the **shared "Reminders" calendar** (or tagged) become reminders; RRULE events become recurring reminders. Satisfies F4.
- **todo** — optional: items on a HA To-do list with due dates become reminders (nice for Assist voice: "add milk reminder").

---

## 7. Scheduling & lifecycle

A per-reminder state machine, driven by a lightweight coordinator (tick + event
subscriptions), **not** a busy loop.

```
 idle ──(source says due)──▶ due ──(opportunity within availability + cooldown)──▶ prompting
   ▲                                                                                │
   │                                        (retry_interval, no ack) ──────────────┤
   │                                        after max_retries ──▶ escalated ◀───────┘
   │                                          (louder, rotate devices, faster)
   │                                        after max_escalations ──▶ on_exhaust
   │                                                                   ├─ optional → skipped(today)
   │                                                                   └─ until_done → defer to next opportunity/day
   └────────────────────────── done ◀── ack("yes"/tap/complete) ── run on_complete, compute next_due
                               snoozed ◀── "not yet"/snooze(duration) ── re-surface later
```

- **Due detection** is source-specific (§6). "Due" ≠ "prompt now."
- **Opportunity gate** — when due, wait for an `opportunity_trigger` (arrival/TV/kitchen) **or** a `fixed_time`, subject to `availability` (presence, quiet hours, focus) and `cooldown`. This is the anti-ignore mechanism (F5). `catchup_on_arrival` is just an opportunity trigger.
- **Escalation ladder** — retry → escalate (higher volume, more/rotated devices, shorter interval) → exhaust. Per-reminder overrides of hub defaults.
- **Completion** — ack computes the next `next_due` (schedule anchor) or clears (condition/once), runs `on_complete` side-effects (F8), and optionally writes back to the calendar.

---

## 8. Delivery & acknowledgement

**Delivery is delegated.** The default channel calls `script.unified_notifications`;
the engine never re-implements voice/mobile plumbing.

```python
# default channel = unified_notifications
script.unified_notifications(
  method="all", who=<who>,
  alexa_device=<single Echo>,             # actionable → one device (learning #1/#2)
  severity="TIME-SENSITIVE" | "CRITICAL"  # escalation bumps severity
  title="🔔 Reminder", message=<prompt>,  # self-prompting ("... say yes when done")
  tag=f"ar_{id}",
  confirm_text="Done",  confirm_action=[actionable_reminders.mark_done(id)],
  dismiss_text="Not yet", dismiss_action=[actionable_reminders.dismiss(id)],
)
```

- **Voice-ack** works because `unified_notifications` owns the Alexa actionable
  round-trip on a single Echo; "Done" → `mark_done`. This *closes the loop v0.3.2
  left open* and *finishes the Alexa stub* — without re-implementing either.
- **Optional synchronized awareness announce:** for reminders that should be
  *heard* house-wide, additionally fire a non-actionable `type: announce` to
  `announce_devices` (synchronized), while the actionable prompt still targets one
  Echo. "Heard everywhere, acked on one."
- **Belt-and-suspenders ack listener:** the engine *also* subscribes to
  `mobile_app_notification_action` (`ar_done_{id}`/`ar_dismiss_{id}`) and
  `alexa_actionable_notification`, so dashboards, other channels, and raw notifies
  all resolve to the same `mark_done`/`dismiss` — every ack route converges.
- **Ack routes (all equivalent):** voice "yes" · mobile tap · dashboard button ·
  `mark_done` service · completing the linked todo/calendar item.

---

## 9. Home Assistant surface

- **Config entry = the hub** (global defaults: default channel, default Echo,
  presence entities, opportunity triggers, quiet hours, escalation defaults,
  Google calendar entity).
- **Reminders = config subentries** of the hub (modern HA subentry flow) — one
  managed surface, add/edit/remove in the UI. (v0.3.2 used one entry per
  reminder; subentries are cleaner. — *decision, §15*.)
- **Entities per reminder:**
  - `sensor.<name>` — state = lifecycle status; attributes = next_due, last_done, retries, current message. (Dashboards/automations.)
  - `button.<name>_done` / `_snooze` / `_skip` — action hooks.
  - Aggregate `todo.reminders` — **all active reminders as a native To-do list** (management + Assist voice), completion mirrors `mark_done`.
  - Aggregate `calendar.reminders_upcoming` — upcoming due dates (dashboard; feeds Google sync).
- **Services:** `mark_done`, `dismiss`, `snooze(id, duration)`, `skip_today`,
  `force_prompt`, **`create_reminder`**, **`update_reminder`**, `remove_reminder`,
  `set_due(id)` / `clear(id)` (for condition/service sources).
- **Events:** `actionable_reminders_prompted|escalated|done|dismissed|skipped`
  `{id, name}` for observability and external hooks.
- Coordinator pattern, diagnostics, repairs (flag misconfig e.g. missing Echo),
  translations.

---

## 10. Google Calendar integration (the wife's surface)

Two-way, HA-native (read-write Google Calendar integration).

- **Inbound (she creates):** a dedicated shared **"Reminders"** calendar — *any*
  event on it becomes a reminder (no tag discipline); recurring events (RRULE)
  become recurring reminders. She adds on her phone, gets Google's native
  notification *and* the engine's voice/mobile nagging.
- **Outbound (engine writes):** condition-based and derived reminders (e.g. "AC
  filter due", next scheduled occurrence) are written to the shared calendar so
  she sees them; on `done`, the event is updated/removed.
- **Completion from her side:** deleting/completing the event → `mark_done`
  (calendar source watches for the event disappearing); or she just answers the
  voice/mobile prompt like anyone else.

---

## 11. Management surfaces

| Who | Surface |
|---|---|
| **Wife** | Google Calendar (add events) — nothing else required |
| **Operator** | Config-flow UI (managed reminders) + a dashboard (todo list + status sensors + buttons) + services (dynamic) |
| **Voice (Assist)** | "what reminders do I have", "mark flea meds done", "remind me to X tomorrow" via the `todo` entity + custom sentences |
| **Other apps/automations** | `create_reminder` / `force_prompt` / `set_due` services |

---

## 12. State & persistence

- Runtime state lives in the config subentry (or a coordinator-backed store),
  restart-restored. Timestamps (`last_done`, `next_due`, `snoozed_until`) are the
  source of truth — no fragile `for:`-duration clocks.
- Condition sources keep due-ness derived from live state, so they self-heal
  across restarts.

---

## 13. Migration

- **From v0.3.2:** keep what's good (config-flow skeleton, escalation math,
  state model, presence/quiet-hours). Restructure into Source/Engine/Channel
  seams; add the `unified_notifications` channel (closes the ack loop + finishes
  Alexa); add `condition`/`calendar`/`service` sources; move from switch-only to
  the sensor/button/todo/calendar entity model; add dynamic-create services.
- **Retire this session's Chore-Helper scaffolding** once the schedule source
  covers flea-meds: delete `sensor.reminder_dogs_flea_meds` (chore),
  `automation.reminder_engine_dispatch_due_reminders`,
  `automation.reminder_engine_notify_ack`, `input_datetime.reminder_last_nag`,
  and (optionally) the Chore Helper HACS integration.
- **AC filter:** becomes a `condition` reminder (`due_template` = runtime >
  threshold, `on_complete` = press `input_button.filter_replaced_*`). Keep the
  accumulator/threshold/reset helpers exactly as-is.

---

## 14. Phased build plan

1. **Core + schedule source + unified_notifications channel.** Reminder model,
   lifecycle state machine, opportunity/cooldown/escalation, mark_done/dismiss/
   snooze services, sensor+button entities. Prove with flea-meds (schedule,
   after_completion). → replaces the Chore-Helper pilot.
2. **Condition source + on_complete + dynamic services.** Migrate the AC filter;
   add `create_reminder`. Fold in vacuum maintenance.
3. **todo + calendar entities + Assist sentences.** Operator dashboard + voice
   management.
4. **Google Calendar source/sink.** Wife's inbound events + outbound derived
   events + calendar-side completion.
5. **Polish:** synchronized awareness announce, diagnostics, repairs, README/HACS.

Each phase is independently useful and independently testable.

---

## 15. Open decisions (need a call before/while building)

1. **Subentries vs per-reminder config entries** — recommend **subentries**
   (one clean hub surface). Slightly newer API; v0.3.2 uses per-entry.
2. **Rewrite vs refactor v0.3.2** — recommend **refactor toward this** (reuse
   escalation/state/config-flow), not greenfield.
3. **Aggregate `todo` as the primary operator UI?** — recommend yes; it's the
   most HA-native "single list" and gives Assist voice for free.
4. **Announce scope default** — single Echo (reliable) with optional per-reminder
   synchronized awareness announce. (Confirmed: no group-actionable.)
5. **Domain/name** — keep `actionable_reminders`, or rename (e.g. `reminders`,
   `nag`)? Keeping it avoids a migration.
6. **How much of `unified_notifications`' behavior to hard-depend on** — it's the
   default channel, but the engine should degrade to plain notify if it's absent
   (keeps the integration shareable).
