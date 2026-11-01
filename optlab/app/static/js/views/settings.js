// Settings: preferences of this browser and what the server runs with.

import { api } from "../api.js";
import { applyTheme, save, state } from "../state.js";
import { clear, field, h, segmented, toast } from "../ui.js";

export async function render(page) {
  let health = null;
  try { health = await api("/api/health"); } catch (e) { /* shown below */ }
  const draw = () => clear(page,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Settings"), h("p", {}, "Preferences are kept in this browser."))),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card stack" },
        h("h2", {}, "Display"),
        field("Theme", segmented([["system", "System"], ["light", "Light"], ["dark", "Dark"]], state.theme,
          (v) => { save({ theme: v }); applyTheme(v); draw(); }, "Theme")),
        field("Default precision", segmented([["quick", "Quick · 20k"], ["standard", "Standard · 100k"], ["precise", "Precise · 200k"]],
          state.precision, (v) => { save({ precision: v }); draw(); }, "Precision"),
          { hint: "Quick to iterate, Precise for the number you keep." }),
        h("div", { class: "btn-row" }, h("button", { class: "btn", type: "button", onclick: () => {
          try { localStorage.removeItem("optlab.desk"); localStorage.removeItem("optlab.script"); } catch (e) { /* ignore */ }
          toast("Local preferences cleared: reload the page");
        } }, "Clear local preferences"))),
      h("div", { class: "card stack" },
        h("h2", {}, "Server"),
        health ? h("table", { class: "data" },
          [["Market snapshot", health.snapshot_id], ["Valuation date", health.valuation],
            ["Monte Carlo workers", health.workers || "1 (sequential)"], ["Store", health.store], ["Started", health.started],
            ["Public address", health.public_url || "none (start with --tunnel)"]]
            .map(([k, v]) => h("tr", {}, h("td", { class: "muted" }, k), h("td", { style: { overflowWrap: "anywhere" } }, String(v)))))
          : h("div", { class: "notice err" }, "The server does not answer."),
        h("p", { class: "muted" }, "Start options: --workers 6 (process pool), --store and --calibration to change the "
          + "files.")),
      h("div", { class: "card stack" },
        h("h2", {}, "Sharing"),
        h("p", {}, "The engine stays on this PC; the people you share with use their browser."),
        h("table", { class: "data nosort" },
          h("tr", {}, h("th", {}, "How"), h("th", {}, "Start with"), h("th", {}, "Notes")),
          [["This PC only", "python -m optlab.app", "the default: listens on 127.0.0.1, no token"],
            ["Your network", "python -m optlab.app --host 0.0.0.0", "a token is generated; share the printed address"],
            ["A public link", "python -m optlab.app --tunnel", "https through localhost.run (ssh, no install); a third "
              + "party relays the traffic, so the token is always on; the address changes at each start"],
            ["A fixed result", "Open the report (Pricer or Book)", "one HTML file that opens anywhere without the desk"]]
            .map(([a, c, n]) => h("tr", {}, h("td", {}, a), h("td", {}, h("code", {}, c)), h("td", { class: "muted" }, n)))))));
  draw();
}
