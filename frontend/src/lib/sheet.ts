// A term sheet's fields between the API and a person (the v0 desk's rules, kept): the API works in fractions, the
// screen shows percent where a field is a level or a rate, basis points where its unit is bp. The server says which
// fields show and validates them (POST /structuring/termsheet/form); this only converts and labels.
import type { Schemas } from "../api/client";

export type FormField = Schemas["FormField"];
export type Values = Record<string, Record<string, unknown>>;

const PERCENT_UNITS = new Set(["of initial", "per annum", "of notional", "vol points", "points", "of dividends"]);
const SUFFIX: Record<string, string> = {
  "vol points": "pt",
  points: "pt",
  "of dividends": "%",
  bp: "bp",
  "% per 1 %": "% / 1 %",
};

type FieldLike = Pick<FormField, "name"> & {
  kind?: string | undefined;
  unit?: string | undefined;
  optional?: boolean | undefined;
};

export function isPercent(f: FieldLike): boolean {
  return PERCENT_UNITS.has(f.unit ?? "") || f.name === "participation";
}

/** What one unit of the library's fraction is on screen. */
export function scaleOf(f: FieldLike): number {
  if (f.unit === "bp") return 1e4;
  return isPercent(f) ? 100 : 1;
}

/** The value as the input shows it. */
export function display(f: FieldLike, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (f.kind === "names") return Array.isArray(value) ? value.join(", ") : String(value);
  if (f.kind === "number" && typeof value === "number" && scaleOf(f) !== 1) {
    return String(Number((scaleOf(f) * value).toFixed(6)));
  }
  return String(value);
}

/** The text typed, as the API takes it; a number that does not read stays text, for the server to refuse. */
export function parse(f: FieldLike, text: string): unknown {
  if (f.kind === "names") {
    return text
      .split(/[,\s]+/)
      .map((x) => x.trim().toUpperCase())
      .filter(Boolean);
  }
  if (f.kind === "date") return text || null;
  if (f.kind === "int") {
    const v = Number(text);
    return text.trim() !== "" && Number.isFinite(v) ? Math.round(v) : text;
  }
  if (f.kind === "number") {
    if (f.optional && text.trim() === "") return null;
    const v = Number(text.replace(",", ".").replaceAll("%", "").trim());
    if (text.trim() === "" || !Number.isFinite(v)) return text;
    return Number((v / scaleOf(f)).toPrecision(12));
  }
  return text;
}

/** The unit after the input. */
export function suffix(f: FieldLike): string {
  const own = SUFFIX[f.unit ?? ""];
  if (own) return own;
  if (f.kind === "number" && isPercent(f)) return f.unit === "per annum" ? "% p.a." : "%";
  if (f.kind === "int") return f.unit === "business days" ? "days" : (f.unit ?? "");
  return "";
}

/** A flat sheet (section -> field -> value, with its `type`) as the API takes it back: {type, values}. */
export type Mask = "autocall" | "bullet" | "variance"; // the API's product types (TermSheetIn), checked there

export function toRequest(sheet: Record<string, unknown>): { type: Mask; values: Values } {
  const values: Values = {};
  for (const [k, v] of Object.entries(sheet)) {
    if (k !== "type" && v !== null && typeof v === "object" && !Array.isArray(v)) values[k] = { ...(v as object) };
  }
  return { type: typeof sheet.type === "string" ? (sheet.type as Mask) : "autocall", values };
}

/** One line that says what a sheet is: "24M · SPX · conditional coupon · PDI 60 % european". */
export function describeSheet(sheet: Record<string, unknown> | null | undefined): string {
  if (!sheet) return "";
  const g = (sheet.general ?? {}) as Record<string, unknown>;
  const c = (sheet.coupon ?? {}) as Record<string, unknown>;
  const d = (sheet.downside ?? {}) as Record<string, unknown>;
  const w = (sheet.wrapper ?? {}) as Record<string, unknown>;
  const names = Array.isArray(g.underlyings) ? (g.underlyings as string[]).join(", ") : "";
  const und = g.basket === "worst_of" ? `worst of ${names}` : String(g.underlying ?? "SPX");
  const parts = [`${g.maturity_months ?? "?"}M`, und];
  const solving = w.solve === "coupon" || w.solve === "strike";
  const v = sheet.variance as Record<string, unknown> | undefined;
  if (v) {
    const strike = typeof v.strike === "number" && !solving ? ` ${(100 * v.strike).toFixed(2)} vol` : "";
    parts.push(`${v.type === "volatility" ? "volatility" : "variance"} swap${strike}`);
    const lo = typeof v.lower === "number" ? `${(100 * v.lower).toFixed(0)} %` : null;
    const hi = typeof v.upper === "number" ? `${(100 * v.upper).toFixed(0)} %` : null;
    if (v.corridor === "range" && lo && hi) parts.push(`corridor ${lo} to ${hi}`);
    if (v.corridor === "down" && hi) parts.push(`below ${hi}`);
    if (v.corridor === "up" && lo) parts.push(`above ${lo}`);
    return parts.join(" · ");
  }
  if (c.type && c.type !== "none") {
    const rate = typeof c.rate === "number" && !solving ? ` ${(100 * c.rate).toFixed(2)} %` : "";
    parts.push(`${c.type} coupon${rate}`);
  }
  if (d.type === "pdi" && typeof d.barrier === "number") {
    parts.push(`PDI ${(100 * d.barrier).toFixed(0)} % ${String(d.observation ?? "")}`.trim());
  }
  if (d.type === "geared_put" && typeof d.strike === "number")
    parts.push(`geared put ${(100 * d.strike).toFixed(0)} %`);
  return parts.join(" · ");
}

/** A choice's words for people: "modified_following" → "modified following". */
export function choiceLabel(value: unknown): string {
  return String(value).replaceAll("_", " ");
}

/** What a trade pays on, for a table's cell: a note's coupon per annum, or a swap's strike in vol points. */
export function couponOrStrike(
  summary: { coupon_rate?: number | null; strike?: number | null } | null | undefined,
): string {
  if (typeof summary?.coupon_rate === "number") return `${(100 * summary.coupon_rate).toFixed(2)} %`;
  if (typeof summary?.strike === "number") return `${(100 * summary.strike).toFixed(2)} vol`;
  return "—";
}
