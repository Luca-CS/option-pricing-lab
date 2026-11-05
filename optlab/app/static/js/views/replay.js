// Replay: a product through its life on real closes, valued each day on the market of that day.

import { api, runJob } from "../api.js";
import { legend, lineChart } from "../charts.js";
import { clear, dateLabel, errorNotice, field, h, int, num, originBadge, pretty, progress, signed } from "../ui.js";

const dateAxis = (t) => new Date(t).toISOString().slice(0, 7);
const day = (iso) => Date.parse(iso);
const ORIGIN_OF_LAYER = { observed: "observed", reconstructed: "reconstructed", synthetic: "synthetic" };

function draw(out, r) {
  const names = r.underlyings || Object.keys(r.daily[0].perf);
  const layers = [...new Set(r.daily.flatMap((x) => Object.values(x.layer || {})))];
  const value = { name: "Value", points: r.daily.map((x) => [day(x.date), x.value]) };
  let cash = 0;
  const wealth = { name: "Value + cash received", points: r.daily.map((x) => { cash += x.cash; return [day(x.date), x.value + cash]; }) };
  const perf = names.map((n) => ({ name: `${n}, % of initial`, points: r.daily.map((x) => [day(x.date), 100 * x.perf[n]]) }));
  const x = r.explain || {};
  const rec = r.reconciliation || {};
  const prov = r.provenance && r.provenance[names[0]];
  clear(out,
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `${r.trade_id}`),
      h("div", { class: "btn-row" }, layers.map((l) => originBadge(ORIGIN_OF_LAYER[l] || l)))),
      h("p", {}, `Coupon ${num(100 * (r.inception?.coupon ?? 0), 2)} % p.a. solved on the market of ${r.inception?.strike ? dateLabel(r.inception.strike) : "–"}. `,
        `${int(r.n_valuations)} valuations, ${num(r.seconds?.total ?? 0, 0)} s (${num(r.seconds?.per_valuation ?? 0, 2)} s each)`,
        r.filled_fixings ? `, ${r.filled_fixings} missing closes filled with the day before` : "", "."),
      prov ? h("p", {}, "Fixings ", originBadge("observed"), " · curve ", originBadge(prov.discount.origin), " · carry ", originBadge(prov.carry.origin),
        ` (${pretty(prov.carry.method)}) · surface `, originBadge(prov.surface.origin), ` beyond ${num(prov.surface.extrapolated_from, 2)} y the term structure is an assumption.`) : null,
      lineChart([value, wealth], { xFormat: dateAxis, yFormat: (v) => v.toFixed(0), xLabel: "date", label: "Value and cash of the trade over its life" }),
      legend([value, wealth]),
      lineChart(perf, { xFormat: dateAxis, yFormat: (v) => `${v.toFixed(0)} %`, xLabel: "date", label: "Performance of the underlying since the strike" }),
      legend(perf)),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Events"), originBadge("observed")),
      h("p", {}, "From the product's own script on the real closes."),
      h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("tr", {}, ["Observed", "Event", "Amount", "Paid"].map((t) => h("th", {}, t))),
        r.events.map((e) => h("tr", {}, h("td", {}, dateLabel(e.date)), h("td", {}, pretty(e.kind)),
          h("td", { class: "num" }, e.amount === null ? "–" : num(e.amount, 4)), h("td", {}, e.payment ? dateLabel(e.payment) : "–"))))),
      h("p", {}, `Cash booked ${num(rec.cash_booked ?? 0, 4)}, from the events ${num(rec.cash_from_events ?? 0, 4)}, gap ${num(rec.cash_gap ?? 0, 6)}.`)),
    x.steps ? h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "P&L explained")),
      h("p", {}, `Over ${int(x.steps)} steps, with Greeks from the step before. The mean daily P&L is ${num(x.mean_abs_pnl, 3)} in absolute value, the mean residual ${num(x.mean_abs_residual, 3)}, against a standard error of ${num(x.mean_value_se, 3)} on each value.`),
      h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("tr", {}, ["Delta", "Gamma", "Vega", "Time", "Residual", "Total P&L"].map((t) => h("th", { class: "num" }, t))),
        h("tr", {}, ["delta", "gamma", "vega", "time"].map((k) => h("td", { class: "num" }, signed(x.sum[k], 3))),
          h("td", { class: "num" }, signed(x.sum_residual, 3)), h("td", { class: "num" }, signed(x.sum_pnl, 3)))))) : null);
}

export async function render(page) {
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Replay"))), h("div", { class: "skeleton", style: { height: "200px" } }));
  let info;
  try { info = await api("/api/replay/runs"); } catch (e) { clear(page, errorNotice(e)); return; }
  const out = h("div", {});
  const preset = h("select", { id: "rp-preset" }, info.presets.filter((p) => !["airbag", "capital_protected"].includes(p)).map((p) => h("option", { value: p }, pretty(p))));
  preset.value = "barrier_reverse_convertible";
  const name = h("select", { id: "rp-name" }, ["SPX", "NDX", "RUT", "AAPL", "AMZN", "GOOGL", "GS", "IBM"].map((n) => h("option", { value: n }, n)));
  const strike = h("input", { id: "rp-strike", type: "date", value: "2020-01-15" });
  const every = h("input", { id: "rp-every", type: "number", min: 1, value: 5 });
  const model = h("select", { id: "rp-model" }, h("option", { value: "lv" }, "Local vol"), h("option", { value: "slv" }, "SLV"));
  const list = h("div", {});
  const open = async (id) => {
    clear(out, progress("Loading"));
    try { draw(out, await api(`/api/replay/runs/${id}`)); } catch (e) { clear(out, errorNotice(e)); }
  };
  const drawList = (runs) => clear(list, runs.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("tr", {}, ["Run", "Kept in", "Strike", "Valuations", "Events", "Seconds", ""].map((t) => h("th", {}, t))),
    runs.map((r) => h("tr", {}, h("td", {}, r.trade_id), h("td", {}, r.kind), h("td", {}, r.strike || "–"), h("td", { class: "num" }, int(r.n_valuations || 0)),
      h("td", { class: "num" }, int(r.events)), h("td", { class: "num" }, num(r.seconds || 0, 0)),
      h("td", { class: "num" }, h("button", { class: "btn sm", type: "button", onclick: () => open(r.id) }, "Open")))))) : h("p", {}, "No run yet."));
  drawList(info.runs);
  const run = async () => {
    clear(out, progress("Replaying: one valuation per step, on the market of that day"));
    try {
      const { job } = await api("/api/replay/run", { method: "POST", body: { preset: preset.value, underlying: name.value, strike: strike.value, every: +every.value, model: model.value } });
      const j = await runJob(job, () => {});
      draw(out, j.result);
      drawList((await api("/api/replay/runs")).runs);
    } catch (e) { clear(out, errorNotice(e)); }
  };
  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Replay"),
      h("p", {}, "Deal a preset on a past date, then follow it on the real closes. Each value uses only what was known that day."))),
    h("div", { class: "card" }, h("div", { class: "form-row" }, field("Product", preset), field("Underlying", name), field("Strike date", strike),
      field("Value every", every, { hint: "business days; event days are always valued" }), field("Model", model)),
      h("div", { class: "btn-row" }, h("button", { class: "btn primary", type: "button", onclick: run, disabled: !info.available }, "Replay"))),
    out,
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Runs")), list));
}
