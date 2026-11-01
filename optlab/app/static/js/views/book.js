// Book: trades, their value and risk on one shared simulation.

import { api, runJob, withToken } from "../api.js";
import { describeSheet, vegaNote } from "../format.js";
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
    h("div", { class: "card-head" }, h("h2", {}, "Valuation"), h("span", { class: "muted" }, "single-name trades")),
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
          h("td", {}, h("b", {}, t.trade_id), t.version > 1 ? h("span", { class: "faint" }, ` v${t.version}`) : null),
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
    const g = res.greeks;
    const n = res.positions.length;
    clear(out,
      h("div", { class: "grid cols-4" },
        kpi("Book value", pm(res.value, res.stderr, 2), `currency · ${n} position${n > 1 ? "s" : ""}`),
        kpi("Error if legs were independent", num(res.independent_stderr, 2),
          n > 1 ? "against the error of the total: the shared simulation nets the legs" : "one position: the same"),
        kpi("Delta", g && g.delta ? pm(g.delta.total, g.delta.total_se, 3) : "–", "currency per +1 % spot"),
        kpi("Vega", g && g.vega ? pm(g.vega.total, g.vega.total_se, 3) : "–", g && !g.vega ? vegaNote(g.note) : "currency per vol point")),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Positions"),
          h("span", { class: "muted" },
            `${pretty(res.method)} (${pretty(res.model)}) · ${int(res.n_paths)} paths · ${num(res.seconds, 1)} s`)),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, ["Trade", "Class", "Quantity", "Unit price %", "Value", "SE", "Delta"].map((c, i) =>
            h("th", { class: i > 1 ? "num" : "" }, c))),
          res.positions.map((p, i) => h("tr", {}, h("td", {}, h("b", {}, p.name)), h("td", {}, pretty(p.class)),
            h("td", { class: "num" }, num(p.quantity, 2)), h("td", { class: "num" }, pm(p.unit_price, p.unit_stderr)),
            h("td", { class: "num" }, num(p.value, 2)), h("td", { class: "num" }, num(p.stderr, 2)),
            h("td", { class: "num" }, g && g.delta ? signed(g.delta.positions[i], 3) : "–"))))),
        res.skipped && res.skipped.length ? h("div", { class: "notice", style: { marginTop: "10px" } },
          `Not in this valuation: ${res.skipped.map((s) => `${s.trade_id} (${s.reason})`).join(", ")}`) : null,
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
