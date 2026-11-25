// The names a person reads for the library's models, methods and policy classes (the API sends their keys).

export const MODEL: Record<string, string> = {
  lv: "Local vol (Dupire, on the SVI surface)",
  slv: "Stochastic local vol (SLV)",
  heston: "Heston",
  bs: "Black-Scholes",
  surface: "Replication on the SVI smile",
  localcorr: "Local correlation",
};

export const METHOD: Record<string, string> = {
  monte_carlo: "Monte Carlo",
  replication: "replication on the SVI smile",
  closed_form: "closed form",
};

export const CLASS: Record<string, string> = {
  european: "European",
  path_dependent: "Path-dependent",
  callable: "Callable",
};

/** What a desk line is, as the desk says it. */
export const LINE_KIND: Record<string, string> = {
  model: "model reserve",
  bump: "market over",
  digital: "digital overhedge",
  interaction: "cross-effect",
};

export const STICKY: Record<string, string> = {
  moneyness: "sticky moneyness",
  strike: "sticky strike",
  model: "the model's own",
};

export function modelName(key: string | null | undefined): string {
  if (!key) return "—";
  return MODEL[key] ?? key;
}

/** A desk line's label, short enough for a chart's axis: "Overskew (90-110 % at 1 year)" → "Overskew". */
export function shortLine(label: string): string {
  return label
    .replace(/\s*\(.*\)$/, "")
    .replace(/^Digital overhedge: /, "digital: ")
    .replace(/^Model reserve: /, "model: ");
}
