// Script lab: any product the language can write, checked as you type, priced under a chosen model.

import { api, runJob } from "../api.js";
import { clear, errorNotice, field, h, int, num, pm, progress } from "../ui.js";

const EXAMPLE = `underlying SPX
param N = 100
param K = 1.0
param B = 0.7

event 2026-10-27
  S0 = SPX
end

event 2027-10-27 pay 2027-11-03
  perf = SPX / S0
  if lowest(SPX, 2026-10-27, 2027-10-27) < B * S0 then
    pay N - N * max(K - perf, 0) as redemption
  else
    pay N + N * 0.5 * max(perf - 1, 0) as redemption
  end
end
`;

export async function render(page) {
  const KEY = "optlab.script";
  let text;
  try { text = localStorage.getItem(KEY) || EXAMPLE; } catch (e) { text = EXAMPLE; }
  const gutter = h("div", { class: "gutter", "aria-hidden": "true" });
  const area = h("textarea", { spellcheck: "false", "aria-label": "Script", wrap: "off" });
  area.value = text;
  const status = h("div", { "aria-live": "polite" });
  const out = h("div", { "aria-live": "polite" });
  let model = "lv", paths = 40000;

  const lines = (bad) => {
    const n = area.value.split("\n").length;
    clear(gutter, Array.from({ length: n }, (_, i) => h("span", { class: i + 1 === bad ? "bad" : "" }, `${i + 1}\n`)));
  };
  area.addEventListener("scroll", () => { gutter.scrollTop = area.scrollTop; });
  area.addEventListener("keydown", (e) => {
    if (e.key === "Tab") { e.preventDefault(); const s = area.selectionStart; area.setRangeText("  ", s, area.selectionEnd, "end"); }
  });

  let timer = null;
  const check = async () => {
    try { localStorage.setItem(KEY, area.value); } catch (e) { /* ignore */ }
    const r = await api("/api/script/check", { method: "POST", body: { script: area.value } }).catch((e) => ({ ok: false, message: e.message }));
    lines(r.ok ? 0 : r.line);
    clear(status, r.ok
      ? h("div", { class: "notice info" }, `Valid: ${r.underlyings.join(", ")} · ${r.events} events · params ${Object.keys(r.params).join(", ") || "none"}`)
      : h("div", { class: "notice err" }, r.line ? `Line ${r.line}, column ${r.col}: ${r.message}` : r.message));
    return r.ok;
  };
  area.addEventListener("input", () => { lines(0); clearTimeout(timer); timer = setTimeout(check, 350); });

  const priceIt = async () => {
    if (!(await check())) return;
    clear(out, progress("Pricing…"));
    try {
      const { job } = await api("/api/script/price", { method: "POST", body: { script: area.value, model, n_paths: paths } });
      const r = await runJob(job);
      clear(out, h("div", { class: "card kpi" }, h("div", { class: "label" }, `${model.toUpperCase()} Monte Carlo, ${int(paths)} paths`),
        h("div", { class: "value" }, `${pm(r.price, r.stderr)} %`),
        h("div", { class: "sub" }, `of notional ${num(r.notional, 0)} · expected life ${num(r.expected_life, 2)} y · ${num(r.seconds, 1)} s`),
        r.warnings.map((w) => h("div", { class: "notice", style: { marginTop: "8px" } }, w))));
    } catch (e) { clear(out, errorNotice(e)); }
  };

  clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Script lab"),
      h("p", {}, "Write a payoff in the script language: errors show the line and column; price it under any model."))),
    h("div", { class: "pricer" },
      h("div", { class: "stack" }, h("div", { class: "editor" }, gutter, area), status),
      h("div", { class: "stack" },
        h("div", { class: "card" }, h("h2", {}, "Price"),
          h("div", { class: "fields" },
            field("Model", h("select", { onchange: (e) => { model = e.target.value; } },
              [["lv", "Local vol"], ["slv", "SLV"], ["heston", "Heston"], ["bs", "Black-Scholes"]].map(([v, t]) => h("option", { value: v }, t)))),
            field("Paths", h("select", { onchange: (e) => { paths = Number(e.target.value); } },
              [20000, 40000, 100000, 200000].map((n) => h("option", { value: n, selected: n === 40000 || undefined }, int(n)))))),
          h("div", { class: "btn-row", style: { marginTop: "12px" } },
            h("button", { class: "btn primary", type: "button", onclick: priceIt }, "Price the script"),
            h("button", { class: "btn ghost", type: "button", onclick: () => { area.value = EXAMPLE; lines(0); check(); } }, "Reset the example"))),
        out,
        h("div", { class: "card" }, h("h2", {}, "Language"),
          h("pre", { class: "code" }, "underlying NAME · param NAME = number\nevent DATE [pay DATE] … end\n"
            + "x = expr · pay expr as coupon|upside|redemption · terminate\nif cond then … [else …] end\n"
            + "min max abs exp log worst best · lowest(NAME, DATE, DATE) highest(…)\n< <= > >= == and or not")))));
  lines(0);
  check();
}
