// Book: trades, their value and risk on one shared simulation.

import { api, runJob, withToken } from "../api.js";
import { describeSheet, vegaNote } from "../format.js";
import { basketCard } from "./pricer.js";
import { save, state } from "../state.js";
import { badgeFor, clear, dateLabel, dialog, errorNotice, field, h, int, num, pm, pretty, progress, segmented,
  signed, switchControl, toast } from "../ui.js";

export async function render(page) {
  const list = h("div", { class: "card" });
  const runCard = h("div", { class: "card" });
  const out = h("div", { class: "stack", "aria-live": "polite" });
  let filter = "live";
  let trades = [];
  let greeks = true;

  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Book"),
      h("p", {}, "Every live trade priced on one simulation: the error of the total accounts for the hedges.")),
      h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/pricer" }, "New quote"),
        h("a", { class: "btn", href: withToken("/report/book"), target: "_blank", rel: "noopener" }, "Book report"))),
    h("div", { class: "stack" }, runCard, out, list));

  const load = async () => {
    try { trades = (await api("/api/trades")).trades; } catch (e) { clear(list, errorNotice(e)); return; }
    drawList();
  };

  const drawRun = () => clear(runCard,
    h("div", { class: "card-head" }, h("h2", {}, "Valuation"), h("span", { class: "muted" }, "every live trade")),
    h("div", { class: "btn-row", style: { justifyContent: "space-between" } },
      h("div", { class: "btn-row", style: { gap: "16px" } },
        segmented([["quick", "Quick"], ["standard", "Standard"], ["precise", "Precise"]], state.precision,
          (v) => { save({ precision: v }); drawRun(); }, "Precision"),
        switchControl("Delta and vega", greeks, (v) => { greeks = v; }, "b-greeks"),
        switchControl("SLV vega (slower)", state.slvVega, (v) => { save({ slvVega: v }); }, "b-slv-vega")),
      h("button", { class: "btn primary", type: "button", id: "price-book", onclick: priceBook }, "Price the book")));

  const drawList = () => {
    const shown = trades.filter((t) => filter === "all" || t.status === filter);
    clear(list,
      h("div", { class: "card-head" }, h("h2", {}, `Trades (${shown.length})`),
        segmented([["live", "Live"], ["called", "Called"], ["matured", "Matured"], ["all", "All"]], filter,
          (v) => { filter = v; drawList(); }, "Status")),
      shown.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("tr", {}, ["Trade", "Product", "Quantity", "Strike", "Status", ""].map((c, i) => h("th", { class: i === 2 ? "num" : "" }, c))),
        shown.map((t) => h("tr", {},
          h("td", {}, h("a", { href: `#/trade/${t.trade_id}` }, h("b", {}, t.trade_id)),
            t.version > 1 ? h("span", { class: "faint" }, ` v${t.version}`) : null),
          h("td", {}, t.summary ? describeSheet(t.product.term_sheet) : "script"),
          h("td", { class: "num" }, num(t.quantity, 2)),
          h("td", {}, dateLabel(t.strike_date)),
          h("td", {}, badgeFor(t.status)),
          h("td", { class: "num" }, h("div", { class: "btn-row", style: { justifyContent: "flex-end" } },
            h("a", { class: "btn sm ghost", href: `#/lifecycle/${t.trade_id}` }, "Events"),
            h("button", { class: "btn sm ghost", type: "button", onclick: () => edit(t) }, "Edit"),
            h("button", { class: "btn sm ghost danger", type: "button", onclick: () => remove(t) }, "Delete")))))))
        : h("div", { class: "empty" }, filter === "live" ? "No live trade. Quote a product and book it from the pricer." : "Nothing here."));
  };

  async function edit(t) {
    const res = await dialog({ title: `Edit ${t.trade_id}`, ok: "Save",
      body: [field("Quantity", h("input", { type: "number", name: "quantity", value: String(t.quantity), step: "any" })),
        field("Status", h("select", { name: "status" }, ["live", "called", "matured", "cancelled"].map((s) =>
          h("option", { value: s, selected: s === t.status || undefined }, s)))),
        field("Counterparty", h("input", { type: "text", name: "counterparty", value: t.counterparty || "" }))],
      read: (form) => Object.fromEntries(new FormData(form)) });
    if (!res) return;
    try {
      await api(`/api/trades/${t.trade_id}`, { method: "PUT", body: { quantity: Number(res.quantity), status: res.status,
        counterparty: res.counterparty } });
      toast(`${t.trade_id} saved (new version)`);
      load();
    } catch (e) { toast(e.message); }
  }

  async function remove(t) {
    const ok = await dialog({ title: `Delete ${t.trade_id}?`, ok: "Delete", danger: true,
      body: h("p", {}, "The trade leaves the book. You can undo it from the message that follows.") });
    if (!ok) return;
    await api(`/api/trades/${t.trade_id}`, { method: "DELETE" });
    toast(`${t.trade_id} deleted`, { action: "Undo", timeout: 8000, onAction: async () => {
      await api(`/api/trades/${t.trade_id}/restore`, { method: "POST" });
      load();
    } });
    load();
  }

  async function priceBook() {
    const btn = document.getElementById("price-book");
    btn.disabled = true;
    const started = performance.now();
    const status = progress("Pricing the book…");
    clear(out, h("div", { class: "card" }, status));
    try {
      const { job } = await api("/api/book/price", { method: "POST", body: { options: { precision: state.precision, greeks,
        slv_vega: state.slvVega } } });
      const res = await runJob(job, (j) => {
        status.querySelector("p").textContent = `${j.state} · ${num((performance.now() - started) / 1000, 1)} s`;
      });
      res.when = new Date().toLocaleString("en-GB");
      save({ lastBook: res });
      drawResult(res);
    } catch (e) {
      clear(out, errorNotice(e));
    } finally {
      btn.disabled = false;
    }
  }

  function drawResult(res) {
    const g = res.greeks && res.greeks.by_name ? res.greeks : null;  // older runs kept in the browser: no risk
    const n = res.positions.length;
    const groups = res.groups || [];
    const several = groups.length > 1;
    clear(out,
      res.reserve != null ? h("div", { class: "grid cols-3" },
        kpi("Reserves left", num(res.reserve, 2), `of ${num(res.reserve_initial, 2)} at issue · frozen with each trade`),
        kpi("Value net of reserves", num(res.value_net, 2), "the book's value less what is left"),
        kpi("Booked without a reserve", String(res.no_reserve.length), res.no_reserve.length ? res.no_reserve.slice(0, 4).join(", ") : "every trade carries one")) : null,
      h("div", { class: "grid cols-3" },
        kpi("Book value", pm(res.value, res.stderr, 2), `currency · ${n} position${n > 1 ? "s" : ""}`),
        kpi("Error if legs were independent", num(res.independent_stderr, 2),
          n > 1 ? "against the error of the total: positions on one simulation net out" : "one position: the same"),
        kpi("Run", `${num(res.seconds, 1)} s`,
          `${int(res.n_paths)} paths · ${several ? `${groups.length} independent simulations` : pretty(res.model)}`)),
      g ? h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Risk by underlying"),
          h("span", { class: "muted" }, "currency per +1 % spot · per vol point")),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Underlying"), h("th", { class: "num" }, "Delta"), h("th", { class: "num" }, "Vega")),
          Object.entries(g.by_name).map(([name, r]) => h("tr", {}, h("td", {}, name),
            h("td", { class: "num" }, pm(r.delta, r.delta_se, 3)),
            h("td", { class: "num" }, r.vega != null ? pm(r.vega, r.vega_se, 3) : `– (not for ${r.vega_missing.join(", ")})`))))),
        g.note ? h("p", { class: "muted", style: { marginTop: "8px" } }, vegaNote(g.note)) : null) : null,
      several ? h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Simulations"),
          h("span", { class: "muted" }, "independent: their errors add in quadrature")),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Group", "Positions", "Model", "Value", "Time"].map((c, k) => h("th", { class: k === 1 || k > 2 ? "num" : "" }, c))),
          groups.map((x) => h("tr", {}, h("td", {}, pretty(x.label)), h("td", { class: "num" }, x.positions),
            h("td", {}, `${pretty(x.method)} (${pretty(x.model)})`), h("td", { class: "num" }, pm(x.value, x.stderr, 2)),
            h("td", { class: "num" }, `${num(x.seconds, 1)} s`))))),
        groups.filter((x) => x.basket).map((x) => h("div", { style: { marginTop: "12px" } }, basketCard(x.basket)))) : null,
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Positions"),
          h("span", { class: "muted" }, several ? "" : `${pretty(res.method)} (${pretty(res.model)})`)),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Trade", several ? "Group" : null, "Class", "Quantity", "Unit price %", "Value", "SE", "Delta", "Reserve left"]
            .filter((c) => c !== null).map((c, k, all) => h("th", { class: k >= all.indexOf("Quantity") ? "num" : "" }, c))),
          res.positions.map((p, k) => h("tr", {}, h("td", {}, h("a", { href: `#/trade/${p.name}` }, h("b", {}, p.name))),
            several ? h("td", {}, pretty(p.group)) : null, h("td", {}, pretty(p.class)),
            h("td", { class: "num" }, num(p.quantity, 2)), h("td", { class: "num" }, pm(p.unit_price, p.unit_stderr)),
            h("td", { class: "num" }, num(p.value, 2)), h("td", { class: "num" }, num(p.stderr, 2)),
            h("td", { class: "num" }, g && g.positions[k] != null ? signed(g.positions[k], 3) : "–"),
            h("td", { class: "num" }, p.reserve ? num(p.reserve.amount, 2) : h("span", { class: "faint" }, "none booked")))))),
        res.warnings.map((w) => h("div", { class: "notice", style: { marginTop: "8px" } }, w))));
  }

  drawRun();
  if (state.lastBook) drawResult(state.lastBook);
  await load();
}

function kpi(label, value, sub) {
  return h("div", { class: "card tight kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    h("div", { class: "sub" }, sub));
}
