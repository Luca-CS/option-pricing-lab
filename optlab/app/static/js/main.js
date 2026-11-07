// Shell: router, status bar, theme, keyboard shortcuts.

import { api } from "./api.js";
import { clear, dialog, h, sortable } from "./ui.js";
import { applyTheme, desk, save, state } from "./state.js";

const VIEWS = {
  overview: () => import("./views/overview.js"),
  pricer: () => import("./views/pricer.js"),
  book: () => import("./views/book.js"),
  risk: () => import("./views/risk.js"),
  pnl: () => import("./views/pnl.js"),
  trade: () => import("./views/trade.js"),
  lifecycle: () => import("./views/lifecycle.js"),
  market: () => import("./views/market.js"),
  costs: () => import("./views/costs.js"),
  script: () => import("./views/script.js"),
  vollab: () => import("./views/vollab.js"),
  replay: () => import("./views/replay.js"),
  data: () => import("./views/data.js"),
  settings: () => import("./views/settings.js"),
};
const TITLES = { overview: "Overview", pricer: "Pricer", book: "Book", risk: "Risk", pnl: "P&L explained", trade: "Trade", lifecycle: "Lifecycle",
  costs: "Cost defaults", market: "Market", script: "Script lab", vollab: "Vol lab", replay: "Replay", data: "Data", settings: "Settings" };
const SECTION = { trade: "book" };  // pages without their own entry in the navigation

const main = document.getElementById("main");
let cleanup = null;

// Every data table a screen inserts becomes sortable by its headers.
new MutationObserver((changes) => {
  for (const change of changes) {
    for (const node of change.addedNodes) {
      if (node.nodeType !== 1) continue;
      if (node.matches("table.data")) sortable(node);
      node.querySelectorAll("table.data").forEach(sortable);
    }
  }
}).observe(main, { childList: true, subtree: true });

function route() {
  const [name, ...rest] = (location.hash.replace(/^#\/?/, "") || "overview").split("/");
  return { name: VIEWS[name] ? name : "overview", arg: rest.join("/") };
}

async function render() {
  const { name, arg } = route();
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.route === (SECTION[name] || name)) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = `${TITLES[name]} · optlab desk`;
  if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
  const page = h("div", { class: "page" });
  clear(main, page);
  try {
    const mod = await VIEWS[name]();
    cleanup = (await mod.render(page, arg)) || null;
  } catch (e) {
    clear(page, h("div", { class: "notice err", role: "alert" }, `This screen failed to load: ${e.message}`));
  }
  main.focus({ preventScroll: true });
}

// Where the Monte Carlo paths come from: the GPU, every CPU thread, or NumPy.
function engineLabel(engine) {
  if (!engine) return "";
  if (engine.resolved === "gpu") return ` · GPU ${engine.devices.length ? engine.devices[0].name.replace("NVIDIA GeForce ", "") : ""}`;
  if (engine.resolved === "cpu") return ` · CPU engine, ${engine.cpu_threads} threads`;
  return " · NumPy";
}

async function refreshStatus() {
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");
  try {
    const health = await api("/api/health");
    desk.health = health;
    if (!desk.context) desk.context = await api("/api/context");
    dot.className = "dot ok";
    const jobs = health.jobs.length ? ` · ${health.jobs.length} job${health.jobs.length > 1 ? "s" : ""} running` : "";
    const origin = health.origin && health.origin.surface ? ` · surface ${health.origin.surface}` : "";
    text.textContent = `Market ${health.snapshot_id} · valuation ${health.valuation}${origin}` +
      `${engineLabel(health.engine)}${health.workers ? ` · ${health.workers} workers` : ""}${jobs}`;
  } catch (e) {
    dot.className = "dot bad";
    text.textContent = e.message;
  }
}

function showHelp() {
  const rows = [["g o", "Overview"], ["g p", "Pricer"], ["g b", "Book"], ["g l", "Lifecycle"], ["g m", "Market"],
    ["g s", "Script lab"], ["g v", "Vol lab"], ["g r", "Replay"], ["g d", "Data"], ["Ctrl + Enter", "Quote (in the pricer)"], ["t", "Switch theme"], ["?", "This help"]];
  dialog({ title: "Keyboard shortcuts", ok: "Close", cancel: "Back",
    body: h("table", { class: "data" }, rows.map(([k, v]) => h("tr", {}, h("td", {}, h("span", { class: "kbd-hint" }, k)),
      h("td", {}, v)))) });
}

function cycleTheme() {
  const order = ["system", "light", "dark"];
  const next = order[(order.indexOf(state.theme) + 1) % order.length];
  save({ theme: next });
  applyTheme(next);
}

let pendingG = false;
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (["input", "select", "textarea"].includes(tag) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (pendingG) {
    pendingG = false;
    const map = { o: "overview", p: "pricer", b: "book", l: "lifecycle", m: "market", s: "script", v: "vollab", r: "replay", d: "data" };
    if (map[e.key]) { location.hash = `#/${map[e.key]}`; e.preventDefault(); }
    return;
  }
  if (e.key === "g") { pendingG = true; setTimeout(() => { pendingG = false; }, 1200); }
  else if (e.key === "?") showHelp();
  else if (e.key === "t") cycleTheme();
});

document.getElementById("theme-toggle").addEventListener("click", cycleTheme);
document.getElementById("help-toggle").addEventListener("click", showHelp);
window.addEventListener("hashchange", render);
applyTheme();
refreshStatus().then(render);
setInterval(refreshStatus, 10_000);
window.addEventListener("optlab:jobs", refreshStatus);
