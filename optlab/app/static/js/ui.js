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
