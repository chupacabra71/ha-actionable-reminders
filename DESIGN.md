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
| F4 | **Low-friction calendar entry:** a household member who lives in **Google Calendar** and won't use dashboards must be able to add a reminder *from their calendar*. |
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
- **No gamification** — points/rewards economy, badges, leaderboards, competitive
  streaks, or **overdue penalties** (KidsChores/Sweepy/Nipto/OurHome). Motivation
  mechanics for kids; noise for an adult household. (We keep the *audit log*, drop
  the scoreboard.)
- **No parent-approval / claim→approve** two-tier authority — the ack *is* completion.
- **No inventory/pantry auto-consumption** (Grocy) — out of scope until reminders
  couple to stock, if ever.
- Not trying to replace Google Calendar as *their* surface — we integrate with it.
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
   │   events they see)           │      └───────────────────────┘
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
  due_template:   "{{ states('sensor.hvac_runtime_hours')|float
                     > states('input_number.hvac_runtime_limit')|float }}"
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
who:              all                           # e.g. alice | bob | all
actionable:       true
alexa_device:     media_player.living_room_echo # SINGLE device for voice-ack
announce_devices: []                            # optional synchronized awareness announce

# ── Surfacing / nagging policy (hub defaults, per-reminder overrides) ───────
opportunity_triggers:                           # "natural moments" to surface
  - {entity: person.alice, to: home, for: "00:20:00"}
  - {entity: person.bob,   to: home, for: "00:20:00"}
  - {entity: media_player.living_room_tv, from: "off"}
  - {entity: binary_sensor.kitchen_motion, to: "on"}
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
    target: {entity_id: input_button.filter_reset}

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
| Ad-hoc (calendar) | `source: calendar` on the shared Google calendar |
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

## 10. Google Calendar integration (the calendar-user's surface)

Two-way, HA-native (read-write Google Calendar integration).

- **Inbound (they create):** a dedicated shared **"Reminders"** calendar — *any*
  event on it becomes a reminder (no tag discipline); recurring events (RRULE)
  become recurring reminders. They add on their phone, get Google's native
  notification *and* the engine's voice/mobile nagging.
- **Outbound (engine writes):** condition-based and derived reminders (e.g. "AC
  filter due", next scheduled occurrence) are written to the shared calendar so
  they see them; on `done`, the event is updated/removed.
- **Completion from their side:** deleting/completing the event → `mark_done`
  (calendar source watches for the event disappearing); or they just answer the
  voice/mobile prompt like anyone else.

---

## 11. Management surfaces

| Who | Surface |
|---|---|
| **Calendar user** | Google Calendar (add events) — nothing else required |
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
  threshold, `on_complete` = press `input_button.filter_reset`). Keep the
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
4. **Google Calendar source/sink.** Inbound calendar events + outbound derived
   events + calendar-side completion.
5. **Polish:** synchronized awareness announce, diagnostics, repairs, README/HACS.

Each phase is independently useful and independently testable.

---

## 15. Decisions (locked 2026-07-18)

1. **Config model:** **subentries** — one hub config entry, reminders as config
   subentries (clean single surface). *Sequencing:* the incremental refactor may
   keep v0.3.2's per-entry model briefly, migrating to subentries before Phase 1
   closes.
2. **Refactor, not greenfield** — evolve v0.3.2 in place: keep its escalation
   math, state model, and config-flow skeleton; restructure into Source/Engine/
   Channel seams and add the missing pieces.
3. **Aggregate `todo.reminders` is the primary operator UI** — most HA-native
   "single list," Assist voice for free; sensors/buttons are secondary hooks.
4. **Announce scope:** single Echo (`media_player.living_room_echo`) for the
   actionable prompt; optional per-reminder synchronized awareness announce. No
   group-actionable (proven broken).
5. **Domain name stays `actionable_reminders`** — avoids a breaking migration;
   the repo/title carry the friendlier name.
6. **`unified_notifications` is the default channel but a soft dependency** — the
   engine degrades to plain `notify` + `alexa_media` announce if it's absent, so
   the integration stays shareable.

### First build step (Phase 1, step 0)
Wire `_send_prompt` → `script.unified_notifications` (confirm→`mark_done`,
dismiss→`dismiss`). This closes v0.3.2's open ack loop and delivers voice-ack
immediately — the smallest change that makes the current engine work end-to-end —
before the larger Source/Channel restructure.

---

## 16. Research-informed enhancements

Folded in after comparing against Tody, Sweepy, Tidywell, OurHome, Grocy,
KidsChores, Chore Helper, Home Maintenance, **Maintenance Supporter**, HomeRoutines,
and Todoist. These *extend* the sections above.

### 16.1 Accumulator / sensor-metered due — the differentiator (extends §6 condition)

The single biggest idea most calendar-based apps lack (Maintenance Supporter does
it): bind "due" to **real device data since last completion**, not a clock.
Generalize the `condition` source into richer **due anchors**:

