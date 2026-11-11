// Risk: Greeks per trade and for the book, the vega matrix and tenor buckets, the spot ladder, scenarios and the VaR.

import { api, runJob, withToken } from "../api.js";
import { barChart, legend, lineChart } from "../charts.js";
import { save, state } from "../state.js";
import { clear, dialog, errorNotice, field, h, int, isNum, num, originBadge, pretty, progress, segmented, signed, switchControl,
  toast } from "../ui.js";

const TENORS = ["1m", "3m", "6m", "1y", "18m", "2y", "3y", "5y"];
const STRIKES = ["60%", "70%", "80%", "90%", "95%", "100%", "105%", "110%", "120%", "140%"];
const RULES = { sticky_moneyness: "Sticky moneyness", sticky_strike: "Sticky strike", realistic: "Realistic (betas)" };

// A Greek with its error; within twice its error it is shown faint and marked.
function g(v, d = 0) {
  if (!v || !isNum(v[0])) return "–";
  const [x, se] = v;
  const noisy = isNum(se) && se > 0 && Math.abs(x) < 2 * se;
  const text = isNum(se) && se > 0 ? `${signed(x, d)} ± ${num(se, d)}` : signed(x, d);
  return noisy ? h("span", { class: "faint", title: "within twice its standard error: in the noise" }, text, " ~") : text;
}

function namesOf(book) {
  const out = [];
  for (const k of Object.keys(book)) {
    const [greek, name] = k.split("|");
    if (greek === "delta" && name && name !== "*" && !out.includes(name)) out.push(name);
  }
  return out;
}

function card(title, unit, ...body) {
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, title), unit ? h("span", { class: "muted" }, unit) : null),
    ...body);
}

function table(head, rows, numFrom = 1) {
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("tr", {}, head.map((c, i) => h("th", { class: i >= numFrom ? "num" : "" }, c))),
    rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: i >= numFrom ? "num" : "" }, c))))));
}

// Background of a heatmap cell: a share of the positive or negative colour, by size against the largest cell.
function heat(v, vmax) {
  if (!isNum(v) || !vmax) return {};
  const share = Math.round(70 * Math.min(1, Math.abs(v) / vmax));
  return { background: `color-mix(in srgb, var(${v >= 0 ? "--pos" : "--neg"}) ${share}%, transparent)` };
}

