// Trade: one position in full — its term sheet, its value at today's market, its events and its versions.

import { api, runJob } from "../api.js";
import { describeSheet, display, suffix, vegaNote } from "../format.js";
import { save, state } from "../state.js";
import { badgeFor, clear, dateLabel, dialog, errorNotice, field, h, num, pm, pretty, progress, segmented, signed,
  switchControl, toast } from "../ui.js";
import { basketCard } from "./pricer.js";

const KIND = {
  coupon_paid: ["pos", "Coupon paid"], coupon_missed: ["neg", "Coupon missed"], called: ["accent", "Called"],
  knocked_in: ["neg", "Knocked in"], matured: ["accent", "Matured"],
};

export async function render(page, id) {
  if (!id) { location.hash = "#/book"; return; }
  clear(page, h("div", { class: "skeleton", style: { height: "200px" } }));
  let detail, ev;
  try {
    [detail, ev] = await Promise.all([api(`/api/trades/${id}`), api(`/api/trades/${id}/events`)]);
  } catch (e) {
    clear(page, h("div", { class: "page-head" }, h("h1", {}, id)), errorNotice(e),
      h("p", {}, h("a", { href: "#/book" }, "Back to the book")));
    return;
  }
  const t = detail.trade;
  const sheet = t.product.term_sheet;
  const valueCard = h("div", { class: "card" });
  const valueOut = h("div", { "aria-live": "polite" });
  let greeks = false;

  const quoteCopy = () => {
    const { type, ...values } = sheet;
    save({ draft: { type, values: { ...values, general: { ...values.general, strike_date: null } }, preset: null } });
    location.hash = "#/pricer";
  };

  const edit = async () => {
    const res = await dialog({ title: `Edit ${t.trade_id}`, ok: "Save",
      body: [field("Quantity", h("input", { type: "number", name: "quantity", value: String(t.quantity), step: "any" })),
        field("Status", h("select", { name: "status" }, ["live", "called", "matured", "cancelled"].map((s) =>
          h("option", { value: s, selected: s === t.status || undefined }, pretty(s))))),
        field("Counterparty", h("input", { type: "text", name: "counterparty", value: t.counterparty || "" }))],
      read: (form) => Object.fromEntries(new FormData(form)) });
    if (!res) return;
    try {
      await api(`/api/trades/${t.trade_id}`, { method: "PUT", body: { quantity: Number(res.quantity), status: res.status,
        counterparty: res.counterparty } });
      toast(`${t.trade_id} saved (version ${t.version + 1})`);
      render(page, id);
    } catch (e) { toast(e.message); }
  };

  const reserveCard = () => {
    const r = detail.reserve;
    if (!r) return h("div", { class: "card" }, h("h2", {}, "Reserve"), h("p", { class: "muted" }, "None: this trade was booked without the lines of a quote."));
    return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Reserve"),
      h("span", { class: "muted" }, `${num(100 * r.fraction, 1)} % left · ${r.method}`)),
      h("div", { class: "table-wrap" }, h("table", { class: "data nosort" },
        h("tr", {}, h("th", {}, "Line"), h("th", { class: "num" }, "Frozen at issue, % of notional"), h("th", { class: "num" }, "SE"),
          h("th", { class: "num" }, "At issue"), h("th", { class: "num" }, "Left")),
        r.lines.map((x) => h("tr", {}, h("td", {}, x.label), h("td", { class: "num" }, signed(x.cost, 3)), h("td", { class: "num" }, num(x.se, 3)),
          h("td", { class: "num" }, num(x.initial, 2)), h("td", { class: "num" }, num(x.amount, 2)))),
        h("tr", {}, h("td", {}, h("b", {}, "Total")), h("td", {}), h("td", {}), h("td", { class: "num" }, h("b", {}, num(r.initial, 2))),
          h("td", { class: "num" }, h("b", {}, num(r.amount, 2)))))));
  };

  const drawValue = () => clear(valueCard,
    h("div", { class: "card-head" }, h("h2", {}, "Value at today's market"),
      h("span", { class: "muted" }, pathsLabel())),
    h("div", { class: "btn-row", style: { justifyContent: "space-between" } },
      h("div", { class: "btn-row", style: { gap: "16px" } },
        segmented([["quick", "Quick"], ["standard", "Standard"], ["precise", "Precise"]], state.precision,
          (v) => { save({ precision: v }); drawValue(); }, "Precision"),
        switchControl("Delta and vega", greeks, (v) => { greeks = v; }, "t-greeks")),
      h("button", { class: "btn primary", type: "button", id: "value-btn", onclick: valueIt }, "Value this trade")),
    valueOut);

  async function valueIt() {
    const btn = document.getElementById("value-btn");
    btn.disabled = true;
    const started = performance.now();
    const status = progress("Valuing…");
    clear(valueOut, h("div", { style: { marginTop: "12px" } }, status));
    try {
      const { job } = await api(`/api/trades/${t.trade_id}/value`, { method: "POST",
        body: { options: { precision: state.precision, greeks, slv_vega: state.slvVega, sticky: state.sticky } } });
      const v = await runJob(job, (j) => {
        status.querySelector("p").textContent = `${pretty(j.state)} · ${num((performance.now() - started) / 1000, 1)} s`;
      });
      const q = v.quote;
      const g = q.greeks;
      clear(valueOut,
        h("div", { class: "grid cols-3", style: { marginTop: "12px" } },
          kpi("Price", `${pm(v.unit_price, v.unit_stderr)} %`, "of notional"),
          kpi("Value", pm(v.value, v.stderr, 2), `${num(t.quantity, 2)} × ${num(v.notional, 0)} notional`),
          kpi("Model", pretty(q.policy.model), `${pretty(q.policy.method)} · ${num(q.seconds, 1)} s`)),
        g ? h("div", { class: "grid cols-2", style: { marginTop: "12px" } },
          kpi(`Delta (sticky ${g.sticky})`, `${signed(g.delta, 4)} %`, `± ${num(g.delta_se, 4)} · per +1 % spot`),
          kpi("Vega", g.vega != null ? `${signed(g.vega, 4)} %` : "–",
            g.vega != null ? `± ${num(g.vega_se, 4)} · per vol point` : vegaNote(g.note))) : null,
        g && g.per_name ? h("div", { class: "table-wrap", style: { marginTop: "10px" } }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Name"), h("th", { class: "num" }, "Delta %"), h("th", { class: "num" }, "Vega %")),
          Object.entries(g.per_name).map(([n, x]) => h("tr", {}, h("td", {}, n), h("td", { class: "num" }, pm(x.delta, x.delta_se, 4)),
            h("td", { class: "num" }, pm(x.vega, x.vega_se, 4)))))) : null,
        h("p", { class: "muted", style: { marginTop: "10px" } },
          `Market ${v.snapshot_id}, valuation ${dateLabel(v.valuation)}. ${q.policy.reason}.`),
        q.warnings.map((w) => h("div", { class: "notice", style: { marginTop: "8px" } }, w)),
        v.basket ? h("div", { style: { marginTop: "12px" } }, basketCard(v.basket)) : null);
    } catch (e) {
      clear(valueOut, h("div", { style: { marginTop: "12px" } }, errorNotice(e)));
    } finally {
      btn.disabled = false;
    }
  }

  const items = [
    ...ev.past.map((e) => ({ date: e.date, cls: (KIND[e.kind] || ["", e.kind])[0], text: (KIND[e.kind] || ["", e.kind])[1],
      extra: e.amount != null ? `${num(e.amount, 3)} paid ${dateLabel(e.payment)}` : "" })),
    ...ev.upcoming.slice(0, 8).map((u, i) => ({ date: u.observation, cls: "future", text: i === 0 ? "Next observation" : "Observation",
      extra: u.payment ? `paid ${dateLabel(u.payment)}` : "" })),
  ];
  const s = t.summary || {};

  clear(page,
    h("div", { class: "page-head" },
      h("div", {}, h("p", {}, h("a", { href: "#/book" }, "Book"), " / ", t.trade_id),
        h("h1", {}, `${t.trade_id} `, badgeFor(t.status)),
        h("p", {}, sheet ? describeSheet(sheet) : "Script product")),
      h("div", { class: "btn-row" },
        h("button", { class: "btn", type: "button", onclick: edit }, "Edit"),
        sheet ? h("button", { class: "btn", type: "button", onclick: quoteCopy }, "Quote a copy") : null,
        detail.quote_id ? h("a", { class: "btn ghost", href: `#/pricer/quote/${detail.quote_id}` }, "Original quote") : null)),
    h("div", { class: "grid cols-4" },
      kpi("Quantity", num(t.quantity, 2), t.counterparty ? `with ${t.counterparty}` : `book ${t.book}`),
      kpi("Struck", dateLabel(ev.strike_date), `traded ${dateLabel(t.trade_date)}`),
      kpi("Matures", dateLabel(ev.maturity), (ev.underlyings || []).join(", ")),
      kpi("Coupon", s.coupon && s.coupon !== "none" && s.coupon_rate != null ? `${num(100 * s.coupon_rate, 3)} % p.a.` : "–",
        s.coupon ? pretty(s.coupon) : "")),
    h("div", { class: "grid cols-2", style: { marginTop: "16px" } },
      h("div", { class: "stack" },
        reserveCard(), valueCard,
        h("div", { class: "card" },
          h("div", { class: "card-head" }, h("h2", {}, "Events"), h("a", { href: `#/lifecycle/${t.trade_id}` }, "Fixings")),
          ev.missing ? h("div", { class: "notice", style: { marginBottom: "10px" } },
            `${ev.missing[0].toUpperCase()}${ev.missing.slice(1)}: add it in Lifecycle to see what has happened.`) : null,
          items.length ? h("ul", { class: "timeline" }, items.map((it) => h("li", {},
            h("span", { class: "when" }, dateLabel(it.date)), h("span", { class: `mark ${it.cls}` }),
            h("span", {}, h("b", {}, it.text), it.extra ? h("span", { class: "muted" }, ` · ${it.extra}`) : null))))
            : h("div", { class: "empty" }, "Nothing yet.")),
        h("div", { class: "card" }, h("h2", {}, "Versions"),
          h("div", { class: "table-wrap" }, h("table", { class: "data" },
            h("tr", {}, ["Version", "Saved", "Quantity", "Status", "Counterparty"].map((c, i) => h("th", { class: i === 2 ? "num" : "" }, c))),
            detail.versions.slice().reverse().map((v) => h("tr", {}, h("td", {}, `v${v.version}`),
              h("td", {}, v.recorded_at.replace("T", " ").slice(0, 16)), h("td", { class: "num" }, num(v.quantity, 2)),
              h("td", {}, badgeFor(v.status)), h("td", {}, v.counterparty || "–"))))))),
      h("div", { class: "card" }, h("h2", {}, "Term sheet"),
        detail.form ? detail.form.sections.map((sec) => h("div", { class: "sheet-section" },
          h("h3", {}, sec.label),
          h("dl", { class: "sheet" }, sec.fields.map((f) => [h("dt", {}, f.label), h("dd", {}, shown(f))]))))
          : h("pre", { class: "code" }, detail.script || ""))));
  drawValue();
}

function pathsLabel() {
  return state.precision === "quick" ? "20,000 paths" : state.precision === "precise" ? "200,000 paths" : "100,000 paths";
}

function shown(f) {
  if (f.value === null || f.value === undefined || f.value === "") return "–";
  if (f.read_only) return `× ${num(f.value, 3)}`;
  if (f.kind === "bool") return f.value ? "Yes" : "No";
  if (f.kind === "choice") return pretty(f.value);
  if (f.kind === "date") return dateLabel(f.value);
  const sfx = suffix(f);
  return `${display(f, f.value)}${sfx ? ` ${sfx}` : ""}`;
}

function kpi(label, value, sub) {
  return h("div", { class: "card tight kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    sub ? h("div", { class: "sub" }, sub) : null);
}
