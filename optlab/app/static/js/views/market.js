// Market: the snapshot behind every price, its smile and term structure, model risk, and the other names of
// worst-ofs (not calibrated here: their volatility, dividend and correlations are inputs).

import { api } from "../api.js";
import { legend, lineChart } from "../charts.js";
import { desk } from "../state.js";
import { clear, dateLabel, dialog, errorNotice, h, num, pretty, toast } from "../ui.js";

export async function render(page) {
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Market"))), h("div", { class: "skeleton", style: { height: "240px" } }));
  let ctx, term, risk, snaps, basket;
  try {
    [ctx, term, risk, snaps, basket] = await Promise.all([api("/api/context"), api("/api/market/term"),
      api("/api/market/risk"), api("/api/market/snapshots"), api("/api/basket")]);
  } catch (e) { clear(page, errorNotice(e)); return; }
  desk.context = ctx;
  const smileCard = h("div", { class: "card" });
  const basketCard = h("div", { class: "card" });
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
      legend(series),
      h("details", { class: "more" }, h("summary", {}, "Values"),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Strike / forward"), data.map((d) => h("th", { class: "num" }, `${num(d.T, 2)} y`))),
          data[0].moneyness.map((m, i) => i % 5 ? null : h("tr", {}, h("td", {}, `${num(100 * m, 0)} %`),
            data.map((d) => h("td", { class: "num" }, `${num(100 * d.iv[i], 2)} %`))))))));
  };

  const use = async (s) => {
    const ok = await dialog({ title: "Price on this market?", ok: "Use it",
      body: h("p", {}, `${s.snapshot_id} (${s.source}, valuation ${dateLabel(s.valuation)}). Quotes, the book and the `
        + "lifecycle switch to it; runs in progress finish on the market they started with.") });
    if (!ok) return;
    try {
      desk.context = await api("/api/market/use", { method: "POST", body: { snapshot_id: s.snapshot_id } });
      window.dispatchEvent(new Event("optlab:jobs"));  // the status bar names the market
      toast(`Now pricing on ${s.snapshot_id}`);
      render(page);
    } catch (e) { toast(e.message); }
  };

  // --- other names of worst-ofs ---
  let rows = Object.entries(basket.names).map(([n, p]) => ({ name: n, vol: p.vol, q: p.q, r: p.r }));
  let corr = { ...basket.corr };
  let error = null;
  const d = basket.defaults;
  const pctIn = (value, onSet, label, placeholder) => h("input", { type: "number", step: "any", "aria-label": label,
    value: value == null ? "" : String(+(100 * value).toFixed(4)), placeholder,
    onchange: (e) => onSet(e.target.value === "" ? null : Number(e.target.value) / 100) });
  const key = (a, b) => [a, b].sort().join("|");

  const drawBasket = () => {
    const names = [basket.calibrated, ...rows.map((r) => r.name).filter(Boolean)];
    clear(basketCard,
      h("div", { class: "card-head" }, h("h2", {}, "Other underlyings (worst-of)"),
        h("div", { class: "btn-row" },
          h("button", { class: "btn sm", type: "button", onclick: () => { rows.push({ name: "", vol: d.vol, q: d.q, r: null }); drawBasket(); } }, "Add a name"),
          h("button", { class: "btn sm primary", type: "button", onclick: saveBasket }, "Save"))),
      h("p", { class: "muted" }, `Only ${basket.calibrated} is calibrated. Other names are flat Black-Scholes markets rescaled to 100; `
        + `a name not listed here uses ${num(100 * d.vol, 0)} % volatility, ${num(100 * d.q, 1)} % dividend, the `
        + `${basket.calibrated} 1-year rate (${num(100 * d.r, 2)} %) and a correlation of ${num(d.corr, 2)}.`),
      error ? h("div", { class: "notice err", role: "alert", style: { marginBottom: "10px" } }, error) : null,
      rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "data nosort" },
        h("tr", {}, ["Name", "Volatility %", "Dividend %", "Rate %", ""].map((c) => h("th", {}, c))),
        rows.map((r, i) => h("tr", {},
          h("td", {}, h("input", { type: "text", value: r.name, "aria-label": "Name", placeholder: "SX5E", style: { width: "90px" },
            onchange: (e) => { r.name = e.target.value.trim().toUpperCase(); drawBasket(); } })),
          h("td", {}, pctIn(r.vol, (v) => { r.vol = v; }, `Volatility of ${r.name}`)),
          h("td", {}, pctIn(r.q, (v) => { r.q = v; }, `Dividend of ${r.name}`)),
          h("td", {}, pctIn(r.r, (v) => { r.r = v; }, `Rate of ${r.name}`, `${num(100 * d.r, 2)}`)),
          h("td", {}, h("button", { class: "btn sm ghost danger", type: "button",
            onclick: () => { rows.splice(i, 1); drawBasket(); } }, "Remove"))))))
        : h("div", { class: "empty" }, "No other name yet: worst-ofs use the defaults above."),
      names.length > 1 ? h("div", { style: { marginTop: "14px" } }, h("h3", {}, "Correlations"),
        h("div", { class: "table-wrap" }, h("table", { class: "data nosort" },
          h("tr", {}, h("th", {}, ""), names.map((n) => h("th", { class: "num" }, n))),
          names.map((a, i) => h("tr", {}, h("th", {}, a), names.map((b, j) => h("td", { class: "num" },
            i === j ? "1" : j < i ? num(corr[key(a, b)] ?? d.corr, 2)
              : h("input", { type: "number", step: "0.05", min: "-0.99", max: "0.99", style: { width: "72px" },
                "aria-label": `Correlation ${a} ${b}`, value: String(corr[key(a, b)] ?? d.corr),
                onchange: (e) => { corr[key(a, b)] = Number(e.target.value); drawBasket(); } }))))))))
        : null);
  };

  async function saveBasket() {
    const names = Object.fromEntries(rows.filter((r) => r.name).map((r) => [r.name, { vol: r.vol, q: r.q, r: r.r }]));
    const known = new Set([basket.calibrated, ...Object.keys(names)]);
    const pairs = Object.fromEntries(Object.entries(corr).filter(([k]) => k.split("|").every((n) => known.has(n))));
    try {
      basket = await api("/api/basket", { method: "PUT", body: { names, corr: pairs } });
      rows = Object.entries(basket.names).map(([n, p]) => ({ name: n, vol: p.vol, q: p.q, r: p.r }));
      corr = { ...basket.corr };
      error = null;
      toast("Other underlyings saved: the next worst-of quotes use them");
    } catch (e) { error = e.message; }
    drawBasket();
  }

  const models = Object.keys(Object.values(risk.exotics)[0] || {});
  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Market"),
      h("p", {}, `Snapshot ${ctx.snapshot_id} · valuation ${dateLabel(ctx.valuation)}${risk.note ? ` · ${risk.note}` : ""}`))),
    h("div", { class: "stack" },
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Market snapshots"),
          h("span", { class: "muted" }, "immutable, identified by their content")),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Valuation", "Source", "Snapshot", "File", ""].map((c) => h("th", {}, c))),
          snaps.snapshots.map((s) => h("tr", {}, h("td", {}, dateLabel(s.valuation)), h("td", {}, pretty(s.source)),
            h("td", {}, h("code", {}, s.snapshot_id)), h("td", { class: "muted" }, s.file || "–"),
            h("td", { class: "num" }, s.active ? h("span", { class: "badge pos" }, "In use")
              : h("button", { class: "btn sm", type: "button", onclick: () => use(s) }, "Use"))))))),
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
      basketCard,
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Model risk"), h("span", { class: "muted" }, "1 year, % of spot, standard error in brackets")),
        models.length ? [h("p", { class: "muted" }, "Four models that agree on vanillas disagree on path-dependent payoffs."),
          h("div", { class: "table-wrap" }, h("table", { class: "data" },
            h("tr", {}, h("th", {}, "Product"), models.map((m) => h("th", { class: "num" }, m))),
            Object.entries(risk.exotics).map(([p, row]) => h("tr", {}, h("td", {}, p),
              models.map((m) => h("td", { class: "num" }, `${num(row[m].price, 3)} (${num(row[m].stderr, 3)})`))))))]
          : h("div", { class: "empty" }, "No model-risk table stored with this snapshot.")),
      risk.errors.length ? h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Calibration fit"), h("span", { class: "muted" }, "basis points of implied vol")),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Expiry", "T", "Quotes", "SVI", "Heston", "Local vol", "SLV"].map((c, i) => h("th", { class: i ? "num" : "" }, c))),
          risk.errors.map((e) => h("tr", {}, h("td", {}, e.expiry), h("td", { class: "num" }, num(e.T, 2)),
            h("td", { class: "num" }, e.n_quotes), h("td", { class: "num" }, num(e.svi_rmse_bp, 1)),
            h("td", { class: "num" }, num(e.heston_rmse_bp, 1)), h("td", { class: "num" }, num(e.lv_mae_bp, 1)),
            h("td", { class: "num" }, num(e.slv_mae_bp, 1))))))) : null));
  drawBasket();
  await drawSmiles();
}
