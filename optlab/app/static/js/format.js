// Term-sheet values: the API works in fractions, the page shows percent where a field is a level or a rate.

const PERCENT_UNITS = new Set(["of initial", "per annum", "of notional", "vol points", "points", "of dividends"]);
const SUFFIX = { "vol points": "pt", points: "pt", "of dividends": "%", bp: "bp" };

// What one unit of the library's fraction is on screen.
function scale(f) {
  if (f.unit === "bp") return 1e4;
  return isPercent(f) ? 100 : 1;
}

export function isPercent(f) {
  return PERCENT_UNITS.has(f.unit) || f.name === "participation";
}

export function display(f, value) {
  if (value === null || value === undefined) return "";
  if (f.kind === "names") return Array.isArray(value) ? value.join(", ") : String(value);
  if (f.kind === "number" && scale(f) !== 1) return String(+(scale(f) * value).toFixed(6));
  return String(value);
}

export function parse(f, text) {
  if (f.kind === "names") return text.split(/[,\s]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);
  if (f.kind === "date") return text || null;
  if (f.kind === "int") {
    const v = Number(text);
    return Number.isFinite(v) ? Math.round(v) : text;
  }
  if (f.kind === "number") {
    if (f.optional && String(text).trim() === "") return null;
    const v = Number(String(text).replace(",", ".").replace("%", "").trim());
    if (!Number.isFinite(v)) return text;
    return v / scale(f);
  }
  return text;
}

export function suffix(f) {
  if (SUFFIX[f.unit]) return SUFFIX[f.unit];
  if (f.kind === "number" && isPercent(f)) return f.unit === "per annum" ? "% p.a." : "%";
  if (f.kind === "int") return f.unit === "business days" ? "days" : f.unit || "";
  return "";
}

export function describeSheet(sheet) {
  if (!sheet) return "";
  const g = sheet.general || {}, c = sheet.coupon || {}, d = sheet.downside || {};
  const und = g.basket === "worst_of" ? `worst of ${(g.underlyings || []).join(", ")}` : g.underlying || "SPX";
  const parts = [`${g.maturity_months}M`, und];
  const solving = (sheet.wrapper || {}).solve === "coupon";
  if (c.type && c.type !== "none") {
    parts.push(`${c.type} coupon${c.rate != null && !solving ? " " + (100 * c.rate).toFixed(2) + " %" : ""}`);
  }
  if (d.type === "pdi" && d.barrier != null) parts.push(`PDI ${(100 * d.barrier).toFixed(0)} % ${d.observation || ""}`.trim());
  if (d.type === "geared_put" && d.strike != null) parts.push(`geared put ${(100 * d.strike).toFixed(0)} %`);
  return parts.join(" · ");
}

// The library's notes name its arguments; on screen, point to the switch instead.
export function vegaNote(note) {
  if (/no vega under SLV/.test(note || "")) return "No vega under SLV unless the leverage is recalibrated: turn on SLV vega";
  return note || "";
}
