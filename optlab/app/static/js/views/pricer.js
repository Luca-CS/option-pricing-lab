// Pricer: term sheet from the server's schema, quote as a background job, results with their context.

import { api, runJob, withToken } from "../api.js";
import { barChart } from "../charts.js";
import { describeSheet, display, parse, suffix, vegaNote } from "../format.js";
import { desk, save, state } from "../state.js";
import { clear, dateLabel, dialog, errorNotice, field, h, int, num, originBadge, pm, pretty, progress, segmented, signed,
  switchControl, toast } from "../ui.js";

const PRECISION = [["quick", "Quick · 20k"], ["standard", "Standard · 100k"], ["precise", "Precise · 200k"]];
const BLURB = {
  reverse_convertible: "Fixed coupons, capital at risk below a barrier at maturity",
  barrier_reverse_convertible: "Fixed coupons, daily knock-in barrier",
  phoenix: "Conditional coupons with memory, autocall",
  athena: "Snowball coupon paid at the call",
  airbag: "Upside participation, geared put below the strike",
  capital_protected: "Capital protected, partial upside",
};

let presets = [];
let draft = null;
let lastForm = null;
let running = null;

function fromPreset(name) {
  const p = presets.find((x) => x.name === name) || presets[0];
  const { type, ...values } = p.sheet;
  return { type, values, preset: p.name };
}

