/*
 * actionable-reminders-card — every reminder, grouped by category, editable in
 * place. Served by the integration itself (no resource to add).
 *
 *   type: custom:actionable-reminders-card
 *   categories: [Yard & Lawn, Pool]   # optional: only these
 *   show_search: true                  # default true
 *   collapsed_by_default: false        # default false
 *   title: Reminders                   # optional
 *
 * Data comes from the actionable_reminders/list websocket command and edits go
 * through actionable_reminders/update, which shares its schema and merge with
 * the update_reminder service. Plain web component, no build step, no deps.
 */

const DOMAIN = "actionable_reminders";
const UNCATEGORIZED = "Uncategorized";
const STORAGE_KEY = "actionable-reminders-card:collapsed";
const WIZARD_PATH = `/config/integrations/integration/${DOMAIN}`;

// Same ladder as dashboard/reminders-view.yaml.
const STATUS_COLOR = {
  triggered: "#ef5350",
  overdue: "#ffa726",
  due_soon: "#f0b429",
  disabled: "#9e9e9e",
  ok: "#66bb6a",
};
const STATUS_RANK = { triggered: 0, overdue: 1, due_soon: 2, ok: 3, disabled: 4 };
const STATUS_LABEL = {
  triggered: "Escalated",
  overdue: "Overdue",
  due_soon: "Due soon",
  disabled: "Off",
  ok: "OK",
};
const DUE = new Set(["triggered", "overdue", "due_soon"]);
const WEEKDAYS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
const UNITS = ["days", "weeks", "months", "years"];
const SNOOZES = [["01:00:00", "1 hour"], ["03:00:00", "3 hours"], ["24:00:00", "1 day"], ["72:00:00", "3 days"], ["168:00:00", "1 week"]];

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function loadCollapsed() {
  try {
    return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}") || {};
  } catch (_e) {
    return {};
  }
}

function saveCollapsed(map) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch (_e) {
    /* private mode / blocked storage: collapse state just isn't remembered */
  }
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const opts = { weekday: "short", month: "short", day: "numeric" };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(undefined, opts);
}

// Editor draft ← stored config. Only fields the inline editor knows about;
// everything else stays wizard-only and is never sent.
function draftFrom(r) {
  const c = r.config || {};
  const msgs = Array.isArray(c.prompt_messages) ? c.prompt_messages : [];
  return {
    name: r.name || "",
    category: r.category || "",
    enabled: r.enabled !== false,
    time: String(c.schedule_time || "09:00").slice(0, 5),
    once_date: c.once_date || "",
    anniversary_date: c.anniversary_date || "",
    every: c.interval_every ?? 1,
    unit: c.interval_unit || "weeks",
    anchor: c.interval_anchor || "",
    weekdays: Array.isArray(c.schedule_days) ? [...c.schedule_days] : [],
    monthly_type: c.schedule_monthly_type || "day",
    monthly_day: c.schedule_monthly_day ?? "",
    due_template: c.due_template || "",
    window_template: c.window_template || "",
    accumulator_limit: c.accumulator_limit ?? "",
    threshold_below: c.threshold_below ?? "",
    threshold_above: c.threshold_above ?? "",
    messages: msgs.join("\n"),
    mandatory: !!c.mandatory,
    until_done: c.until_done !== false,
    nag: c.nag !== false,
  };
}

const numOrNull = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