function greeksCards(run) {
  const book = run.book;
  const names = namesOf(book);
  const cur = (Object.keys(book).find((k) => k.startsWith("rho|")) || "rho|USD|").split("|")[1];
  const meta = run.meta || {};
  const origin = meta.origin || {};
  const groups = run.groups || [];
  const out = [];
  out.push(h("div", { class: "grid cols-3" },
    kpi("Book value", g(book["value||"], 0), `currency, desk side · ${groups.map((x) => `${x.label}: ${pretty(x.method)} (${pretty(x.model)})`).join(" · ")}`),
    kpi("Theta", g(book["theta|*|"], 0), "per business day, market frozen"),
    kpi("Run", `${num(run.seconds, 0)} s`, `${int(run.settings.n_paths)} paths · ${groups.reduce((a, x) => a + x.simulations, 0)} simulations`)));
  out.push(h("p", { class: "muted" }, `Market ${meta.snapshot_id || "–"}, valuation ${meta.valuation || "–"} · surface `,
    originBadge(origin.surface || "unknown"), " · digitals smoothed over 0.5 % for the Greeks, bias on the price ",
    groups.map((x) => x.smoothing_bias ? `${x.label} ${signed(x.smoothing_bias[0], 0)} ± ${num(x.smoothing_bias[1], 0)}` : null).filter(Boolean).join(", ") || "none", "."));
  out.push(card("Greeks by underlying", "currency · delta per +1 % spot · gamma per +1 % per +1 % · vega per vol point",
    table(["Underlying", "Delta", "Delta sticky strike", "Delta realistic", "Gamma 1 %", "Gamma 5 %", "Vega", "Vega +1 % relative", "Vanna", "Volga", "Skew vega", "Epsilon / bp"],
      names.map((n) => [n, g(book[`delta|${n}|`]), g(book[`delta_sticky_strike|${n}|`]), g(book[`delta_realistic|${n}|`]),
        g(book[`gamma|${n}|`]), g(book[`gamma_5|${n}|`]), g(book[`vega|${n}|`]), g(book[`vega_relative|${n}|`]), g(book[`vanna|${n}|`]),
        g(book[`volga|${n}|`]), g(book[`skew_vega|${n}|`]), g(book[`epsilon|${n}|`], 1)])),
    h("p", { class: "muted", style: { marginTop: "8px" } }, `Rho ${cur} `, g(book[`rho|${cur}|`], 1), " per bp (discount ",
      g(book[`rho_discount|${cur}|`], 1), ", forward ", g(book[`rho_forward|${cur}|`], 1), ") · correlation ", g(book["corr|*|"]),
      " per point · delta in units of the underlying (spot 100): ", names.map((n) => `${n} ${num((book[`delta_units|${n}|`] || [0])[0], 0)}`).join(", "))));
  const vega = names.map((n) => {
    const tenors = TENORS.map((t) => book[`vega_tenor|${n}|${t}`]);
    const net = book[`vega_net|${n}|`];
    const gross = run.gross[`vega_node_gross|${n}|`] ?? run.gross[`vega_tenor_gross|${n}|`];
    return [n, ...tenors.map((v) => g(v)), g(net), isNum(gross) ? num(gross, 0) : "–", g(book[`vega_weighted|${n}|`]), g(book[`vega_weighted_direct|${n}|`])];
  });
  out.push(card("Vega by tenor", "currency per vol point · net, gross, weighted (× √(1 y / T))",
    table(["Underlying", ...TENORS, "Net", "Gross", "Weighted", "Weighted, one shock"], vega)));
  const curves = [[`Rho ${cur}`, TENORS.map((t) => book[`rho_tenor|${cur}|${t}`])],
    ...names.map((n) => [`Epsilon ${n}`, TENORS.map((t) => book[`epsilon_tenor|${n}|${t}`])])];
  out.push(card("Rates and dividends by tenor", "currency per +1 bp", table(["", ...TENORS], curves.map(([l, vs]) => [l, ...vs.map((v) => g(v, 1))]))));
  for (const n of names) {
    const cells = STRIKES.map((k) => TENORS.map((t) => book[`vega_node|${n}|${k}@${t}`]));
    if (!cells.flat().some(Boolean)) continue;
    const vmax = Math.max(...cells.flat().map((v) => (v ? Math.abs(v[0]) : 0)));
    out.push(card(`Vega matrix, ${n}`, "currency per vol point on one node · strikes in % of the forward",
      h("div", { class: "table-wrap" }, h("table", { class: "data heat" },
        h("tr", {}, h("th", {}, "Strike"), TENORS.map((t) => h("th", { class: "num" }, t))),
        STRIKES.map((k, i) => h("tr", {}, h("td", {}, k), cells[i].map((v) => h("td", { class: "num", style: heat(v && v[0], vmax),
          title: v ? `${k} @ ${TENORS[cells[i].indexOf(v)]}: ${signed(v[0], 0)} ± ${num(v[1], 0)}` : "" }, v ? num(v[0], 0) : "–")))))),
      h("p", { class: "muted", style: { marginTop: "8px" } }, "Sum of the nodes ", g(book[`vega_net|${n}|`]), " against the parallel vega ",
        g(book[`vega|${n}|`]), ": the gap is the bumps' nonlinearity (one-sided nodes, a central parallel bump).")));
  }
  out.push(card("Greeks by trade", "currency · all names together",
    table(["Trade", "Group", "Value", "Delta", "Gamma 5 %", "Vega", "Theta", "Rho", "Noise"],
      run.positions.map((p) => [h("a", { href: `#/trade/${p.name}` }, h("b", {}, p.name)), pretty(p.group), g([p.value, p.value_se]),
        g(p.greeks["delta|*|"] || p.greeks[`delta|${names[0]}|`]), g(p.greeks["gamma_5|*|"] || p.greeks[`gamma_5|${names[0]}|`]),
        g(p.greeks["vega|*|"] || p.greeks[`vega|${names[0]}|`]), g(p.greeks["theta|*|"]), g(p.greeks[`rho|${cur}|`], 1),
        p.noise.length ? h("span", { class: "faint" }, `${p.noise.length} in the noise`) : ""]), 2)));
  return out;
}

