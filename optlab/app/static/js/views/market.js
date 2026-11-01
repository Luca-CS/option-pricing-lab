// Market: the calibration behind every price.

import { api } from "../api.js";
import { legend, lineChart } from "../charts.js";
import { desk } from "../state.js";
import { clear, dateLabel, errorNotice, h, num } from "../ui.js";

export async function render(page) {
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Market"))), h("div", { class: "skeleton", style: { height: "240px" } }));
  let ctx, term, risk;
  try {
    [ctx, term, risk] = await Promise.all([desk.context || api("/api/context"), api("/api/market/term"), api("/api/market/risk")]);
  } catch (e) { clear(page, errorNotice(e)); return; }
  const smileCard = h("div", { class: "card" });
  const mats = ctx.maturities.filter((T) => T >= 0.08);
  let chosen = [mats[Math.floor(mats.length / 3)], mats[mats.length - 3], mats[mats.length - 1]].filter(Boolean);

  const drawSmiles = async () => {
    const data = await Promise.all(chosen.map((T) => api(`/api/market/smile?T=${T}`)));
    const series = data.map((d) => ({ name: `${num(d.T, 2)} y`, points: d.moneyness.map((m, i) => [100 * m, 100 * d.iv[i]]) }));
    clear(smileCard,
      h("div", { class: "card-head" }, h("h2", {}, "Implied volatility smile (SVI)"),
        h("div", { class: "btn-row" }, mats.map((T) => h("button", { class: "btn sm", type: "button",
          "aria-pressed": String(chosen.includes(T)), style: chosen.includes(T) ? { borderColor: "var(--accent)", color: "var(--accent)" } : {},
          onclick: () => { chosen = chosen.includes(T) ? chosen.filter((x) => x !== T) : [...chosen, T].sort((a, b) => a - b).slice(-4);
            if (!chosen.length) chosen = [T]; drawSmiles(); } }, `${num(T, 2)}y`)))),
      lineChart(series, { xFormat: (v) => `${v.toFixed(0)} %`, yFormat: (v) => `${v.toFixed(0)} %`, xLabel: "strike / forward",
        label: "Implied volatility against strike over forward" }),
      legend(series));
  };

  const models = Object.keys(Object.values(risk.exotics)[0] || {});
  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Market"),
      h("p", {}, `Snapshot ${ctx.snapshot_id} · valuation ${dateLabel(ctx.valuation)} · ${risk.note || ""}`))),
    h("div", { class: "stack" },
      smileCard,
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h2", {}, "At-the-money volatility"),
          lineChart([{ name: "ATM vol", points: term.T.map((T, i) => [T, 100 * term.atm_vol[i]]) }],
            { xFormat: (v) => `${v.toFixed(1)}y`, yFormat: (v) => `${v.toFixed(1)} %`, label: "ATM volatility term structure", height: 220 })),
        h("div", { class: "card" }, h("h2", {}, "Forward and discount"),
          lineChart([{ name: "Forward / spot", points: term.T.map((T, i) => [T, term.forward[i]]) },
            { name: "Discount factor", points: term.T.map((T, i) => [T, term.discount[i]]) }],
          { xFormat: (v) => `${v.toFixed(1)}y`, yFormat: (v) => v.toFixed(3), label: "Forward over spot and discount factor", height: 220 }),
          legend([{ name: "Forward / spot" }, { name: "Discount factor" }]))),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Model risk"), h("span", { class: "muted" }, "1 year, % of spot, standard error in brackets")),
        h("p", { class: "muted" }, "Four models that agree on vanillas disagree on path-dependent payoffs."),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Product"), models.map((m) => h("th", { class: "num" }, m))),
          Object.entries(risk.exotics).map(([p, row]) => h("tr", {}, h("td", {}, p),
            models.map((m) => h("td", { class: "num" }, `${num(row[m].price, 3)} (${num(row[m].stderr, 3)})`))))))),
      risk.errors.length ? h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Calibration fit"), h("span", { class: "muted" }, "basis points of implied vol")),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Expiry", "T", "Quotes", "SVI", "Heston", "Local vol", "SLV"].map((c, i) => h("th", { class: i ? "num" : "" }, c))),
          risk.errors.map((e) => h("tr", {}, h("td", {}, e.expiry), h("td", { class: "num" }, num(e.T, 2)),
            h("td", { class: "num" }, e.n_quotes), h("td", { class: "num" }, num(e.svi_rmse_bp, 1)),
            h("td", { class: "num" }, num(e.heston_rmse_bp, 1)), h("td", { class: "num" }, num(e.lv_mae_bp, 1)),
            h("td", { class: "num" }, num(e.slv_mae_bp, 1))))))) : null));
  await drawSmiles();
}
