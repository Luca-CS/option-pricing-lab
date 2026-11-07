// P&L explained: the cascade of full revaluations from one close to the next, against the Greeks of the day before.

import { api, runJob } from "../api.js";
import { legend, lineChart } from "../charts.js";
import { clear, errorNotice, field, h, int, isNum, num, pretty, progress, signed, toast } from "../ui.js";

const NS = "http://www.w3.org/2000/svg";
const STEP_HELP = {
  time: "one day passes, market frozen; the day's cash is counted", spot: "the spot of the day", vol: "the at-the-money vols per tenor",
  skew: "the rest of the surface (skew and shape)", rates: "the discount curve, the forward following the rate",
  dividends: "dividends, repo and quanto drift (the forward of the day)", corr: "the correlations of the worst-ofs", fx: "currency conversion",
};

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const c of children) if (c) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

// Waterfall: each step a bar from the running total, then the total.
function waterfall(order, steps, total, { height = 240, label = "P&L cascade" } = {}) {
  const W = 720, H = height, L = 60, R = 10, T = 14, B = 40;
  let acc = 0;
  const bars = order.map((k) => { const a = acc; acc += steps[k]; return { k, from: a, to: acc }; });
  bars.push({ k: "total", from: 0, to: total, total: true });
  const lo = Math.min(0, ...bars.map((b) => Math.min(b.from, b.to))), hi = Math.max(0, ...bars.map((b) => Math.max(b.from, b.to)));
  const pad = (hi - lo) * 0.08 || 1;
  const Y = (v) => T + (H - T - B) * (1 - (v - (lo - pad)) / (hi - lo + 2 * pad));
  const bw = (W - L - R) / bars.length;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": label });
  svg.append(s("line", { x1: L, x2: W - R, y1: Y(0), y2: Y(0), class: "axis" }));
  for (const t of [lo, 0, hi]) svg.append(s("text", { x: L - 6, y: Y(t) + 4, "text-anchor": "end" }, num(t, 0)));
  bars.forEach((b, i) => {
    const x = L + i * bw + bw * 0.15;
    const up = b.to >= b.from;
    const fill = b.total ? "var(--chart-1)" : up ? "var(--pos)" : "var(--neg)";
    svg.append(s("rect", { x, y: Y(Math.max(b.from, b.to)), width: bw * 0.7, height: Math.max(1, Math.abs(Y(b.from) - Y(b.to))), rx: 2, fill },
      s("title", {}, `${b.k}: ${signed(b.to - b.from, 2)}`)));
    svg.append(s("text", { x: x + bw * 0.35, y: H - B + 16, "text-anchor": "middle" }, b.k));
    svg.append(s("text", { x: x + bw * 0.35, y: Y(Math.max(b.from, b.to)) - 4, "text-anchor": "middle" }, num(b.to - b.from, 0)));
  });
  return svg;
}

function card(title, unit, ...body) {
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, title), unit ? h("span", { class: "muted" }, unit) : null), ...body);
}

function table(head, rows, numFrom = 1) {
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("tr", {}, head.map((c, i) => h("th", { class: i >= numFrom ? "num" : "" }, c))),
    rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: i >= numFrom ? "num" : "" }, c))))));
}

const sum = (o) => Object.values(o || {}).reduce((a, x) => a + (Array.isArray(x) ? x[0] : x), 0);