export async function render(page, arg) {
  presets = (await api("/api/presets")).presets;
  draft = state.draft && state.draft.type ? state.draft : fromPreset("phoenix");
  let loaded = null;
  if (arg && arg.startsWith("quote/")) {
    try {
      const saved = await api(`/api/quotes/${arg.slice(6)}`);
      const { type, ...values } = saved.sheet;
      draft = { type, values, preset: null };
      loaded = saved.result;
    } catch (e) { toast(e.message); }
  }

  const picker = h("div", { class: "picker", role: "group", "aria-label": "Product" });
  const formCard = h("div", { class: "card stack" });
  const optionsCard = h("div", { class: "card" });
  const results = h("div", { class: "stack", "aria-live": "polite" });

  clear(page,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Pricer"),
        h("p", {}, "Build a term sheet, quote it with the model the policy picks, then book it."))),
    h("div", { class: "pricer" },
      h("div", { class: "stack" },
        h("div", { class: "card" }, h("h3", {}, "Product"), picker),
        formCard),
      h("div", { class: "stack" }, optionsCard, results)));

  const drawPicker = () => {
    clear(picker,
      presets.map((p) => h("button", { class: "pick", type: "button", "aria-pressed": String(draft.preset === p.name),
        onclick: () => { draft = fromPreset(p.name); persist(); drawPicker(); refresh(); } },
        h("b", {}, p.label), h("span", {}, BLURB[p.name] || pretty(p.type)))),
      ["autocall", "bullet"].map((mask) => h("button", { class: "pick", type: "button",
        "aria-pressed": String(!draft.preset && draft.type === mask),
        onclick: () => { draft = { type: mask, values: {}, preset: null }; persist(); drawPicker(); refresh(); } },
        h("b", {}, mask === "autocall" ? "Blank autocall" : "Blank bullet"),
        h("span", {}, "Start from the defaults"))));
  };

  const persist = () => save({ draft });

  const drawForm = (resp) => {
    lastForm = resp;
    const focused = document.activeElement && document.activeElement.id;
    const sections = resp.form.sections.map((sec) => {
      const fields = sec.fields.map((f) => control(f, resp));
      return h("fieldset", { class: "section" },
        h("div", { class: "section-head" }, h("h3", { style: { margin: 0 } }, sec.label)),
        h("div", { class: "fields" }, fields));
    });
    const top = resp.error && !resp.error.field ? errorNotice(resp.error.message) : null;
    const general = (resp.sheet && resp.sheet.general) || draft.values.general || {};
    const worstOf = general.basket === "worst_of" ? h("div", { class: "notice info" },
      "Each name takes its own market from the lake at the valuation date, with a measured correlation. A name or a pair typed in ",
      h("a", { href: "#/market" }, "Market › Other underlyings"),
      " stays what was typed. The policy prices worst-ofs with local vol per name.") : null;
    clear(formCard,
      h("div", { class: "card-head" }, h("h2", {}, "Term sheet"),
        h("span", { class: "muted" }, describeSheet(resp.sheet || { ...draft.values }))),
      top, sections, worstOf);
    if (focused) { const el = document.getElementById(focused); if (el) el.focus(); }
    const qb = document.getElementById("quote-btn");
    if (qb && !running) {
      qb.disabled = !!resp.error;
      qb.title = resp.error ? "Correct the term sheet first" : "";
    }
  };

  const control = (f, resp) => {
    const id = `f-${f.path.replace(".", "-")}`;
    const value = (draft.values[f.path.split(".")[0]] || {})[f.name] ?? f.value;
    const err = resp.error && resp.error.field === f.path ? friendly(f, resp) : null;
    const set = (v) => {
      const [s, n] = f.path.split(".");
      draft.values[s] = { ...(draft.values[s] || {}), [n]: v };
      draft.preset = null;
      persist();
      drawPicker();
      refresh();
    };
    let ctl;
    if (f.read_only) {
      ctl = h("div", { class: "input-wrap" }, h("input", { type: "text", id, value: f.value != null ? `× ${num(f.value, 3)}` : "",
        readonly: true, "aria-readonly": "true" }));
    } else if (f.kind === "choice") {
      const short = f.choices.length <= 3 && f.choices.every((c) => pretty(c).length <= 12);
      ctl = short
        ? segmented(f.choices.map((c) => [c, pretty(c)]), value, set, f.label)
        : h("select", { id, onchange: (e) => set(e.target.value) },
          f.choices.map((c) => h("option", { value: c, selected: c === value || undefined }, pretty(c))));
    } else if (f.kind === "bool") {
      ctl = switchControl(value ? "Yes" : "No", value, set, id);
    } else if (f.kind === "date") {
      ctl = h("input", { type: "date", id, value: value || "", onchange: (e) => set(e.target.value || null) });
    } else {
      const sfx = suffix(f);
      ctl = h("div", { class: "input-wrap" },
        h("input", { type: "text", inputmode: f.kind === "names" ? "text" : "decimal", id, value: display(f, value),
          class: sfx ? "has-suffix" : "", "aria-invalid": err ? "true" : undefined,
          onchange: (e) => set(parse(f, e.target.value)) }),
        sfx ? h("span", { class: "suffix" }, sfx) : null);
    }
    const hint = f.path === "general.strike_date" && !value && desk.context ? `Empty: the valuation date ${desk.context.valuation}` : null;
    return field(f.label, ctl, { error: err, hint, wide: f.kind === "names" });
  };

  // Bound errors in the units on screen (the library speaks in fractions: 0.6 for 60 %).
  const friendly = (f, resp) => {
    const b = resp.bounds && resp.bounds[f.path];
    if (!b || !/is (below the minimum|above the maximum)/.test(resp.error.message)) return resp.error.message;
    const show = (x) => `${display(f, x)}${suffix(f) ? ` ${suffix(f)}` : ""}`;
    if (b.min != null && b.max != null) return `Must be between ${show(b.min)} and ${show(b.max)}.`;
    return b.min != null ? `Must be at least ${show(b.min)}.` : `Must be at most ${show(b.max)}.`;
  };

  let timer = null;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const resp = await api("/api/termsheet/form", { method: "POST", body: { type: draft.type, values: draft.values } });
        if (resp.sheet) {
          const { type, ...values } = resp.sheet;
          draft.values = values;
          persist();
        }
        drawForm(resp);
      } catch (e) { clear(formCard, errorNotice(e)); }
    }, 120);
  };

  const drawOptions = () => {
    const opt = (key, label, id) => switchControl(label, state[key], (v) => { save({ [key]: v }); }, id);
    clear(optionsCard,
      h("div", { class: "card-head" }, h("h2", {}, "Quote"),
        h("span", { class: "muted" }, "Ctrl + Enter")),
      h("div", { class: "btn-row", style: { justifyContent: "space-between" } },
        segmented(PRECISION, state.precision, (v) => { save({ precision: v }); drawOptions(); }, "Precision"),
        h("button", { class: "btn primary lg", type: "button", id: "quote-btn", onclick: doQuote,
          disabled: lastForm && lastForm.error ? true : undefined }, "Quote")),
      h("div", { class: "btn-row", style: { marginTop: "12px", gap: "18px" } },
        opt("control", "Control model", "o-control"), opt("greeks", "Delta and vega", "o-greeks"),
        opt("mixBand", "SLV mix band", "o-mix")),
      h("details", { class: "more" }, h("summary", {}, "Advanced"),
        h("div", { class: "fields" },
          field("Model", h("select", { onchange: (e) => save({ model: e.target.value }) },
            [["", "Policy (recommended)"], ["surface", "Replication on the smile"], ["slv", "SLV"], ["lv", "Local vol"],
              ["heston", "Heston"], ["bs", "Black-Scholes"]].map(([v, t]) =>
              h("option", { value: v, selected: v === state.model || undefined }, t)))),
          field("Delta convention", h("select", { onchange: (e) => save({ sticky: e.target.value }) },
            [["moneyness", "Sticky moneyness"], ["strike", "Sticky strike"], ["model", "The model's own"]].map(([v, t]) =>
              h("option", { value: v, selected: v === state.sticky || undefined }, t)))),
          h("div", { class: "wide btn-row", style: { gap: "18px" } },
            opt("fast", "Fast models (grid interpolation)", "o-fast"),
            opt("controlForwards", "Forward control variates", "o-cf"),
            opt("slvVega", "SLV vega (recalibrates the leverage: slower)", "o-slv-vega")))));
  };

  async function doQuote() {
    if (running) return;
    const btn = document.getElementById("quote-btn");
    const ctrl = new AbortController();
    running = ctrl;
    btn.disabled = true;
    const started = performance.now();
    const status = progress("Submitting…");
    clear(results, h("div", { class: "card" }, status,
      h("div", { class: "btn-row", style: { marginTop: "10px" } },
        h("button", { class: "btn sm", type: "button", onclick: () => ctrl.abort() }, "Cancel"))));
    try {
      const { job } = await api("/api/quote", { method: "POST", body: { sheet: { type: draft.type, values: draft.values },
        options: { precision: state.precision, control: state.control, greeks: state.greeks, mix_band: state.mixBand,
          fast: state.fast, control_forwards: state.controlForwards, sticky: state.sticky, model: state.model || null,
          slv_vega: state.slvVega } } });
      const q = await runJob(job, (j) => {
        status.querySelector("p").textContent = `${pretty(j.state)} · ${num((performance.now() - started) / 1000, 1)} s`
          + (state.mixBand ? " · the mix band recalibrates four leverages" : "");
      }, ctrl.signal);
      save({ lastQuote: { id: q.quote_id, headline: q.headline, product: describeSheet(q.sheet) } });
      drawResult(q);
    } catch (e) {
      clear(results, e.status === 499 ? h("div", { class: "notice info" }, "Quote cancelled.") : errorNotice(e));
    } finally {
      running = null;
      btn.disabled = !!(lastForm && lastForm.error);
    }
  }

  function drawResult(q) {
    const head = q.headline;
    const r = q.result;
    const ends = q.schedule.map((s) => ({ label: s.observation ? s.observation.slice(2, 7) : `t=${num(s.t, 2)}`,
      value: 100 * s.p_end, title: `${dateLabel(s.observation)}: ends with probability ${num(100 * s.p_end, 1)} %` }));
    const called = q.schedule.slice(0, -1).reduce((a, s) => a + s.p_end, 0);
    const gap = q.control_gap_se;
    const gapClass = gap == null ? "" : Math.abs(gap) < 2 ? "pos" : Math.abs(gap) < 4 ? "warn" : "neg";
    const g = q.greeks;
    clear(results,
      h("div", { class: "card" },
        h("div", { class: "muted" }, head.label),
        h("div", { class: "headline" }, num(head.value, head.kind === "coupon" ? 3 : 3),
          h("span", { class: "unit" }, head.unit), head.se ? h("span", { class: "se" }, `± ${num(head.se, 3)}`) : null),
        h("div", { class: "btn-row", style: { marginTop: "8px" } },
          h("span", { class: "badge accent" }, q.policy.label.replace("->", "→")),
          q.policy.forced ? h("span", { class: "badge warn" }, "model forced") : null,
          h("span", { class: "badge" }, `margin ${num(q.margin, 2)} %`),
          h("span", { class: "badge" }, `${num(q.seconds, 1)} s`),
          h("span", { class: "badge", title: "Market snapshot" }, q.snapshot_id)),
        h("p", { class: "muted", style: { marginTop: "10px" } }, `${q.policy.description}. ${q.policy.reason}.`),
        q.control ? h("p", {}, `Control ${String(q.policy.control).toUpperCase()}: ${pm(q.control.price, q.control.stderr)} % `,
          h("span", { class: `badge ${gapClass}` }, `${signed(gap, 1)} SE`),
          h("span", { class: "muted" }, " gap to the main model")) : null,
        q.warnings.concat(r.warnings.filter((w) => !w.startsWith("digital smoothing"))).map((w) =>
          h("div", { class: "notice", style: { marginTop: "8px" } }, w)),
        h("div", { class: "btn-row", style: { marginTop: "14px" } },
          h("button", { class: "btn primary", type: "button", onclick: () => bookIt(q) }, "Book this trade"),
          h("a", { class: "btn", href: withToken(`/report/quote/${q.quote_id}`), target: "_blank", rel: "noopener" },
            "Open the report"),
          h("button", { class: "btn ghost", type: "button", onclick: () => copySheet(q.sheet) }, "Copy term sheet"))),
      h("div", { class: "grid cols-4" },
        kpi("Expected life", `${num(r.expected_life, 2)} y`),
        kpi("Ends before maturity", `${num(100 * called, 1)} %`),
        kpi("Method", pretty(r.method), r.n_paths ? `${int(r.n_paths)} paths` : `grid error ${r.numerical_error.toExponential(1)} %`),
        kpi("Value", `${pm(r.price, r.stderr)} %`, q.product.format)),
      ends.length > 1 ? h("div", { class: "card" }, h("h2", {}, "Probability that the product ends at each date"),
        barChart(ends, { unit: " %", yFormat: (v) => v.toFixed(v < 10 ? 1 : 0), label: "Probability of ending by date" })) : null,
      g ? h("div", { class: "card" }, h("h2", {}, "Greeks"),
        h("div", { class: "grid cols-3" },
          kpi(`Delta (sticky ${g.sticky})`, `${signed(g.delta, 4)} %`, `± ${num(g.delta_se, 4)} · per +1 % spot`),
          kpi("Vega", g.vega != null ? `${signed(g.vega, 4)} %` : "–", g.vega != null ? `± ${num(g.vega_se, 4)} · per vol point` : vegaNote(g.note)),
          kpi("Smoothing", g.smoothing ? `${num(100 * g.smoothing, 1)} %` : "none",
            g.smoothing ? `price bias ${signed(g.smoothing_bias, 4)} %` : "exact digitals")),
        g.per_name ? h("div", { class: "table-wrap", style: { marginTop: "10px" } }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Name"), h("th", { class: "num" }, "Delta %"), h("th", { class: "num" }, "Vega %")),
          Object.entries(g.per_name).map(([n, v]) => h("tr", {}, h("td", {}, n), h("td", { class: "num" }, pm(v.delta, v.delta_se, 4)),
            h("td", { class: "num" }, pm(v.vega, v.vega_se, 4)))))) : null) : null,
      q.basket ? basketCard(q.basket) : null,
      q.mix_band ? h("div", { class: "card" }, h("h2", {}, "SLV mixing fraction"),
        h("p", { class: "muted" }, "The share of stochastic vol of vol is not calibrated; the vanillas stay on the surface."),
        h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Mix"), h("th", { class: "num" }, head.kind === "coupon" ? "Coupon % p.a." : "Value %"),
            h("th", { class: "num" }, "ATM vol error")),
          Object.entries(q.mix_band).map(([m, b]) => h("tr", {}, h("td", {}, num(+m, 2)),
            h("td", { class: "num" }, pm(head.kind === "coupon" ? 100 * b.value : b.value, head.kind === "coupon" ? 100 * b.se : b.se)),
            h("td", { class: `num ${Math.abs(b.atm_error_bp) > 5 ? "neg" : ""}` }, `${signed(b.atm_error_bp, 1)} bp`))))) : null,
      h("div", { class: "card" }, h("h2", {}, "Expected flows by date"),
        h("p", { class: "muted" }, "% of notional, undiscounted, over all paths."),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Observation", "Payment", "Ends", "Coupon", "Upside", "Redemption"].map((c, i) =>
            h("th", { class: i > 1 ? "num" : "" }, c))),
          q.schedule.map((s) => h("tr", {}, h("td", {}, dateLabel(s.observation)), h("td", {}, dateLabel(s.payment)),
            h("td", { class: "num" }, `${num(100 * s.p_end, 1)} %`), h("td", { class: "num" }, num(s.flows.coupon, 3)),
            h("td", { class: "num" }, num(s.flows.upside, 3)), h("td", { class: "num" }, num(s.flows.redemption, 3))))))),
      h("details", { class: "card more" }, h("summary", {}, "Generated script"),
        h("pre", { class: "code" }, q.product.script)));
  }

  async function bookIt(q) {
    const ctxDate = desk.context ? desk.context.valuation : "";
    const strike = (draft.values.general || {}).strike_date || ctxDate;
    const res = await dialog({
      title: "Book this trade", ok: "Book",
      body: [h("p", { class: "muted" }, `${describeSheet(q.sheet)}${q.coupon_rate != null ? ` · coupon ${num(100 * q.coupon_rate, 3)} % p.a.` : ""}`),
        field("Quantity (units of the notional)", h("input", { type: "number", name: "quantity", value: "1", step: "any", required: true })),
        h("div", { class: "fields" },
          field("Trade date", h("input", { type: "date", name: "trade_date", value: ctxDate })),
          field("Strike date", h("input", { type: "date", name: "strike_date", value: strike }))),
        field("Counterparty (optional)", h("input", { type: "text", name: "counterparty" }))],
      read: (form) => Object.fromEntries(new FormData(form)),
    });
    if (!res) return;
    try {
      const t = await api("/api/trades", { method: "POST", body: { sheet: q.sheet ? { type: q.sheet.type, values: strip(q.sheet) } : null,
        quantity: Number(res.quantity), trade_date: res.trade_date, strike_date: res.strike_date || null,
        counterparty: res.counterparty, coupon_rate: q.coupon_rate, tags: q.quote_id ? [`quote:${q.quote_id}`] : [] } });
      toast(`Booked ${t.trade_id}`, { action: "Open the trade", onAction: () => { location.hash = `#/trade/${t.trade_id}`; } });
    } catch (e) { toast(e.message); }
  }

  const strip = (sheet) => { const { type, ...values } = sheet; return values; };

  const onKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); doQuote(); } };
  document.addEventListener("keydown", onKey);

  drawPicker();
  drawOptions();
  refresh();
  if (loaded) drawResult(loaded);
  else {
    const last = state.lastQuote;
    clear(results, h("div", { class: "card empty" }, h("p", {}, "No quote yet."),
      h("p", { class: "faint" }, "Pick a product, adjust the term sheet, then Quote (Ctrl + Enter)."),
      last && last.headline ? h("p", {}, "Last quote: ", h("a", { href: `#/pricer/quote/${last.id}` },
        `${last.product} · ${pm(last.headline.value, last.headline.se)} ${last.headline.unit}`)) : null));
  }
  return () => document.removeEventListener("keydown", onKey);
}

