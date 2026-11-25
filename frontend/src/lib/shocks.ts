// A scenario's shock in words, as `optlab.risk.shocks.Shock` stores it: moves of spots, dividends and FX are
// fractions, vol moves are absolute (0.02 is two vol points) or relative, curves move in basis points, correlation in
// points of correlation. Only the wording is here; what the shock does is the server's.
import { formatNumber, formatPercent } from "./format";

type Curve = { parallel?: number; tenors?: Record<string, number> };
type Vol = {
  parallel?: number;
  relative?: number;
  skew?: number;
  nodes?: Record<string, number>;
  relative_nodes?: Record<string, number>;
  term?: Record<string, number>;
};

const RULE: Record<string, string> = {
  sticky_moneyness: "sticky moneyness",
  sticky_strike: "sticky strike",
  realistic: "realistic (spot-vol betas)",
};

const who = (name: string) => (name === "*" ? "all" : name);
const pct = (x: number) => formatPercent(x, { digits: 0, sign: true });
const pts = (x: number) => `${formatNumber(x * 100, { digits: 1, sign: true })} pts`;
const bp = (x: number) => `${formatNumber(x, { digits: 0, sign: true })} bp`;

function curve(c: Curve): string {
  const parts = [];
  if (c.parallel) parts.push(bp(c.parallel));
  const tenors = Object.entries(c.tenors ?? {}).filter(([, v]) => v);
  if (tenors.length) parts.push(`${tenors.length} tenor${tenors.length > 1 ? "s" : ""} bumped`);
  return parts.join(", ");
}

function vol(v: Vol): string {
  const parts = [];
  if (v.parallel) parts.push(pts(v.parallel));
  if (v.relative) parts.push(`${pct(v.relative)} relative`);
  if (v.skew) parts.push(`skew ${pts(v.skew)}`);
  const nodes = Object.keys(v.nodes ?? {}).length + Object.keys(v.relative_nodes ?? {}).length;
  if (nodes) parts.push(`${nodes} node${nodes > 1 ? "s" : ""}`);
  const term = Object.keys(v.term ?? {}).length;
  if (term) parts.push(`${term} tenor${term > 1 ? "s" : ""}`);
  return parts.join(", ");
}

// a move of zero says nothing
const moved = (say: (x: number) => string) => ((x: number) => (x ? say(x) : "")) as (v: never) => string;

const LINES: [string, string, (v: never) => string][] = [
  ["spot", "Spot", moved(pct)],
  ["vol", "Vol", vol as (v: never) => string],
  ["dividend_relative", "Dividends", moved(pct)],
  ["dividend", "Dividend yield", curve as (v: never) => string],
  ["repo", "Repo", curve as (v: never) => string],
  ["rates", "Rates", curve as (v: never) => string],
  ["discount", "Discount", curve as (v: never) => string],
  ["forward_rate", "Rate in the forward", curve as (v: never) => string],
  ["fx", "FX", moved(pct)],
  ["quanto_corr", "Quanto correlation", moved(pts)],
  ["fx_vol", "FX vol", moved(pts)],
  ["corr_pairs", "Correlation", moved(pts)],
];

/** One line per moved part: "Spot · all −20 %", "Vol · all skew +2.0 pts", "Rates · all −50 bp". */
export function describeShock(shock: Record<string, unknown>): { what: string; move: string }[] {
  const out: { what: string; move: string }[] = [];
  for (const [key, what, say] of LINES) {
    const by = shock[key] as Record<string, never> | undefined;
    for (const [name, move] of Object.entries(by ?? {})) {
      const text = say(move);
      if (text) out.push({ what, move: `${key === "corr_pairs" ? name.replaceAll("|", " / ") : who(name)} ${text}` });
    }
  }
  if (typeof shock.corr === "number" && shock.corr)
    out.push({ what: "Correlation", move: `every pair ${pts(shock.corr)}` });
  if (typeof shock.roll_days === "number" && shock.roll_days) {
    out.push({ what: "Time", move: `${shock.roll_days} business day${shock.roll_days > 1 ? "s" : ""} later` });
  }
  const known = new Set([...LINES.map(([k]) => k), "corr", "roll_days", "rule", "rule_spot"]);
  for (const key of Object.keys(shock).filter((k) => !known.has(k)))
    out.push({ what: key, move: JSON.stringify(shock[key]) });
  if (typeof shock.rule === "string" && shock.rule !== "sticky_moneyness") {
    out.push({ what: "Vol rule", move: RULE[shock.rule] ?? shock.rule });
  }
  return out;
}