```yaml
condition:
  mode: template | accumulator | threshold | counter | compound
  # accumulator: integrate a value since last completion, fire at a limit
  accumulator: {source: sensor.hvac_runtime_hours, limit: 450, reset_on_done: true}
  # threshold: fire when a live sensor crosses (with hysteresis)
  threshold:  {entity: sensor.filter_airflow_pct, below: 60}
  # counter: fire every N cycles/uses (power-cycles, door-opens, dishwasher runs)
  counter:    {entity: counter.dishwasher_runs, every: 30}
  # compound: AND/OR across the above
  auto_resolve: true    # auto-mark-done when the metric returns to normal
```

- **The AC filter becomes native**: `accumulator` over HVAC runtime with `limit`
  and `reset_on_done` — no external accumulator automation, no Chore Helper. This
  is exactly the F1/F8 case, first-class.
- A `due_soon` threshold (e.g. 90% of limit) feeds the lead-time nudge (§16.3).

### 16.2 Urgency-ranked *single* surfacing (extends §7 opportunity gate)

Every reminder carries a continuous **`urgency`** attribute (fraction of interval
elapsed, or overdue magnitude), and a 4-state ladder `ok → due_soon → overdue →
triggered` (Maintenance Supporter), plus a **tolerance/window** rather than a hard
date (Tody's "optimal day ± ~20%").

**When an opportunity trigger fires, surface the single highest-urgency eligible
reminder — not the whole pending list.** This is the biggest "hard to ignore
without being annoying" lever: one thing, at a good moment, most-urgent-first.
Batching handles the rest (§16.5). `urgency` also drives dashboard sort and the
digest order.

### 16.3 Softer, smarter lifecycle rungs (extends §7 state machine)

- **`due_soon` (pre-due nudge)** + **`grace` window** *before* retry→escalate. First
  contact is gentle ("coming up / just came due"), escalation only after grace.
- **Single live instance — never stack overdue occurrences** (Tody). Completing a
  late monthly reminder does **not** create a backlog; it re-anchors forward. One
  active occurrence per reminder, always.
- **Distinct completion verbs** (Chore Helper's date ops, generalized), each a service:
  - `mark_done` — accept + re-anchor (compute next due).
  - `complete_early` — accept now, re-anchor from now.
  - `skip_occurrence` — drop *this* occurrence, no penalty, **don't** shift the anchor.
  - `reschedule_next(id, date)` — move only the next due.
  - `snooze(id, duration)` — back off, re-surface later (already in §9).
  - `mark_undo(id)` — reverse the last completion (fat-finger insurance).

### 16.4 Away / vacation freeze (extends §7 availability)

Beyond presence-gating delivery: a global **away/vacation state that freezes
interval progression and overdue accrual** (Maintenance Supporter), so you don't
return from a trip to a wall of red for things that *couldn't* be done while gone.
Distinct from quiet hours (which only withholds delivery).

### 16.5 Digest + batching channel (extends §8 delivery)

- **Batch simultaneously-due reminders into ONE announcement** rather than N
  sequential Echo prompts — critical given single-Echo delivery. ("You have 3
  things due: … Which did you do?" or surface the top one + "and 2 more".)
- Optional **daily/weekly digest** of everything pending/overdue as a low-friction
  complement to per-reminder nagging (Novu batching best-practice; Maintenance
  Supporter weekly digest).

### 16.6 History / audit log + fairness (new — extends §5, §9)

- **Completion journal**: every done/skip/undo with `{ts, actor, ack_source}`.
  Exposed via a service/event and (optionally) a `sensor` attribute. Grocy-style.
  Cheap to add, painful to lack, and feeds fairness for free.
- **Optional assignment + effort** (Grocy/OurHome/Tidywell): per-reminder
  `assignee` + rotation policy (`round_robin | least_recently_done | fixed`) and an
  `effort` weight (1–5). Track **cumulative effort per person**, not task count —
  so "we each did 3" is honest when one did three 2-minute jobs. Optional even for
  a two-person house; pairs with per-person notification routing.

### 16.7 Seasonal activation (extends §5)

Optional `active_months: [apr..oct]` gate so a reminder only *exists* in season
(gutters, deck staining, AC vs. furnace filters) — Chore Helper start/end months.

### 16.8 NFC / physical-tag ack (extends §8 ack routes)

An NFC tag at the point of action (Grocy Grocycode; Home Maintenance thread) =
completion **with proof-of-presence** — genuinely "hard to ignore" and screen-free.
Add `tag` scan as another ack route converging on `mark_done`.

### 16.9 Backlog / nice-to-haves (not phase-1)

- **Seed `last_done` / first-due offset** on create so a new reminder doesn't fire
  immediately (Chore Helper).
- **Predicted next-due** for `after_completion`/accumulator reminders so the
  calendar isn't blank ("fluid" estimate).
- **Rotating focus zone** (HomeRoutines/FlyLady): a tag + weekly rotation cursor to
  surface one area at a time.
- **Completion metadata** (cost / duration / photo) → a maintenance record.
- **Checklists / subtasks + `blocked_by` dependencies** (do X before Y).
- **Quick-add natural language** as an alternate entry surface.

### 16.10 Revised "top of build order"

Fold into the Phase-1/2 plan (§14): **(A)** accumulator due anchor, **(B)** urgency-
ranked single-surface, **(C)** audit log — the three highest-value additions, none
previously in scope. Assignment/effort and away-freeze land in a later phase.
