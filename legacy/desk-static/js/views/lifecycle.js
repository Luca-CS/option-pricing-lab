// Lifecycle: fixings in, events out.

import { api } from "../api.js";
import { describeSheet } from "../format.js";
import { desk } from "../state.js";
import { clear, dateLabel, dialog, errorNotice, field, h, num, switchControl, toast } from "../ui.js";

const KIND = {
  coupon_paid: ["pos", "Coupon paid"], coupon_missed: ["neg", "Coupon missed"], called: ["accent", "Called"],
  knocked_in: ["neg", "Knocked in"], matured: ["accent", "Matured"],
};

export async function render(page, arg) {
  const fixCard = h("div", { class: "card" });
  const tradeCard = h("div", { class: "card" });
  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Lifecycle"),
      h("p", {}, "Enter fixings as they come; each trade shows what happened and what is next."))),
    h("div", { class: "grid cols-2" }, fixCard, tradeCard));

  let trades = [];
  let selected = arg || null;
  let underlying = "SPX";

  const loadFixings = async () => {
    try {
      const all = (await api("/api/fixings")).fixings;
      drawFixings(all[underlying] || {});
    } catch (e) { clear(fixCard, errorNotice(e)); }
  };

  const drawFixings = (levels) => {
    const rows = Object.entries(levels).sort((a, b) => (a[0] < b[0] ? 1 : -1));
    clear(fixCard,
      h("div", { class: "card-head" }, h("h2", {}, "Fixings"),
        h("div", { class: "btn-row" },
          h("select", { "aria-label": "Underlying", onchange: (e) => { underlying = e.target.value; loadFixings(); } },
            names().map((n) => h("option", { value: n, selected: n === underlying || undefined }, n))),
          h("button", { class: "btn primary sm", type: "button", onclick: addFixings }, "Add fixings"))),
      h("p", { class: "muted" }, "Levels in the model's scale (spot rescaled to 100 on the valuation date), or real index levels "
        + "converted with today's real spot."),
      rows.length ? h("div", { class: "table-wrap", style: { maxHeight: "420px", overflowY: "auto" } }, h("table", { class: "data" },
        h("tr", {}, h("th", {}, "Date"), h("th", { class: "num" }, "Level"), h("th", {}, "")),
        rows.map(([d, v]) => h("tr", {}, h("td", {}, dateLabel(d)), h("td", { class: "num" }, num(v, 4)),
          h("td", { class: "num" }, h("button", { class: "btn sm ghost danger", type: "button", onclick: async () => {
            await api(`/api/fixings/${underlying}/${d}`, { method: "DELETE" });
            loadFixings();
          } }, "Remove"))))))
        : h("div", { class: "empty" }, `No fixing of ${underlying} yet.`));
  };

  let basketNames = [];
  const names = () => {
    const set = new Set([desk.context ? desk.context.underlying : "SPX", ...basketNames]);
    for (const t of trades) for (const n of (t.summary && t.summary.underlyings) || []) set.add(n);
    return [...set];
  };

  async function addFixings() {
    let real = false;
    const area = h("textarea", { name: "lines", rows: "8", placeholder: "2026-07-27 104.2\n2026-10-27 98.7" });
    const file = h("input", { type: "file", accept: ".csv,.txt,text/csv", "aria-label": "CSV file",
      onchange: async (e) => { const f = e.target.files[0]; if (f) area.value = await f.text(); } });
    const res = await dialog({ title: `Add fixings of ${underlying}`, ok: "Save",
      body: [field("One fixing per line: date and level", area,
        { hint: "Space, comma, semicolon or tab between them; a pasted column pair or a CSV with a header works "
          + "(its Close column is taken, as in a Yahoo export). Dates as 2026-07-27 or 27/07/2026." }),
        field("Or a CSV file", file),
        switchControl("These are real index levels", false, (v) => { real = v; }, "fx-real"),
        field("Real spot on the valuation date (for real levels)", h("input", { type: "number", name: "real_spot", step: "any",
          placeholder: "e.g. 5812.4" }), { hint: desk.context ? `Model spot ${desk.context.spot} on ${desk.context.valuation}` : "" })],
      read: (form) => Object.fromEntries(new FormData(form)) });
    if (!res) return;
    try {  // the server parses the text (lines, columns or a CSV export): one parser, under the tests
      const r = await api("/api/fixings", { method: "POST", body: { underlying, text: res.lines,
        real_spot: real ? Number(res.real_spot) || null : null } });
      toast(`${r.stored} fixing${r.stored > 1 ? "s" : ""} saved`
        + (r.skipped.length ? `; skipped (not "date level"): ${r.skipped.slice(0, 3).join(" | ")}` : ""), { timeout: 8000 });
      loadFixings();
      if (selected) drawTrade(selected);
    } catch (e) { toast(e.message); }
  }

  const drawTrades = async () => {
    try { trades = (await api("/api/trades")).trades; } catch (e) { clear(tradeCard, errorNotice(e)); return; }
    if (!selected && trades.length) selected = trades[0].trade_id;
    drawTrade(selected);
  };

  async function drawTrade(id) {
    const picker = h("select", { "aria-label": "Trade", onchange: (e) => { selected = e.target.value; drawTrade(selected); } },
      trades.map((t) => h("option", { value: t.trade_id, selected: t.trade_id === id || undefined },
        `${t.trade_id} · ${t.summary ? describeSheet(t.product.term_sheet) : "script"}`)));
    if (!trades.length) {
      clear(tradeCard, h("div", { class: "card-head" }, h("h2", {}, "Trade")), h("div", { class: "empty" }, "No trade in the book."));
      return;
    }
    clear(tradeCard, h("div", { class: "card-head" }, h("h2", {}, "Trade"), picker), h("div", { class: "skeleton", style: { height: "160px" } }));
    try {
      const ev = await api(`/api/trades/${id}/events`);
      const items = [
        ...ev.past.map((e) => ({ date: e.date, cls: (KIND[e.kind] || ["", e.kind])[0], text: (KIND[e.kind] || ["", e.kind])[1],
          extra: e.amount != null ? `${num(e.amount, 3)} paid ${dateLabel(e.payment)}` : "" })),
        ...ev.upcoming.slice(0, 12).map((u, i) => ({ date: u.observation, cls: "future", text: i === 0 ? "Next observation" : "Observation",
          extra: u.payment ? `paid ${dateLabel(u.payment)}` : "" })),
      ];
      clear(tradeCard,
        h("div", { class: "card-head" }, h("h2", {}, "Trade"), picker),
        h("p", { class: "muted" }, `Struck ${dateLabel(ev.strike_date)} · matures ${dateLabel(ev.maturity)}`),
        ev.missing ? h("div", { class: "notice", style: { marginBottom: "10px" } },
          `${ev.missing[0].toUpperCase()}${ev.missing.slice(1)}: add it under Fixings to see what has happened.`) : null,
        items.length ? h("ul", { class: "timeline" }, items.map((it) => h("li", {},
          h("span", { class: "when" }, dateLabel(it.date)), h("span", { class: `mark ${it.cls}` }),
          h("span", {}, h("b", {}, it.text), it.extra ? h("span", { class: "muted" }, ` · ${it.extra}`) : null))))
          : h("div", { class: "empty" }, "Nothing yet."));
    } catch (e) { clear(tradeCard, h("div", { class: "card-head" }, h("h2", {}, "Trade"), picker), errorNotice(e)); }
  }

  try { basketNames = Object.keys((await api("/api/basket")).names); } catch (e) { /* the list keeps the others */ }
  await drawTrades();
  await loadFixings();
}
