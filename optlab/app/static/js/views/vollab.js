// Vol lab: fit SVI, SSVI, Heston and SLV on a day's real quotes, measured against the bid-ask, with every run stored.

import { api, runJob } from "../api.js";
import { legend, lineChart } from "../charts.js";
import { clear, errorNotice, field, h, int, num, originBadge, progress, toast } from "../ui.js";

const pc = (x, d = 0) => (x === null || x === undefined ? "–" : `${num(100 * x, d)} %`);
const bp = (x) => (x === null || x === undefined ? "–" : num(x, 1));

export async function render(page) {
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Vol lab"))), h("div", { class: "skeleton", style: { height: "240px" } }));
  let days, runs;
  try { [days, runs] = await Promise.all([api("/api/vollab/days"), api("/api/vollab/runs")]); } catch (e) { clear(page, errorNotice(e)); return; }
  if (!days.available) {
    clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Vol lab"))),
      h("div", { class: "notice" }, "No option quotes in the lake yet: run scripts/lake_update.py --import-sample."));
    return;
  }
  const sets = days.sets;
  const preferred = sets.find((s) => s.underlying === "SPX" && s.source === "historicaldata") || sets[0];
  const setSel = h("select", { id: "lab-set" }, sets.map((s, i) => h("option", { value: i, selected: s === preferred },
    `${s.underlying} · ${s.source} · ${s.n} days${s.quality.includes("suspect") ? " · taken outside market hours" : ""}`)));
  const daySel = h("select", { id: "lab-day" });
  const fillDays = () => {
    const s = sets[+setSel.value];
    clear(daySel, ...s.days.slice().reverse().map((d) => h("option", { value: d }, d)));
  };
  setSel.addEventListener("change", fillDays);
  fillDays();
  const boxes = days.models.map((m) => {
    const box = h("input", { type: "checkbox", id: `m-${m}`, checked: m !== "slv" });
    return { m, box, label: h("label", { for: `m-${m}`, style: { marginRight: "14px" } }, box, ` ${m.toUpperCase()}`) };
  });
  const out = h("div", {});
  const runsCard = h("div", { class: "card" });

  const drawRuns = (list) => clear(runsCard, h("div", { class: "card-head" }, h("h2", {}, "Runs"), originBadge("derived")),
    h("p", {}, "Each fit is stored with a hash of its quotes and its settings, and can be replayed from that row."),
    list.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("tr", {}, ["Run", "Name", "Day", "Source", "Model", "RMSE, bp", "Within spread", "Seconds", "Code", ""].map((t) => h("th", {}, t))),
      list.map((r) => h("tr", {}, h("td", {}, r.calibration_id), h("td", {}, r.underlying), h("td", {}, r.day), h("td", {}, r.source),
        h("td", {}, r.model.toUpperCase()), h("td", { class: "num" }, bp(r.rmse_bp)), h("td", { class: "num" }, pc(r.within_spread)),
        h("td", { class: "num" }, num(r.seconds, 1)), h("td", {}, r.code_version),
        h("td", { class: "num" }, h("button", { class: "btn sm", type: "button", onclick: async (ev) => {
          ev.target.disabled = true;
          ev.target.textContent = "Replaying";
          try {
            const { job } = await api(`/api/vollab/runs/${r.calibration_id}/reproduce`, { method: "POST", body: {} });
            const result = await runJob(job, () => {});
            const x = result;
            toast(x.same_quotes && x.largest_parameter_gap < 1e-8 ? `${r.calibration_id}: same parameters` : `${r.calibration_id}: differs by ${x.largest_parameter_gap}`);
            ev.target.textContent = x.largest_parameter_gap < 1e-8 ? "Same" : "Differs";
          } catch (e) { toast(e.message); ev.target.textContent = "Failed"; }
        } }, "Replay")))))) : h("p", {}, "No run yet."));
  drawRuns(runs.runs);

  const draw = (res, quotes) => {
    const models = Object.keys(res.fits);
    const mats = res.maturities;
    let chosen = mats[Math.floor(mats.length / 2)];
    const smile = h("div", {});
    const drawSmile = () => {
      const series = [];
      const near = (list) => list.reduce((a, b) => (Math.abs(b.T - chosen) < Math.abs(a.T - chosen) ? b : a));
      if (quotes) {
        const q = near(quotes.slices);
        series.push({ name: "Bid", points: q.moneyness.map((m, i) => [100 * m, 100 * q.iv_bid[i]]) });
        series.push({ name: "Ask", points: q.moneyness.map((m, i) => [100 * m, 100 * q.iv_ask[i]]) });
      }
      for (const m of models) {
        const c = near(res.fits[m].curves);
        series.push({ name: m.toUpperCase(), points: c.moneyness.map((x, i) => [100 * x, 100 * c.iv[i]]) });
      }
      clear(smile,
        h("div", { class: "btn-row" }, mats.map((T) => h("button", { class: "btn sm", type: "button", "aria-pressed": String(T === chosen),
          style: T === chosen ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}, onclick: () => { chosen = T; drawSmile(); } }, `${num(T, 2)} y`))),
        lineChart(series, { xFormat: (v) => `${v.toFixed(0)} %`, yFormat: (v) => `${v.toFixed(0)} %`, xLabel: "strike / forward",
          label: `Implied volatility at ${num(chosen, 2)} years, quotes and fitted models` }),
        legend(series),
        quotes ? null : h("p", {}, "The quotes stay on the owner's machine: this shared desk shows the fits and their errors only."));
    };
    drawSmile();
    clear(out,
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `${res.underlying}, ${res.day}, ${res.source}`), originBadge(res.origin)),
        h("p", {}, `Quotes ${res.quotes_hash}. Models are fits on observed quotes: an error inside the bid-ask is no error.`),
        smile),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Errors against the quotes")),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Model", "Quotes", "RMSE, bp", "Within spread", "Seconds", "Run"].map((t) => h("th", {}, t))),
          models.map((m) => { const f = res.fits[m]; return h("tr", {}, h("td", {}, m.toUpperCase()), h("td", { class: "num" }, int(f.overall.n)),
            h("td", { class: "num" }, bp(f.overall.rmse_bp)), h("td", { class: "num" }, pc(f.overall.within_spread)),
            h("td", { class: "num" }, num(f.seconds, 1)), h("td", {}, f.calibration_id)); }))),
        h("h3", {}, "By maturity: RMSE in bp, then share within the spread"),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Maturity"), h("th", { class: "num" }, "Half spread, bp"), models.map((m) => h("th", { class: "num" }, m.toUpperCase()))),
          mats.map((T, i) => h("tr", {}, h("td", {}, `${num(T, 2)} y`),
            h("td", { class: "num" }, bp(res.fits[models[0]].by_maturity[i].median_half_spread_bp)),
            models.map((m) => { const r = res.fits[m].by_maturity[i]; return h("td", { class: "num" }, `${bp(r.rmse_bp)} · ${pc(r.within_spread)}`); }))))),
        models.includes("slv") ? h("p", {}, `SLV is repriced by Monte Carlo on a few strikes per maturity (standard error about ${bp(res.fits.slv.by_maturity[0].mc_stderr_bp)} bp), so its share within the spread uses interpolated quotes.`) : null,
        h("details", { class: "more" }, h("summary", {}, "Parameters"),
          models.map((m) => h("div", {}, h("h3", {}, m.toUpperCase()), h("pre", { style: { whiteSpace: "pre-wrap", fontSize: "12px" } }, JSON.stringify(res.fits[m].params)))))));
  };

  const fit = async () => {
    const s = sets[+setSel.value];
    const models = boxes.filter((b) => b.box.checked).map((b) => b.m);
    clear(out, progress(`Fitting ${models.map((m) => m.toUpperCase()).join(", ")}`));
    try {
      const { job } = await api("/api/vollab/fit", { method: "POST", body: { underlying: s.underlying, day: daySel.value, source: s.source, models } });
      const result = await runJob(job, () => {});
      const quotes = days.shared ? null : await api(`/api/vollab/quotes?underlying=${s.underlying}&day=${daySel.value}&source=${s.source}`);
      draw(result, quotes);
      drawRuns((await api("/api/vollab/runs")).runs);
    } catch (e) { clear(out, errorNotice(e)); }
  };

  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Vol lab"),
      h("p", {}, "Real quotes against four models, day by day. Only days with a real chain appear here: reconstructed surfaces have nothing to be fitted to."))),
    h("div", { class: "card" }, h("div", { class: "fields" }, field("Quotes", setSel), field("Day", daySel)),
      h("div", { style: { margin: "8px 0" } }, boxes.map((b) => b.label)),
      h("div", { class: "btn-row" }, h("button", { class: "btn primary", type: "button", onclick: fit }, "Fit"))),
    out, runsCard);
}