function ladderCard(run) {
  const pts = run.points;
  const series = run.rules.map((r) => ({ name: RULES[r] || r, markers: true, points: pts.map((p) => [100 * p, run.book[`pnl@${r}@${fmtP(p)}`][0]]) }));
  const rows = [];
  for (const r of run.rules) {
    for (const what of ["pnl", "delta", "gamma", "vega"]) {
      rows.push([`${RULES[r] || r} · ${what === "pnl" ? "P&L" : what}`, ...pts.map((p) => g(run.book[`${what}@${r}@${fmtP(p)}`]))]);
    }
  }
  const tenors = run.rules.map((r) => [RULES[r] || r, ...pts.map((p) => {
    const vs = TENORS.map((t) => run.book[`vega_tenor:${t}@${r}@${fmtP(p)}`]).filter(Boolean);
    return vs.length ? num(Math.max(...vs.map((v) => Math.abs(v[0]))), 0) : "–";
  })]);
  return card("Spot ladder", "every spot moved together · currency, desk side",
    lineChart(series, { xFormat: (v) => `${v.toFixed(0)} %`, yFormat: (v) => v.toFixed(0), xLabel: "spot move", label: "Book P&L along the spot ladder" }),
    legend(series), table(["", ...pts.map((p) => `${signed(100 * p, 0)} %`)], rows),
    h("p", { class: "muted", style: { marginTop: "8px" } }, "Largest vega of one tenor at each point:"),
    table(["", ...pts.map((p) => `${signed(100 * p, 0)} %`)], tenors),
    run.note ? h("div", { class: "notice" }, run.note) : null,
    (run.groups || []).flatMap((x) => x.warnings).map((w) => h("div", { class: "notice", style: { marginTop: "8px" } }, w)));
}

const fmtP = (p) => (p >= 0 ? "+" : "-") + Math.abs(p).toFixed(4);

function scenarioCards(run, set, onEdit) {
  const out = [card("Scenarios", set ? (set.starting ? "starting values, not signed yet" : `version ${set.version}, signed by ${set.signed_by}`) : "",
    table(["Scenario", "Moves", "Label", "Book P&L", "Surfaces"], run ? run.scenarios.map((s) => [h("b", {}, s.name), s.description, s.label || "",
      g(s.book), s.arbitrage.length ? h("span", { class: "badge neg" }, `arbitrage: ${s.arbitrage.join(", ")}`) : h("span", { class: "faint" }, "clean")]) :
      (set ? set.scenarios.map((s) => [h("b", {}, s.name), s.description, s.label, "not run", ""]) : []), 3),
    h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("button", { class: "btn", type: "button", onclick: onEdit }, "Edit and sign")))];
  if (run && run.grid) {
    const gr = run.grid;
    const vmax = Math.max(...gr.pnl.flat().map((c) => Math.abs(c[0])));
    out.push(card("Spot × vol", "book P&L, currency · vol moves in points, sticky moneyness",
      h("div", { class: "table-wrap" }, h("table", { class: "data heat" },
        h("tr", {}, h("th", {}, "Spot \\ vol"), gr.vols.map((v) => h("th", { class: "num" }, `${signed(100 * v, 0)}`))),
        gr.spots.map((s, i) => h("tr", {}, h("td", {}, `${signed(100 * s, 0)} %`), gr.pnl[i].map((c) => h("td", { class: "num", style: heat(c[0], vmax),
          title: `${num(c[0], 0)} ± ${num(c[1], 0)}` }, num(c[0], 0)))))))));
  }
  return out;
}