// What a price used for each name: its market, where it comes from, and the correlations.
const SOURCE = { active: "market in use", lake: "lake", input: "typed", default: "default, flat" };

export function basketCard(b) {
  const names = Object.entries(b.names || {});
  const flat = names.filter(([, p]) => p.layer === "flat").map(([n]) => n);
  const pairs = Object.entries(b.corr || {});
  return h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Markets used"), h("a", { href: "#/market" }, "Change")),
    flat.length ? h("div", { class: "notice", style: { marginBottom: "8px" } },
      `${flat.join(", ")}: no smile, a flat Black-Scholes volatility.`) : null,
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("tr", {}, h("th", {}, "Name"), h("th", {}, "Market"), h("th", {}, "Surface"), h("th", { class: "num" }, "ATM vol 1 y"), h("th", { class: "num" }, "Dividend"),
        h("th", { class: "num" }, "Rate")),
      names.map(([n, p]) => h("tr", {}, h("td", {}, n), h("td", {}, SOURCE[p.source] || p.source || ""),
        h("td", {}, originBadge(p.origin)), h("td", { class: "num" }, `${num(100 * p.vol, 1)} %`),
        h("td", { class: "num" }, `${num(100 * p.q, 2)} %`), h("td", { class: "num" }, `${num(100 * p.r, 2)} %`))))),
    pairs.length ? h("p", { class: "muted", style: { marginTop: "8px" } }, "Correlations: ",
      pairs.map(([k, v]) => `${k.replace("|", "–")} ${num(v, 2)}${b.corr_source && b.corr_source[k] !== "lake" ? ` (${b.corr_source[k]})` : ""}`).join(" · "),
      b.correlation ? ` · measured over ${b.correlation.window} days, premium λ ${num(b.correlation.lambda, 2)}` : "") : null);
}

function kpi(label, value, sub) {
  return h("div", { class: "card tight kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    sub ? h("div", { class: "sub" }, sub) : null);
}

// The clipboard API needs a secure context: over plain http on the LAN, show the JSON selected instead.
function copySheet(sheet) {
  const text = JSON.stringify(sheet, null, 1);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => toast("Term sheet copied as JSON"), () => showText(text));
  } else {
    showText(text);
  }
}

function showText(text) {
  const area = h("textarea", { rows: "14", readonly: true, style: { width: "100%", fontFamily: "var(--mono)" } });
  area.value = text;
  dialog({ title: "Term sheet (JSON)", ok: "Close", cancel: "Back", body: [h("p", { class: "muted" }, "Selected: Ctrl + C copies it."), area] });
  area.select();
}
