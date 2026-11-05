// Overview: market, book and what comes next.

import { api, withToken } from "../api.js";
import { describeSheet } from "../format.js";
import { desk, state } from "../state.js";
import { badgeFor, clear, dateLabel, errorNotice, h, num, originBadge, pct, pm, pretty } from "../ui.js";

export async function render(page) {
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Overview"),
    h("p", {}, "The market behind every price, the book, and the dates coming up."))),
  h("div", { class: "skeleton", style: { height: "120px" } }));
  let ctx, trades, upcoming, quotes;
  try {
    [ctx, trades, upcoming, quotes] = await Promise.all([desk.context || api("/api/context"), api("/api/trades"),
      api("/api/upcoming?days=60"), api("/api/quotes")]);
  } catch (e) {
    clear(page, errorNotice(e));
    return;
  }
  desk.context = ctx;
  const byStatus = {};
  for (const t of trades.trades) byStatus[t.status] = (byStatus[t.status] || 0) + 1;
  const book = state.lastBook;

  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Overview"),
      h("p", {}, `Valuation date ${dateLabel(ctx.valuation)} · market ${ctx.snapshot_id} · surface `, originBadge(ctx.origin && ctx.origin.surface))),
      h("div", { class: "btn-row" }, h("a", { class: "btn primary", href: "#/pricer" }, "New quote"),
        h("a", { class: "btn", href: "#/book" }, "Open the book"),
        h("a", { class: "btn ghost", href: withToken("/report/desk"), target: "_blank", rel: "noopener",
          title: "The whole desk on one page that opens anywhere: market, book, events, quotes" }, "Desk snapshot"))),
    h("div", { class: "grid cols-4" },
      kpi("Spot (model scale)", num(ctx.spot, 2), `${ctx.underlying}, rescaled to 100`),
      kpi("ATM vol 1 year", pct(ctx.atm_vols["1"], 2), `3M ${pct(ctx.atm_vols["0.25"], 1)} · 2Y ${pct(ctx.atm_vols["2"], 1)}`),
      kpi("Zero rate 1 year", pct(ctx.zero_rates["1"], 2), `carry ${pct(ctx.carry["1"], 2)}`),
      kpi("Calibrated up to", `${num(ctx.t_max, 2)} y`, `SLV mix ${num(ctx.slv_mix, 2)}`)),
    h("div", { class: "grid cols-2", style: { marginTop: "16px" } },
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Book"), h("a", { href: "#/book" }, "Details")),
        trades.trades.length
          ? [h("div", { class: "btn-row" }, Object.entries(byStatus).map(([s, n]) => h("span", {}, badgeFor(s), ` ${n}`))),
            book ? h("div", { class: "kpi", style: { marginTop: "12px" } }, h("div", { class: "label" }, "Last valuation"),
              h("div", { class: "value" }, pm(book.value, book.stderr, 2)),
              h("div", { class: "sub" }, `${book.positions.length} position${book.positions.length > 1 ? "s" : ""} · ${pretty(book.model)} · ${book.when || ""}`))
              : h("p", { class: "muted", style: { marginTop: "12px" } }, "The book has not been priced yet.")]
          : h("div", { class: "empty" }, "No trade yet: quote a product and book it.")),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Next 60 days"), h("a", { href: "#/lifecycle" }, "Lifecycle")),
        upcoming.events.length
          ? h("ul", { class: "timeline" }, upcoming.events.slice(0, 10).map((e) => h("li", {},
            h("span", { class: "when" }, dateLabel(e.observation)), h("span", { class: `mark ${e.final ? "accent" : "future"}` }),
            h("span", {}, h("b", {}, e.trade_id), ` ${e.final ? "final observation" : "observation"}`,
              e.payment ? h("span", { class: "muted" }, ` · paid ${dateLabel(e.payment)}`) : null))))
          : h("div", { class: "empty" }, "No observation of a live trade in the next 60 days."))),
    h("div", { class: "card", style: { marginTop: "16px" } },
      h("div", { class: "card-head" }, h("h2", {}, "Recent quotes")),
      quotes.quotes.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "When"), h("th", {}, "Product"), h("th", { class: "num" }, "Result"), h("th", {}, "")),
          quotes.quotes.map((q) => h("tr", {},
            h("td", {}, q.created_at.replace("T", " ").slice(0, 16)),
            h("td", {}, describeSheet(q.sheet)),
            h("td", { class: "num" }, q.headline ? `${pm(q.headline.value, q.headline.se)} ${q.headline.unit}` : "–"),
            h("td", { class: "num" }, h("a", { href: `#/pricer/quote/${q.quote_id}` }, "Open"))))))
        : h("div", { class: "empty" }, "No quote yet.")));
}

function kpi(label, value, sub) {
  return h("div", { class: "card kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    h("div", { class: "sub" }, sub));
}