function varCards(run) {
  const rows = [["one_day", "1 day"], ["ten_day", "10 days, overlapping"], ["stressed", "Stressed, 1 day"]].filter(([k]) => run[k]).map(([k, label]) => {
    const r = run[k];
    return [label, `${r.start} → ${r.end}`, num(r.var, 0), num(r.var_se, 0), num(r.es, 0), r.var_date, r.taylor ? num(r.taylor.var, 0) : "–", int(r.n)];
  });
  const one = run.one_day;
  const lo = Math.min(...one.pnl), hi = Math.max(...one.pnl);
  const bins = 30, w = (hi - lo) / bins || 1;
  const counts = Array.from({ length: bins }, (_, i) => ({ label: num(lo + (i + 0.5) * w, 0), value: 0 }));
  for (const x of one.pnl) counts[Math.min(bins - 1, Math.floor((x - lo) / w))].value += 1;
  const vol = Object.entries(run.origins || {}).filter(([k]) => k.startsWith("vol:"));
  const contrib = Object.keys(one.contributions.es).map((p) => [p, signed(one.contributions.var[p], 0), signed(one.contributions.es[p], 0)]);
  const factors = Object.entries(one.factors).map(([f, v]) => [pretty(f), v && isNum(v[0]) ? g(v) : "–"]);
  const st = run.stressed;
  return [card("Historical VaR, full revaluation", "99 % VaR and 97.5 % ES of the book's P&L, a loss as a positive number, currency",
    table(["Horizon", "Window", "VaR", "SE of the VaR scenario", "ES", "VaR day", "Delta-gamma-vega VaR", "Scenarios"], rows, 2),
    h("p", { class: "muted", style: { marginTop: "8px" } }, "Vols from surfaces ", vol.length ? vol.map(([k, o]) => [k.slice(4), " ", o.map(originBadge)]) : "–",
      " · the 10-day VaR uses overlapping 10-day moves, not √10 × the 1-day VaR",
      st ? ` · stressed window ${st.window.start} → ${st.window.end}, found by the ${st.window.method}, stored (version ${st.window.version ?? "–"})` : "", "."),
    barChart(counts, { height: 180, label: "Distribution of the 1-day P&L", yFormat: (v) => v.toFixed(0) }),
    h("p", { class: "muted" }, `Distribution of the ${int(one.n)} one-day P&Ls (scenarios per bin).`)),
  card("VaR contributions", "currency · on the VaR day, and the mean over the ES tail (adds up to the ES)",
    table(["Trade", "On the VaR day", "Over the ES tail"], contrib),
    h("p", { class: "muted", style: { marginTop: "8px" } }, `The VaR day (${one.var_date}) one factor at a time:`), table(["Factor", "P&L"], factors))];
}

