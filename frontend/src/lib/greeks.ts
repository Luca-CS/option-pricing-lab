// Reading a risk run's measures. The API keys every measure "greek|name|bucket" (`book["vega_node|SPX|80%@1y"]`,
// docs/architecture/api.md §5) and gives each greek's unit in `units`. This only splits keys and names them for the
// screen: every number stays the server's.
import type { Estimate } from "./format";

export type MeasureKey = { greek: string; name: string; bucket: string };

export function parseKey(key: string): MeasureKey {
  const [greek = "", name = "", bucket = ""] = key.split("|");
  return { greek, name, bucket };
}

const LABELS: Record<string, string> = {
  value: "Value",
  delta: "Delta",
  delta_sticky_strike: "Delta, sticky strike",
  delta_realistic: "Delta, realistic",
  delta_units: "Delta in units",
  delta_notional: "Delta, % of notional",
  gamma: "Gamma 1 %",
  gamma_5: "Gamma 5 %",
  cross_gamma: "Cross gamma",
  vega: "Vega",
  vega_net: "Vega, net of the smile",
  vega_relative: "Vega, relative",
  vega_weighted: "Vega, weighted",
  vega_weighted_direct: "Vega, weighted (direct)",
  vega_tenor: "Vega by tenor",
  vega_node: "Vega by node",
  skew_vega: "Skew vega",
  vanna: "Vanna",
  volga: "Volga",
  theta: "Theta",
  theta_carry: "Theta, carry",
  rho: "Rho",
  rho_discount: "Rho, discount",
  rho_forward: "Rho, forward",
  rho_tenor: "Rho by tenor",
  repo: "Repo",
  epsilon: "Epsilon (dividends)",
  epsilon_tenor: "Epsilon by tenor",
  corr: "Correlation",
  quanto: "Quanto",
};

export function greekLabel(greek: string): string {
  return LABELS[greek] ?? greek.replaceAll("_", " ");
}

/** The unit of a greek from the run's `units`: its own entry, else the longest one it starts with ("vega_node" →
 * "vega"). */
export function unitOf(greek: string, units: Record<string, unknown> | undefined): string | undefined {
  if (!units) return undefined;
  let best: string | undefined;
  for (const [k, v] of Object.entries(units)) {
    if (typeof v !== "string") continue;
    if (greek === k) return v;
    if (greek.startsWith(`${k}_`) && (best === undefined || k.length > best.length)) best = k;
  }
  return best === undefined ? undefined : (units[best] as string);
}

/** The headline greeks of a name, in a trader's order, as the run has them. */
export const HEADLINE = [
  "delta",
  "delta_sticky_strike",
  "gamma",
  "gamma_5",
  "vega",
  "skew_vega",
  "vanna",
  "volga",
  "theta",
  "rho",
  "repo",
] as const;

export type Row = { key: string; greek: string; name: string; bucket: string; value: Estimate };

export function rows(book: Record<string, Estimate>, greeks: readonly string[] = HEADLINE): Row[] {
  const out: Row[] = [];
  for (const g of greeks) {
    for (const [key, value] of Object.entries(book)) {
      const k = parseKey(key);
      if (k.greek === g && k.bucket === "") out.push({ key, ...k, value });
    }
  }
  return out;
}

/** A ladder point's key: "delta@realistic@-0.0500" (the measure, the vol rule, the spot shift to four decimals). */
export function ladderKey(measure: string, rule: string, shift: number): string {
  return `${measure}@${rule}@${shift < 0 ? "-" : "+"}${Math.abs(shift).toFixed(4)}`;
}

/** One series per vol rule of a measure along the ladder's shifts; a point the run lacks is left out of its series. */
export function ladderSeries(
  book: Record<string, Estimate>,
  measure: string,
  rules: readonly string[],
  shifts: readonly number[],
): { name: string; values: Estimate[] }[] {
  return rules.map((rule) => ({
    name: rule.replaceAll("_", " "),
    values: shifts.flatMap((s) => {
      const e = book[ladderKey(measure, rule, s)];
      return e ? [e] : [];
    }),
  }));
}

export type NodeGrid = { strikes: string[]; tenors: string[]; cells: (Estimate | null)[][] };

/** The vega of each node "K@T" of a name as a strikes × tenors grid, in the order of the run's `grid`. */
export function nodeGrid(
  book: Record<string, Estimate>,
  name: string,
  grid: { strike_labels: string[]; tenor_labels: string[] },
  greek = "vega_node",
): NodeGrid {
  const cells = grid.strike_labels.map((k) => grid.tenor_labels.map((t) => book[`${greek}|${name}|${k}@${t}`] ?? null));
  return { strikes: grid.strike_labels, tenors: grid.tenor_labels, cells };
}
