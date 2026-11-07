// Cost defaults: bumps per group of underlyings and size of trade. Every saved table is a new version; unchanged cells are starting values.

import { api } from "../api.js";
import { display, parse, suffix } from "../format.js";
import { clear, errorNotice, h, int, toast } from "../ui.js";

export async function render(page) {
  let data;
  try { data = await api("/api/costs/defaults"); } catch (e) { clear(page, errorNotice(e)); return; }
  let error = null;
  const fields = data.fields.map((f) => ({ ...f, optional: false }));

  const startOf = (group, i, key) => {
    const g = data.starting[group];
    return g && g.tiers[Math.min(i, g.tiers.length - 1)] ? g.tiers[Math.min(i, g.tiers.length - 1)][key] : undefined;
  };

  const cell = (name, tier, i, f) => {
    const isStart = tier[f.name] === startOf(name, i, f.name);
    return h("td", { class: "num" }, h("div", { class: "input-wrap" },
      h("input", { type: "text", inputmode: "decimal", value: display(f, tier[f.name]), class: suffix(f) ? "has-suffix" : "",
        title: isStart ? "Starting value, to correct" : "Set on this desk", "aria-label": `${f.label}, tier ${i + 1} of ${name}`,
        style: { width: "92px", fontStyle: isStart ? "italic" : "normal" },
        onchange: (e) => { tier[f.name] = parse(f, e.target.value); draw(); } }),
      suffix(f) ? h("span", { class: "suffix" }, suffix(f)) : null));
  };

  const card = ([name, g]) => h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, g.label || name), h("span", { class: "muted" }, `${name} · sizes in ${g.currency || "the group's currency"}`)),
    h("div", { class: "table-wrap" }, h("table", { class: "data nosort" },
      h("tr", {}, h("th", {}, "Up to a notional of"), g.tiers.map((t, i) => h("th", { class: "num" },
        h("input", { type: "number", step: "any", value: String(t.max_notional), "aria-label": `Size of tier ${i + 1} of ${name}`, style: { width: "130px" },
          onchange: (e) => { t.max_notional = Number(e.target.value); draw(); } })))),
      fields.map((f) => h("tr", {}, h("td", {}, f.label), g.tiers.map((t, i) => cell(name, t, i, f)))))),
    h("p", { class: "faint", style: { marginTop: "6px" } }, `Above ${int(g.tiers[g.tiers.length - 1].max_notional)}, the Pricer alerts and asks for the values.`));

  async function saveTable() {
    try {
      data = await api("/api/costs/defaults", { method: "PUT", body: { groups: data.groups } });
      error = null;
      toast(`Saved as version ${data.version}: the next quotes use it`);
    } catch (e) { error = e.message; }
    draw();
  }

  const draw = () => clear(page,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Cost defaults"),
        h("p", {}, "What the Pricer loads for a term sheet, by group of underlyings and size. A value carries its sign and applies as given.")),
      h("button", { class: "btn primary", type: "button", onclick: saveTable }, "Save a new version")),
    h("div", { class: "notice info", style: { marginBottom: "12px" } },
      "Values in italics are starting values, not given by a trader: correct them. A worst-of takes the most prudent of its groups, parameter by parameter; "
      + "when two groups disagree in sign, both are measured on the product and the one against the desk stays."),
    error ? h("div", { class: "notice err", role: "alert", style: { marginBottom: "12px" } }, error) : null,
    h("div", { class: "stack" }, Object.entries(data.groups).map(card),
      h("div", { class: "card" }, h("h2", {}, "Versions"),
        data.versions.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("tr", {}, h("th", {}, "Version"), h("th", {}, "Saved (UTC)")),
          data.versions.map((v) => h("tr", {}, h("td", {}, v.version), h("td", {}, v.recorded_at)))))
          : h("p", { class: "muted" }, "Nothing saved yet: the starting table is in use."))));

  draw();
}
