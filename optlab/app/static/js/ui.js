// DOM helpers, number formats, toasts and dialogs.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// --- formats: tabular figures, explicit units ---
export const isNum = (x) => typeof x === "number" && Number.isFinite(x);
export function num(x, d = 2) {
  return isNum(x) ? x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "–";
}
export function signed(x, d = 2) {
  return isNum(x) ? (x > 0 ? "+" : "") + num(x, d) : "–";
}
export function pct(frac, d = 2) {
  return isNum(frac) ? `${num(100 * frac, d)} %` : "–";
}
export function pm(x, se, d = 3) {
  return isNum(se) && se > 0 ? `${num(x, d)} ± ${num(se, d)}` : num(x, d);
}
export function int(x) {
  return isNum(x) ? Math.round(x).toLocaleString("en-US") : "–";
}
const WORDS = { pdi: "PDI", nyse: "NYSE", slv: "SLV", lv: "Local vol", bs: "Black-Scholes", heston: "Heston",
  spx: "SPX", yahoo: "Yahoo",
  monte_carlo: "Monte Carlo", worst_of: "Worst of" };
export function pretty(s) {
  return WORDS[s] || String(s ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
export function dateLabel(iso) {
  if (!iso) return "–";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

// --- toasts ---
export function toast(message, { action, onAction, timeout = 5000 } = {}) {
  const box = document.getElementById("toasts");
  const btn = action ? h("button", { type: "button", onclick: () => { onAction && onAction(); el.remove(); } }, action) : null;
  const el = h("div", { class: "toast", role: "status" }, h("span", {}, message), btn);
  box.append(el);
  setTimeout(() => el.remove(), timeout);
  return el;
}

// --- dialog: resolves with the value of the form, or null when cancelled ---
export function dialog({ title, body, ok = "OK", cancel = "Cancel", danger = false, read }) {
  const dlg = document.getElementById("dialog");
  return new Promise((resolve) => {
    const form = h("form", { method: "dialog" },
      h("div", { class: "dlg-head" }, h("h2", {}, title)),
      h("div", { class: "dlg-body" }, body),
      h("div", { class: "dlg-foot" },
        h("button", { class: "btn", value: "cancel", type: "submit", formnovalidate: true }, cancel),
        h("button", { class: `btn ${danger ? "danger" : "primary"}`, value: "ok", type: "submit" }, ok)));
    clear(dlg, form);
    // Resolve on submit (synchronous, the form still holds its values); close covers Escape. returnValue is reset
    // because Escape keeps the previous one: an earlier OK must not confirm this dialog.
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      resolve(ok ? (read ? read(form) : true) : null);
    };
    form.addEventListener("submit", (e) => finish(e.submitter && e.submitter.value === "ok"));
    dlg.onclose = () => finish(dlg.returnValue === "ok");
    dlg.returnValue = "";
    dlg.showModal();
    const first = form.querySelector("input, select, textarea");
    if (first) first.focus();
  });
}

export function field(label, control, { hint, error, wide } = {}) {
  return h("div", { class: `field${error ? " invalid" : ""}${wide ? " wide" : ""}` },
    h("label", {}, label), control, hint ? h("div", { class: "hint" }, hint) : null,
    error ? h("div", { class: "err", role: "alert" }, error) : null);
}

export function segmented(options, value, onChange, label) {
  const wrap = h("div", { class: "seg", role: "group", "aria-label": label });
  for (const [v, text] of options) {
    wrap.append(h("button", { type: "button", "aria-pressed": String(v === value), onclick: () => onChange(v) }, text));
  }
  return wrap;
}

export function switchControl(label, checked, onChange, id) {
  return h("label", { class: "switch", for: id },
    h("input", { type: "checkbox", id, checked: checked || undefined, onchange: (e) => onChange(e.target.checked) }),
    h("span", {}, label));
}

// How far a value is from a market observation: shown with it on every screen, in words (not by colour alone).
const ORIGIN_HELP = { observed: "read from a real market source", derived: "computed from observed data",
  reconstructed: "rebuilt from volatility indices and an assumed smile shape, no option quote read",
  synthetic: "an assumption or a simulation, not market data" };
export function originBadge(origin) {
  const name = origin || "unknown";
  return h("span", { class: `badge origin ${name}`, title: ORIGIN_HELP[name] || "origin unknown" }, name);
}

export function badgeFor(state) {
  const map = { live: "pos", called: "accent", matured: "", cancelled: "neg" };
  return h("span", { class: `badge ${map[state] ?? ""}` }, pretty(state));
}

export function progress(text) {
  return h("div", { class: "stack", "aria-live": "polite" },
    h("div", { class: "progress" }, h("i")), h("p", { class: "muted", style: { marginTop: "8px" } }, text));
}

export function errorNotice(err) {
  return h("div", { class: "notice err", role: "alert" }, String(err && err.message ? err.message : err));
}

// --- sortable tables: a click on a header sorts the rows below it (dates, numbers, then text); again reverses ---
const DATE = /^(\d{1,2} [A-Za-z]{3,4}\.? \d{4}|\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?)$/;
const NUMBER = /^[+−-]?(\d[\d,]*)?\.?\d+(e[+-]?\d+)?/i;

function sortKey(text) {
  text = String(text ?? "").trim();
  const empty = !text || text === "–";
  const n = text.match(NUMBER);
  return { text, empty, date: DATE.test(text) ? Date.parse(text) : null,
    num: n ? parseFloat(n[0].replace(/,/g, "").replace("−", "-")) : null };
}

// The order of a column's cells (as shown): dates if every filled cell is a date, else numbers if every one starts
// with a number (98.000 ± 0.034, 65.6 %, +0.183), else text with numeric parts compared as numbers. Empty cells go
// last in both directions; ties keep their order. dir = 1 ascending, -1 descending.
export function sortOrder(texts, dir = 1) {
  const keys = texts.map(sortKey);
  const full = keys.filter((k) => !k.empty);
  const kind = full.every((k) => k.date !== null) ? "date" : full.every((k) => k.num !== null) ? "num" : "text";
  const cmp = (a, b) => {
    if (a.empty || b.empty) return a.empty - b.empty;
    if (kind === "text") return dir * a.text.localeCompare(b.text, undefined, { numeric: true });
    return dir * (a[kind] - b[kind]);
  };
  return keys.map((_, i) => i).sort((i, j) => cmp(keys[i], keys[j]) || i - j);
}

export function sortable(table) {
  const rows = [...table.rows];
  if (table.dataset.sortable || table.classList.contains("nosort") || rows.length < 3) return;
  const head = rows[0];
  if (!head.cells.length || [...head.cells].some((c) => c.tagName !== "TH")) return;
  table.dataset.sortable = "1";
  [...head.cells].forEach((th, col) => {
    if (!th.textContent.trim()) return;
    const btn = h("button", { type: "button", class: "sort", title: "Sort" }, [...th.childNodes]);
    th.replaceChildren(btn);
    btn.addEventListener("click", () => {
      const dir = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
      head.querySelectorAll("th").forEach((x) => x.removeAttribute("aria-sort"));
      th.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
      const body = [...table.rows].slice(1);
      const order = sortOrder(body.map((r) => (r.cells[col] ? r.cells[col].textContent : "")), dir);
      const parent = body[0].parentNode;
      order.forEach((i) => parent.appendChild(body[i]));
    });
  });
}