// Only what changed, in update_reminder's field names.
function diffChanges(r, d) {
  const o = draftFrom(r);
  const ch = {};
  const clear = [];
  const st = r.schedule_type;
  if (d.name.trim() && d.name.trim() !== o.name) ch.name = d.name.trim();
  if (d.category.trim() !== o.category) ch.category = d.category.trim();
  if (d.enabled !== o.enabled) ch.enabled = d.enabled;
  for (const k of ["mandatory", "until_done", "nag"]) if (d[k] !== o[k]) ch[k] = d[k];
  if (st !== "condition" && d.time && d.time !== o.time) ch.time = d.time;
  if (st === "once" && d.once_date && d.once_date !== o.once_date) ch.date = d.once_date;
  if (st === "yearly" && d.anniversary_date && d.anniversary_date !== o.anniversary_date) ch.date = d.anniversary_date;
  if (st === "repeating") {
    if (Number(d.every) !== Number(o.every)) ch.every = Number(d.every);
    if (d.unit !== o.unit) ch.unit = d.unit;
    if (d.anchor && d.anchor !== o.anchor) ch.anchor = d.anchor;
    if (d.unit === "weeks" && d.weekdays.slice().sort().join() !== o.weekdays.slice().sort().join()) ch.weekdays = d.weekdays;
    if (d.unit === "months" && d.monthly_type === "day" && d.monthly_day !== "" && Number(d.monthly_day) !== Number(o.monthly_day))
      ch.monthly_day = Number(d.monthly_day);
  }
  if (st === "condition") {
    const mode = r.condition_mode;
    if (mode === "template") {
      if (d.due_template.trim() && d.due_template !== o.due_template) ch.due_template = d.due_template;
      if (d.window_template !== o.window_template) {
        if (d.window_template.trim()) ch.window_template = d.window_template;
        else clear.push("window_template");
      }
    } else if (mode === "accumulator") {
      if (d.accumulator_limit !== "" && numOrNull(d.accumulator_limit) !== numOrNull(o.accumulator_limit))
        ch.accumulator_limit = Number(d.accumulator_limit);
    } else if (mode === "threshold") {
      for (const k of ["threshold_below", "threshold_above"]) {
        if (numOrNull(d[k]) === numOrNull(o[k])) continue;
        if (d[k] === "") clear.push(k);
        else ch[k] = Number(d[k]);
      }
    }
  }
  if (d.messages !== o.messages) {
    const list = d.messages.split("\n").map((s) => s.trim()).filter(Boolean);
    if (list.length) ch.messages = list;
    else ch.message = "";
  }
  if (clear.length) ch.clear = clear;
  return ch;
}

class ActionableRemindersCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._error = null;
    this._query = "";
    this._chip = null;
    this._open = null; // entry_id whose row is expanded
    this._editing = false; // editor (vs quick actions) shown in the open row
    this._draft = null;
    this._saving = false;
    this._collapsed = loadCollapsed();
    this._sig = "";
    this._dirty = false;
    this._built = false;
  }

  setConfig(config) {
    this._config = {
      show_search: true,
      collapsed_by_default: false,
      ...(config || {}),
    };
    if (this._config.categories && !Array.isArray(this._config.categories)) {
      throw new Error("categories must be a list");
    }
    if (this._built) this._renderList();
  }

  static getStubConfig() {
    return {};
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._built) this._build();
    if (first) {
      this._fetch();
      return;
    }
    // Refetch when any listed reminder's switch changes (done, snooze, an
    // edit's hub reload). State objects are replaced on change, so a cheap
    // identity signature is enough.
    const sig = this._signature();
    if (sig !== this._sig) {
      this._sig = sig;
      clearTimeout(this._refetchTimer);
      this._refetchTimer = setTimeout(() => this._fetch(), 400);
    }
  }

  _signature() {
    const reminders = this._data?.reminders || [];
    const st = this._hass?.states || {};
    return reminders
      .map((r) => {
        const s = r.entity_id && st[r.entity_id];
        return s ? `${r.entity_id}@${s.last_updated}` : r.entry_id;
      })
      .join("|") + `#${Object.keys(st).filter((k) => k.startsWith("switch.")).length}`;
  }

  async _fetch() {
    if (!this._hass) return;
    try {
      const data = await this._hass.callWS({ type: `${DOMAIN}/list` });
      // Every edit reloads the hub; mid-reload the list is briefly empty.
      // Keep what we had and look again rather than flash "no reminders".
      if (!data?.reminders?.length && this._data?.reminders?.length && !this._retrying) {
        this._retrying = true;
        setTimeout(() => this._fetch(), 1500);
        return;
      }
      this._retrying = false;
      this._data = data;
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._sig = this._signature();
    // Don't yank the editor out from under someone typing.
    if (this._editing) {
      this._dirty = true;
      return;
    }
    this._renderChips();
    this._renderList();
  }

  // ── rendering ─────────────────────────────────────────────────────────────

  _build() {
    this._built = true;
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title"></div>
          <a class="wizard" href="${WIZARD_PATH}" title="Open the integration page (full wizard, add reminder)">Full wizard ↗</a>
        </div>
        <div class="search-wrap"><input class="search" type="search" placeholder="Search reminders…" autocomplete="off"></div>
        <div class="chips"></div>
        <div class="list"></div>
      </ha-card>`;
    root.querySelector(".search").addEventListener("input", (ev) => {
      this._query = ev.target.value;
      this._renderList();
    });
    root.addEventListener("click", (ev) => this._onClick(ev));
    root.addEventListener("input", (ev) => this._onInput(ev));
    root.addEventListener("change", (ev) => this._onInput(ev));
    this._renderHead();
  }

  _renderHead() {
    const root = this.shadowRoot;
    const title = this._config?.title;
    root.querySelector(".title").textContent = title || "";
    root.querySelector(".head").classList.toggle("no-title", !title);
    root.querySelector(".search-wrap").hidden = this._config?.show_search === false;
  }

  _visibleCategories() {
    const all = new Set((this._data?.reminders || []).map((r) => r.category || UNCATEGORIZED));
    let cats = [...all];
    const only = this._config?.categories;
    if (only?.length) {
      const want = new Set(only.map((c) => String(c).toLowerCase()));
      cats = cats.filter((c) => want.has(c.toLowerCase()));
    }
    return cats.sort((a, b) => (a === UNCATEGORIZED) - (b === UNCATEGORIZED) || a.localeCompare(b));
  }

  _renderChips() {
    this._renderHead();
    const el = this.shadowRoot.querySelector(".chips");
    const cats = this._visibleCategories();
    if (cats.length < 2) {
      el.innerHTML = "";
      return;
    }
    if (this._chip && !cats.includes(this._chip)) this._chip = null;
    el.innerHTML = [`<button class="chip ${this._chip ? "" : "on"}" data-act="chip" data-cat="">All</button>`]
      .concat(cats.map((c) => `<button class="chip ${this._chip === c ? "on" : ""}" data-act="chip" data-cat="${esc(c)}">${esc(c)}</button>`))
      .join("");
  }

  _renderList() {
    if (!this._built) return;
    const el = this.shadowRoot.querySelector(".list");
    if (this._error) {
      el.innerHTML = `<div class="empty err">Couldn't load reminders: ${esc(this._error)}</div>`;
      return;
    }
    if (!this._data) {
      el.innerHTML = `<div class="empty">Loading…</div>`;
      return;
    }
    const q = this._query.trim().toLowerCase();
    const cats = this._visibleCategories().filter((c) => !this._chip || c === this._chip);
    const groups = cats
      .map((cat) => {
        const items = this._data.reminders
          .filter((r) => (r.category || UNCATEGORIZED) === cat)
          .filter((r) => !q || r.name.toLowerCase().includes(q))
          .sort(
            (a, b) =>
              (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) ||
              (a.days_until_due ?? 99999) - (b.days_until_due ?? 99999) ||
              a.name.localeCompare(b.name)
          );
        return { cat, items };
      })
      .filter((g) => g.items.length);

    if (!groups.length) {
      el.innerHTML = `<div class="empty">${q ? "No reminders match." : "No reminders yet."}</div>`;
      return;
    }
    el.innerHTML = groups.map((g) => this._groupHtml(g, !!q)).join("");
  }

  _isCollapsed(cat) {
    if (cat in this._collapsed) return this._collapsed[cat];
    return !!this._config?.collapsed_by_default;
  }

  _groupHtml({ cat, items }, searching) {
    const due = items.filter((r) => DUE.has(r.status)).length;
    const worst = items.reduce((w, r) => ((STATUS_RANK[r.status] ?? 9) < (STATUS_RANK[w] ?? 9) ? r.status : w), "ok");
    const color = due ? STATUS_COLOR[worst] : "var(--secondary-text-color)";
    // A search expands everything — hidden matches are no matches.
    const collapsed = !searching && this._isCollapsed(cat);
    return `
      <section class="group">
        <button class="ghead" data-act="toggle" data-cat="${esc(cat)}" aria-expanded="${!collapsed}">
          <span class="caret">${collapsed ? "▸" : "▾"}</span>
          <span class="gname">${esc(cat)}</span>
          <span class="gmeta">· ${items.length}${due ? ` · <b style="color:${color}">${due} due</b>` : ""}</span>
        </button>
        ${collapsed ? "" : `<div class="rows">${items.map((r) => this._rowHtml(r)).join("")}</div>`}
      </section>`;
  }

  _rowHtml(r) {
    const color = STATUS_COLOR[r.status] || STATUS_COLOR.ok;
    const open = this._open === r.entry_id;
    const when = r.next_due_date ? fmtDate(r.next_due_date) : "";
    const snoozed = r.snoozed_until ? ` · snoozed` : "";
    const sub = [r.summary, when].filter(Boolean).join(" · ") + snoozed;
    return `
      <div class="row ${open ? "open" : ""}" style="--c:${color}">
        <button class="rhead" data-act="open" data-id="${esc(r.entry_id)}">
          <span class="dot"></span>
          <span class="rtext">
            <span class="rname">${esc(r.name)}${r.mandatory ? ' <span class="must" title="Mandatory">●</span>' : ""}</span>
            <span class="rsub">${esc(sub)}</span>
          </span>
          <span class="pill">${esc(STATUS_LABEL[r.status] || r.status)}</span>
        </button>
        ${open ? this._detailHtml(r) : ""}
      </div>`;
  }

  _detailHtml(r) {
    const id = esc(r.entry_id);
    const admin = !!this._hass?.user?.is_admin;
    const actions = `
      <div class="actions">
        <button class="btn ok" data-act="done" data-id="${id}">Done</button>
        ${r.mandatory ? "" : `
          <select class="btn" data-act="snooze" data-id="${id}">
            <option value="">Snooze…</option>
            ${SNOOZES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
          </select>
          <button class="btn" data-act="skip" data-id="${id}">Skip</button>`}
        ${r.entity_id ? `<button class="btn" data-act="info" data-id="${id}">Details</button>` : ""}
        ${admin ? `<button class="btn primary" data-act="edit" data-id="${id}">${this._editing ? "Close editor" : "Edit"}</button>` : ""}
      </div>`;
    return `<div class="detail">${actions}${this._editing && admin ? this._editorHtml(r) : ""}</div>`;
  }

  _editorHtml(r) {
    const d = this._draft;
    const cats = this._data?.categories || [];
    const st = r.schedule_type;
    const f = (label, inner, wide) => `<label class="f ${wide ? "wide" : ""}"><span>${label}</span>${inner}</label>`;
    const txt = (k, type = "text", extra = "") => `<input type="${type}" data-k="${k}" value="${esc(d[k])}" ${extra}>`;
    const chk = (k, label) => `<label class="ck"><input type="checkbox" data-k="${k}" ${d[k] ? "checked" : ""}> ${label}</label>`;
    let sched = "";
    if (st === "once") sched = f("Date", txt("once_date", "date")) + f("Time", txt("time", "time"));
    else if (st === "yearly") sched = f("Date", txt("anniversary_date", "date")) + f("Time", txt("time", "time"));
    else if (st === "repeating") {
      sched =
        f("Every", txt("every", "number", 'min="1" step="1"')) +
        f("Unit", `<select data-k="unit">${UNITS.map((u) => `<option ${d.unit === u ? "selected" : ""}>${u}</option>`).join("")}</select>`) +
        f("Starting", txt("anchor", "date")) +
        f("Time", txt("time", "time"));
      if (d.unit === "weeks")
        sched += f(
          "Weekdays",
          `<div class="days">${WEEKDAYS.map(
            ([k, l]) => `<label class="day"><input type="checkbox" data-day="${k}" ${d.weekdays.includes(k) ? "checked" : ""}>${l}</label>`
          ).join("")}</div>`,
          true
        );
      if (d.unit === "months") {
        sched += d.monthly_type === "day"
          ? f("Day of month", txt("monthly_day", "number", 'min="1" max="31"'))
          : `<div class="f wide note">Week-pattern months (e.g. 1st Wednesday) are edited in the full wizard.</div>`;
      }
    } else if (st === "condition") {
      const mode = r.condition_mode;
      if (mode === "template") {
        sched =
          f("Due when (template)", `<textarea data-k="due_template" rows="3">${esc(d.due_template)}</textarea>`, true) +
          f("Ask only when (window template, optional)", `<textarea data-k="window_template" rows="2">${esc(d.window_template)}</textarea>`, true);
      } else if (mode === "accumulator") {
        sched = f("Limit", txt("accumulator_limit", "number", 'step="any"')) +
          `<div class="f wide note">Source entity and re-baseline behaviour: full wizard.</div>`;
      } else if (mode === "threshold") {
        sched = f("Due at or below", txt("threshold_below", "number", 'step="any"')) +
          f("Due at or above", txt("threshold_above", "number", 'step="any"')) +
          `<div class="f wide note">Watched entity and hysteresis: full wizard.</div>`;
      }
    } else {
      sched = `<div class="f wide note">Legacy "${esc(st)}" schedule — edit it in the full wizard.</div>`;
    }
    return `
      <div class="editor">
        ${f("Name", txt("name"), true)}
        ${f("Category", `<input type="text" data-k="category" list="ar-cats" value="${esc(d.category)}" placeholder="${UNCATEGORIZED}">
            <datalist id="ar-cats">${cats.map((c) => `<option value="${esc(c)}"></option>`).join("")}</datalist>`)}
        <div class="f flags">${chk("enabled", "Enabled")}${chk("mandatory", "Mandatory")}${chk("until_done", "Carry forward")}${chk("nag", "Nag until done")}</div>
        ${sched}
        ${f("Prompt message(s) — one per line, rotated", `<textarea data-k="messages" rows="3">${esc(d.messages)}</textarea>`, true)}
        <div class="f wide foot">
          <a href="${WIZARD_PATH}" data-act="wizard">Delivery, presence, quiet hours, on-complete → full wizard</a>
          <span class="spacer"></span>
          <button class="btn" data-act="cancel">Cancel</button>
          <button class="btn primary" data-act="save" data-id="${esc(r.entry_id)}" ${this._saving ? "disabled" : ""}>${this._saving ? "Saving…" : "Save"}</button>
        </div>
      </div>`;
  }

  // ── events ────────────────────────────────────────────────────────────────

  _find(id) {
    return (this._data?.reminders || []).find((r) => r.entry_id === id);
  }

  _onInput(ev) {
    const t = ev.target;
    if (!this._draft) return;
    if (t.dataset.k) {
      this._draft[t.dataset.k] = t.type === "checkbox" ? t.checked : t.value;
      // Unit decides which detail fields exist; redraw so they appear.
      if (t.dataset.k === "unit" && ev.type === "change") this._renderList();
    } else if (t.dataset.day) {
      const set = new Set(this._draft.weekdays);
      t.checked ? set.add(t.dataset.day) : set.delete(t.dataset.day);
      this._draft.weekdays = WEEKDAYS.map(([k]) => k).filter((k) => set.has(k));
    } else if (t.dataset.act === "snooze" && ev.type === "change" && t.value) {
      this._call("snooze", { entry_id: t.dataset.id, duration: t.value }, "Snoozed");
      t.value = "";
    }
  }

  _onClick(ev) {
    const el = ev.composedPath().find((n) => n.dataset?.act);
    if (!el) return;
    const act = el.dataset.act;
    const id = el.dataset.id;
    if (act === "snooze") return; // handled on change
    if (el.tagName === "A") {
      ev.preventDefault();
      this._navigate(el.getAttribute("href"));
      return;
    }
    switch (act) {
      case "chip":
        this._chip = el.dataset.cat || null;
        this._renderChips();
        this._renderList();
        break;
      case "toggle": {
        const cat = el.dataset.cat;
        this._collapsed[cat] = !this._isCollapsed(cat);
        saveCollapsed(this._collapsed);
        this._renderList();
        break;
      }
      case "open":
        if (this._open === id) this._closeRow();
        else {
          this._closeRow(false);
          this._open = id;
        }
        this._renderList();
        break;
      case "edit": {
        const r = this._find(id);
        if (!r) return;
        this._editing = !this._editing;
        this._draft = this._editing ? draftFrom(r) : null;
        if (!this._editing) this._flushDirty();
        this._renderList();
        break;
      }
      case "cancel":
        this._editing = false;
        this._draft = null;
        this._flushDirty();
        this._renderList();
        break;
      case "save":
        this._save(id);
        break;
      case "done":
        this._call("mark_done", { entry_id: id, source: "dashboard" }, "Marked done");
        break;
      case "skip": {
        const r = this._find(id);
        if (!window.confirm(`Skip "${r?.name || "this reminder"}" this time?`)) return;
        // The card's own confirm is the confirmation; without confirmed:true
        // the service would send a second yes/no notification.
        this._call("skip_today", { entry_id: id, source: "dashboard", confirmed: true }, "Skipped");
        break;
      }
      case "info": {
        const r = this._find(id);
        if (r?.entity_id) this._fire("hass-more-info", { entityId: r.entity_id });
        break;
      }
      default:
        break;
    }
  }

  _closeRow(render = true) {
    this._open = null;
    this._editing = false;
    this._draft = null;
    this._flushDirty(render);
  }

  _flushDirty(render = true) {
    if (!this._dirty) return;
    this._dirty = false;
    if (render) this._renderChips();
  }

  async _save(id) {
    const r = this._find(id);
    if (!r || !this._draft || this._saving) return;
    const changes = diffChanges(r, this._draft);
    if (!Object.keys(changes).length) {
      this._toast("Nothing changed");
      return;
    }
    this._saving = true;
    this._renderList();
    try {
      const res = await this._hass.callWS({ type: `${DOMAIN}/update`, entry_id: id, changes });
      const n = res?.changed?.length || 0;
      this._toast(n ? `Saved ${res.name || r.name}` : "Nothing changed");
      this._editing = false;
      this._draft = null;
    } catch (err) {
      this._toast(`Couldn't save: ${err?.message || err}`);
    } finally {
      this._saving = false;
      this._dirty = false;
      await this._fetch();
    }
  }

  async _call(service, data, ok) {
    try {
      await this._hass.callService(DOMAIN, service, data);
      this._toast(ok);
    } catch (err) {
      this._toast(`${service} failed: ${err?.message || err}`);
    }
  }

  _toast(message) {
    this._fire("hass-notification", { message });
  }

  _fire(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
  }

  _navigate(path) {
    window.history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 12px 12px 8px; }
  .head { display: flex; align-items: center; gap: 8px; margin: 0 4px 8px; }
  .head.no-title { justify-content: flex-end; }
  .title { flex: 1; font-size: 18px; font-weight: 500; color: var(--primary-text-color); }
  .head.no-title .title { display: none; }
  a { color: var(--primary-color); text-decoration: none; font-size: 13px; }
  .search { width: 100%; box-sizing: border-box; padding: 9px 12px; border-radius: 10px;
    border: 1px solid var(--divider-color); background: var(--secondary-background-color, var(--card-background-color));
    color: var(--primary-text-color); font: inherit; margin-bottom: 8px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .chip { border: 1px solid var(--divider-color); background: none; color: var(--secondary-text-color);
    border-radius: 999px; padding: 4px 11px; font: inherit; font-size: 12.5px; cursor: pointer; }
  .chip.on { background: color-mix(in srgb, var(--primary-color) 16%, transparent); color: var(--primary-color);
    border-color: color-mix(in srgb, var(--primary-color) 45%, transparent); }
  .group { border-top: 1px solid var(--divider-color); }
  .group:first-child { border-top: none; }
  .ghead { display: flex; align-items: baseline; gap: 6px; width: 100%; padding: 10px 4px; background: none;
    border: none; color: var(--primary-text-color); font: inherit; cursor: pointer; text-align: left; }
  .caret { width: 12px; color: var(--secondary-text-color); }
  .gname { font-weight: 600; font-size: 15px; }
  .gmeta { color: var(--secondary-text-color); font-size: 13px; }
  .rows { display: flex; flex-direction: column; gap: 6px; padding: 0 0 10px; }
  .row { border-radius: 12px; background: color-mix(in srgb, var(--c) 7%, var(--card-background-color));
    box-shadow: inset 4px 0 0 0 var(--c); overflow: hidden; }
  .rhead { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 12px 9px 14px; background: none;
    border: none; color: inherit; font: inherit; text-align: left; cursor: pointer; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--c); flex: none; }
  .rtext { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .rname { font-weight: 600; font-size: 14.5px; color: var(--primary-text-color); overflow-wrap: anywhere; }
  .must { color: var(--c); font-size: 10px; vertical-align: middle; }
  .rsub { font-size: 12px; color: var(--secondary-text-color); }
  .pill { flex: none; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap;
    color: var(--c); background: color-mix(in srgb, var(--c) 20%, transparent); }
  .detail { padding: 2px 12px 12px 14px; }
  .actions { display: flex; flex-wrap: wrap; gap: 6px; }
  .btn { border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color);
    border-radius: 8px; padding: 6px 12px; font: inherit; font-size: 13px; cursor: pointer; }
  .btn.ok { border-color: #66bb6a; color: #66bb6a; }
  .btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .btn[disabled] { opacity: .6; cursor: default; }
  .editor { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px 12px; margin-top: 12px;
    padding-top: 12px; border-top: 1px dashed var(--divider-color); }
  .f { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--secondary-text-color); min-width: 0; }
  .f.wide, .flags, .foot { grid-column: 1 / -1; }
  .f input:not([type=checkbox]), .f select, .f textarea { box-sizing: border-box; width: 100%; padding: 7px 9px; border-radius: 8px;
    border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color);
    font: inherit; font-size: 14px; }
  .f textarea { font-family: var(--code-font-family, monospace); font-size: 13px; resize: vertical; }
  .flags { flex-direction: row; flex-wrap: wrap; gap: 6px 16px; }
  .ck, .day { display: inline-flex; align-items: center; gap: 5px; color: var(--primary-text-color); font-size: 13.5px; cursor: pointer; }
  .days { display: flex; flex-wrap: wrap; gap: 4px 12px; }
  .note { font-style: italic; }
  .foot { flex-direction: row; align-items: center; flex-wrap: wrap; gap: 8px; }
  .spacer { flex: 1; }
  .empty { padding: 16px 4px; color: var(--secondary-text-color); text-align: center; }
  .empty.err { color: var(--error-color, #ef5350); }
  @media (max-width: 480px) {
    ha-card { padding: 10px 8px 6px; }
    .editor { grid-template-columns: 1fr 1fr; }
  }
`;

if (!customElements.get("actionable-reminders-card")) {
  customElements.define("actionable-reminders-card", ActionableRemindersCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "actionable-reminders-card",
    name: "Actionable Reminders",
    description: "Every reminder grouped by category, with search, quick actions and an inline editor.",
  });
}
