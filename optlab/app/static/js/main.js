// Shell: router, status bar, theme, keyboard shortcuts.

import { api } from "./api.js";
import { clear, dialog, h } from "./ui.js";
import { applyTheme, desk, save, state } from "./state.js";

const VIEWS = {
  overview: () => import("./views/overview.js"),
  pricer: () => import("./views/pricer.js"),
  book: () => import("./views/book.js"),
  lifecycle: () => import("./views/lifecycle.js"),
  market: () => import("./views/market.js"),
  script: () => import("./views/script.js"),
  settings: () => import("./views/settings.js"),
};
const TITLES = { overview: "Overview", pricer: "Pricer", book: "Book", lifecycle: "Lifecycle", market: "Market",
  script: "Script lab", settings: "Settings" };

const main = document.getElementById("main");
let cleanup = null;

function route() {
  const [name, ...rest] = (location.hash.replace(/^#\/?/, "") || "overview").split("/");
  return { name: VIEWS[name] ? name : "overview", arg: rest.join("/") };
}

async function render() {
  const { name, arg } = route();
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.route === name) a.setAttribute("aria-current", "page");
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

async function refreshStatus() {
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");
  try {
    const health = await api("/api/health");
    desk.health = health;
    if (!desk.context) desk.context = await api("/api/context");
    dot.className = "dot ok";
    const jobs = health.jobs.length ? ` · ${health.jobs.length} job${health.jobs.length > 1 ? "s" : ""} running` : "";
    text.textContent = `Market ${health.snapshot_id} · valuation ${health.valuation}` +
      `${health.workers ? ` · ${health.workers} workers` : ""}${jobs}`;
  } catch (e) {
    dot.className = "dot bad";
    text.textContent = e.message;
  }
}

function showHelp() {
  const rows = [["g o", "Overview"], ["g p", "Pricer"], ["g b", "Book"], ["g l", "Lifecycle"], ["g m", "Market"],
    ["g s", "Script lab"], ["Ctrl + Enter", "Quote (in the pricer)"], ["t", "Switch theme"], ["?", "This help"]];
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
    const map = { o: "overview", p: "pricer", b: "book", l: "lifecycle", m: "market", s: "script" };
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