function draw(out, p, pick, chosen) {
  if (!p.daily || !p.daily.length) {
    clear(out, h("div", { class: "empty" }, p.note || "No P&L for this period: run the EOD backfill above."));
    return;
  }
  const order = p.order;
  const day = chosen ? p.daily.find((d) => d.to === chosen) : null;
  const src = day ? { steps: Object.fromEntries(order.map((k) => [k, day.steps[k][0]])), predicted: day.predicted, unexplained: day.unexplained, total: day.total }
    : { steps: p.steps, predicted: p.predicted, unexplained: p.unexplained, total: p.total };
  const explained = 1 - Math.abs(sum(src.unexplained)) / Math.max(1e-9, Math.abs(src.total));
  const curve = [{ name: "Cumulated P&L", markers: p.days.length < 40, points: p.days.map((d, i) => [Date.parse(d), p.curve[i]]) }];
  let accPred = 0;
  curve.push({ name: "Cumulated prediction by the Greeks", points: p.daily.map((d) => { accPred += sum(d.predicted); return [Date.parse(d.to), accPred]; }) });
  clear(out,
    h("div", { class: "grid cols-3" },
      kpi(day ? `P&L ${day.from} → ${day.to}` : `P&L ${p.from} → ${p.to}`, signed(src.total, 0), "currency, desk side: value + cash"),
      kpi("Predicted by the Greeks", signed(sum(src.predicted), 0), "Greeks of the day before applied to the observed moves"),
      kpi("Unexplained", signed(sum(src.unexplained), 0), isNum(explained) ? `the Greeks explain ${num(100 * explained, 0)} % of it` : "")),
    card(day ? `Cascade of ${day.to}` : "Cascade over the period", "full revaluations, in this order, on the seeds of the day before",
      waterfall(order, src.steps, src.total, { label: "P&L by step" }),
      table(["Step", "What moves", "Cascade", "Greeks", "Unexplained"],
        order.map((k) => [pretty(k), STEP_HELP[k], signed(src.steps[k], 0), signed(src.predicted[k], 0), signed(src.unexplained[k], 0)]), 2)),
    card("Cumulated P&L", "currency", lineChart(curve, { xFormat: (v) => new Date(v).toISOString().slice(5, 10), yFormat: (v) => v.toFixed(0), xLabel: "date",
      label: "Cumulated P&L and its prediction" }), legend(curve)),
    card("Days", "click a day for its cascade",
      table(["Day", "Total", ...order.map(pretty), "Unexplained", "Reserve released", "New trades"],
        p.daily.map((d) => [h("a", { href: "#", onclick: (e) => { e.preventDefault(); pick(d.to); } }, h("b", {}, d.to)), signed(d.total, 0),
          ...order.map((k) => signed(d.steps[k][0], 0)), signed(sum(d.unexplained), 0), signed(d.release || 0, 0),
          d.new_trades.length ? d.new_trades.map((t) => `${t.trade_id} ${t.pnl != null ? signed(t.pnl, 0) : ""}`).join(", ") : ""]), 1)),
    card("By trade", "cumulated over the period", table(["Trade", "Group", "Total", ...order.map(pretty)],
      Object.entries(p.trades).map(([t, r]) => [h("a", { href: `#/trade/${t}` }, h("b", {}, t)), pretty(r.group), signed(r.total, 0),
        ...order.map((k) => signed(r.steps[k], 0))]), 2)),
    card("Events", "coupons, calls and knock-ins in the period", (() => {
      const rows = p.daily.flatMap((d) => Object.entries(d.events || {}).flatMap(([t, evs]) => evs.map((e) => [e.date, t, pretty(e.kind),
        e.amount == null ? "–" : signed(e.amount, 2)])));
      return rows.length ? table(["Date", "Trade", "Event", "Amount"], rows, 3) : h("p", { class: "muted" }, "No event in the period.");
    })()));
}

function kpi(label, value, sub) {
  return h("div", { class: "card tight kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), h("div", { class: "sub" }, sub));
}

export async function render(page) {
  const out = h("div", { class: "stack", "aria-live": "polite" });
  const from = h("input", { type: "date", id: "pnl-from" });
  const to = h("input", { type: "date", id: "pnl-to" });
  const runs = h("p", { class: "muted" });
  let data = null;
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "P&L explained"),
    h("p", {}, "Daily and cumulated, from the stored end-of-day runs: what each part of the market did to the book, and what the Greeks had said.")),
    h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/risk" }, "Risk"))),
  h("div", { class: "stack" },
    card("Period", "dates of the stored EOD runs",
      h("div", { class: "grid cols-3" }, field("From", from), field("To", to),
        h("div", { class: "btn-row", style: { alignItems: "flex-end" } },
          h("button", { class: "btn primary", type: "button", onclick: () => load() }, "Show"),
          h("button", { class: "btn", type: "button", onclick: () => backfill() }, "Run EOD for the period"))), runs),
    out));

  const pick = (d) => draw(out, data, pick, d);

  async function load() {
    clear(out, h("div", { class: "skeleton", style: { height: "240px" } }));
    try {
      const q = new URLSearchParams();
      if (from.value) q.set("from", from.value);
      if (to.value) q.set("to", to.value);
      data = await api(`/api/pnl?${q}`);
      if (!from.value && data.from) from.value = data.from;
      if (!to.value && data.to) to.value = data.to;
      const eod = data.eod || [];
      runs.textContent = eod.length ? `${int(eod.length)} EOD runs stored, ${eod[0].day} to ${eod[eod.length - 1].day} · code ${eod[eod.length - 1].code}` : "No EOD run stored yet.";
      draw(out, data, pick, null);
    } catch (e) { clear(out, errorNotice(e)); }
  }

  async function backfill() {
    if (!from.value) { toast("Give the first date"); return; }
    const status = progress("EOD runs and the cascade of each day…");
    clear(out, h("div", { class: "card" }, status));
    const started = performance.now();
    try {
      const { job } = await api("/api/pnl/eod", { method: "POST", body: { from: from.value, to: to.value || from.value } });
      const res = await runJob(job, (j) => { status.querySelector("p").textContent = `${j.state} · ${num((performance.now() - started) / 1000, 0)} s`; });
      toast(`${res.runs.length} EOD runs (${res.runs.filter((r) => r.reused).length} reused)`);
      await load();
    } catch (e) { clear(out, errorNotice(e)); }
  }

  await load();
}
