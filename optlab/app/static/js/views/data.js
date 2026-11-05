// Data: what the historical lake holds, where each table comes from, its quality, and a market at a past date.

import { api, runJob } from "../api.js";
import { lineChart } from "../charts.js";
import { desk } from "../state.js";
import { clear, dateLabel, errorNotice, field, h, int, num, originBadge, pretty, progress, toast } from "../ui.js";

function table(headers, rows) {
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("tr", {}, headers.map((t) => h("th", {}, t))), rows));
}

export async function render(page) {
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Data"))), h("div", { class: "skeleton", style: { height: "240px" } }));
  let d;
  try { d = await api("/api/data/overview"); } catch (e) { clear(page, errorNotice(e)); return; }
  if (!d.available) {
    clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Data"))),
      h("div", { class: "notice" }, `The lake is empty: ${d.hint}.`));
    return;
  }
  const resultCard = h("div", {});
  const name = h("select", { id: "lake-name" }, d.instruments.filter((i) => i.enabled).map((i) => h("option", { value: i.id }, i.id)));
  const date = h("input", { id: "lake-date", type: "date", value: "2020-03-23" });
  const lever = h("input", { id: "lake-lev", type: "checkbox", checked: true });

  const buildMarket = async () => {
    clear(resultCard, progress("Building the market of that date"));
    try {
      const { job } = await api("/api/data/market", { method: "POST", body: { underlying: name.value, date: date.value, leverage: lever.checked } });
      const result = await runJob(job, () => {});
      const r = result;
      const p = r.provenance;
      clear(resultCard,
        h("h3", {}, `${r.underlying} on ${dateLabel(r.valuation)}: ${r.snapshot_id}`),
        table(["Part", "Origin", "Detail"], [
          h("tr", {}, h("td", {}, "Spot"), h("td", {}, originBadge(p.spot.origin)), h("td", {}, `close of ${dateLabel(p.spot.date)}`)),
          h("tr", {}, h("td", {}, "Discount curve"), h("td", {}, originBadge(p.discount.origin)), h("td", {}, `${p.discount.curve}: Treasury or AAA, not OIS`)),
          h("tr", {}, h("td", {}, "Carry"), h("td", {}, originBadge(p.carry.origin)), h("td", {}, `${pretty(p.carry.method)}, yield ${num(100 * p.carry.dividend_yield, 2)} %`)),
          h("tr", {}, h("td", {}, "Surface"), h("td", {}, originBadge(p.surface.origin)),
            h("td", {}, `${p.surface.layer}, ${dateLabel(p.surface.date)}; term structure assumed beyond ${num(p.surface.extrapolated_from, 2)} y`)),
          h("tr", {}, h("td", {}, "Heston"), h("td", {}, originBadge(p.heston.origin)), h("td", {}, p.heston.mode === "prior" ? "dynamics fixed from a 2026 calibration, levels from the surface" : "fitted on the surface")),
          h("tr", {}, h("td", {}, "SLV leverage"), h("td", {}, originBadge(p.leverage.origin)), h("td", {}, p.leverage.mode === "calibrate" ? "particles on this surface" : "flat: price under local vol only")),
        ]),
        h("div", { class: "btn-row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", type: "button", onclick: async () => {
          desk.context = await api("/api/market/use", { method: "POST", body: { snapshot_id: r.snapshot_id } });
          window.dispatchEvent(new Event("optlab:jobs"));
          toast(`Now pricing on ${r.snapshot_id}`);
        } }, "Price on this market")));
    } catch (e) { clear(resultCard, errorNotice(e)); }
  };

  const volCard = h("div", { class: "card" });
  const drawVol = async (index) => {
    if (d.shared) { clear(volCard, h("div", { class: "card-head" }, h("h2", {}, "Volatility indices")), h("p", {}, "Index levels stay on the owner's machine.")); return; }
    try {
      const v = await api(`/api/data/vol?name=${index}&start=2018-01-01`);
      const step = Math.max(1, Math.floor(v.dates.length / 400));
      const points = v.dates.filter((_, i) => i % step === 0).map((x, i) => [Date.parse(x), v.close[i * step]]);
      clear(volCard, h("div", { class: "card-head" }, h("h2", {}, `${index} since 2018`), originBadge(v.origin)),
        lineChart([{ name: index, points }], { xFormat: (t) => new Date(t).toISOString().slice(0, 7), yFormat: (x) => x.toFixed(0), xLabel: "date", label: `${index} level since 2018` }),
        h("div", { class: "btn-row" }, ["VIX", "VIX1Y", "VVIX", "SKEW", "V2TX", "VXN", "RVX", "VXAPL"].map((n) => h("button", { class: "btn sm", type: "button", onclick: () => drawVol(n) }, n))));
    } catch (e) { clear(volCard, errorNotice(e)); }
  };

  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Data"),
      h("p", {}, `${d.raw_files} raw downloads in the manifest${d.lake ? ` · lake ${d.lake}` : ""}. Personal-use data: nothing here is versioned.`))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Coverage and origin")),
      table(["Table", "Origin", "Source", "Rows", "First", "Last"], d.coverage.map((c) => h("tr", {},
        h("td", {}, pretty(c.dataset)), h("td", {}, originBadge(c.origin)), h("td", {}, c.source), h("td", { class: "num" }, int(c.n)),
        h("td", {}, c.first.slice(0, 10)), h("td", {}, c.last.slice(0, 10)))))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "A market at a past date"), originBadge("derived")),
      h("p", {}, "Only rows dated at or before that date are read. The result is stored like any market snapshot."),
      h("div", { class: "fields" }, field("Underlying", name), field("Date", date), field("Calibrate the SLV leverage", lever)),
      h("div", { class: "btn-row" }, h("button", { class: "btn primary", type: "button", onclick: buildMarket }, "Build the market")),
      resultCard),
    volCard,
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Universe")),
      h("p", {}, "Chosen in 2026, so a replay on it carries a survivorship bias. Names not loaded yet are declared and off."),
      table(["Name", "Class", "Currency", "Place", "Vol index", "Prices", "Last close", "Status"], d.instruments.map((i) => h("tr", {},
        h("td", {}, `${i.id} · ${i.name}`), h("td", {}, i.asset_class), h("td", {}, i.currency), h("td", {}, i.place),
        h("td", {}, i.vol_index || "none"), h("td", {}, i.origin ? originBadge(i.origin) : "–"),
        h("td", {}, i.last ? String(i.last).slice(0, 10) : "–"), h("td", {}, i.enabled ? "loaded" : "off"))))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Quality")),
      d.issues.length ? table(["Table", "Check", "Severity", "Count"], d.issues.map((c) => h("tr", {},
        h("td", {}, pretty(c.dataset)), h("td", {}, pretty(c.check_name)), h("td", {}, c.severity), h("td", { class: "num" }, int(c.n))))) : h("p", {}, "No issue recorded."),
      d.recent_issues.length ? h("details", { class: "more" }, h("summary", {}, "Latest issues"),
        table(["Date", "Item", "Check", "Detail"], d.recent_issues.map((c) => h("tr", {}, h("td", {}, c.obs_date.slice(0, 10)),
          h("td", {}, c.item), h("td", {}, pretty(c.check_name)), h("td", {}, c.detail))))) : null));
  drawVol("VIX");
}