function kpi(label, value, sub) {
  return h("div", { class: "card tight kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), h("div", { class: "sub" }, sub));
}

export async function render(page) {
  const controls = h("div", { class: "card" });
  const out = { greeks: h("div", { class: "stack" }), ladder: h("div", { class: "stack" }), scen: h("div", { class: "stack" }), var: h("div", { class: "stack" }) };
  let tab = state.riskTab || "greeks";
  let kxt = state.riskKxt ?? true;
  let precision = state.riskPrecision || "standard";
  let last = { risk: null, ladder: null, scenarios: null, var: null };
  let set = null;
  const tabs = h("div", {});
  clear(page, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Risk"),
    h("p", {}, "Every Greek, ladder, scenario and VaR is a list of shocks revalued in full on the base's random numbers.")),
    h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/pnl" }, "P&L explained"),
      h("a", { class: "btn", href: withToken("/report/risk"), target: "_blank", rel: "noopener" }, "Risk report"))),
  h("div", { class: "stack" }, controls, tabs));

  const show = () => {
    clear(tabs, segmented([["greeks", "Greeks"], ["ladder", "Ladder"], ["scen", "Scenarios"], ["var", "VaR"]], tab, (v) => { tab = v; save({ riskTab: v }); show(); }, "Section"),
      h("div", { style: { marginTop: "12px" } }, out[tab]));
  };
  const drawControls = () => clear(controls, h("div", { class: "card-head" }, h("h2", {}, "Run"), h("span", { class: "muted" }, "the live book, on the market in use")),
    h("div", { class: "btn-row", style: { justifyContent: "space-between", flexWrap: "wrap", gap: "12px" } },
      h("div", { class: "btn-row", style: { gap: "16px", flexWrap: "wrap" } },
        segmented([["standard", "Standard"], ["max", "Max"]], precision, (v) => { precision = v; save({ riskPrecision: v }); drawControls(); }, "Precision"),
        switchControl("Vega matrix (single names)", kxt, (v) => { kxt = v; save({ riskKxt: v }); }, "r-kxt")),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", type: "button", onclick: () => go("greeks", "/api/risk/run", { options: { precision, kxt: kxt ? "single" : "none" } }) }, "Greeks"),
        h("button", { class: "btn", type: "button", onclick: () => go("ladder", "/api/risk/ladder", { options: { precision } }) }, "Ladder"),
        h("button", { class: "btn", type: "button", onclick: () => go("scen", "/api/risk/scenarios/run", { options: { precision } }) }, "Scenarios"),
        h("button", { class: "btn", type: "button", onclick: () => go("var", "/api/risk/var", { options: { precision: "standard" } }) }, "VaR"))));

  async function go(which, path, body) {
    tab = which; show();
    const started = performance.now();
    const status = progress("Running…");
    clear(out[which], h("div", { class: "card" }, status));
    try {
      const { job } = await api(path, { method: "POST", body });
      const res = await runJob(job, (j) => { status.querySelector("p").textContent = `${j.state} · ${num((performance.now() - started) / 1000, 0)} s`; });
      if (which === "greeks") last.risk = res;
      if (which === "ladder") last.ladder = res;
      if (which === "scen") last.scenarios = res;
      if (which === "var") last.var = res;
      draw();
      toast(`Stored as ${res.run_id}`);
    } catch (e) { clear(out[which], errorNotice(e)); }
  }

  async function edit() {
    const text = h("textarea", { name: "scenarios", rows: 16, style: { width: "100%", fontFamily: "var(--mono)", fontSize: "12px" } },
      JSON.stringify(set.scenarios, null, 1));
    const res = await dialog({ title: "Scenarios", ok: "Save a signed version",
      body: [h("p", { class: "muted" }, "Spots relative (-0.2 = -20 %), vols and skew in fractions (0.01 = 1 point), rates and dividends in bp, dividend_relative a share of the flows, corr absolute. rule: sticky_moneyness, sticky_strike or realistic."),
        field("Scenarios (JSON)", text, { wide: true }), field("Signed by", h("input", { name: "signed_by", type: "text", required: true })),
        field("Note", h("input", { name: "note", type: "text" }))],
      read: (form) => Object.fromEntries(new FormData(form)) });
    if (!res) return;
    try {
      const scenarios = JSON.parse(res.scenarios);
      const r = await api("/api/risk/scenarios", { method: "PUT", body: { scenarios, signed_by: res.signed_by, note: res.note } });
      toast(`Scenarios saved, version ${r.version}`);
      set = await api("/api/risk/scenarios");
      draw();
    } catch (e) { toast(e.message); }
  }

  function draw() {
    clear(out.greeks, ...(last.risk ? greeksCards(last.risk) : [h("div", { class: "empty" }, "No Greeks yet: run them above.")]));
    clear(out.ladder, last.ladder ? ladderCard(last.ladder) : h("div", { class: "empty" }, "No ladder yet."));
    clear(out.scen, ...scenarioCards(last.scenarios, set, edit));
    clear(out.var, ...(last.var ? varCards(last.var) : [h("div", { class: "empty" }, "No VaR yet: it takes minutes (a full revaluation per day of history).")]));
  }

  drawControls();
  show();
  try {
    set = await api("/api/risk/scenarios");
    const { runs } = await api("/api/risk/runs");
    for (const [kind, key] of [["risk", "risk"], ["ladder", "ladder"], ["scenarios", "scenarios"], ["var", "var"]]) {
      const r = runs.find((x) => x.kind === kind);
      if (r) last[key] = await api(`/api/risk/runs/${r.run_id}`);
    }
  } catch (e) { clear(out.greeks, errorNotice(e)); }
  draw();
}
