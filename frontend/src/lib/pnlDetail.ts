// A day of the explained P&L opened (the API's `pnl_detail` run): reading helpers only. The numbers are the API's: each
// step is a full revaluation on the server, the nodes and the residual add up to the vol and skew steps there.
import type { Estimate } from "./format";

export const RULE_STEPS = ["spot", "vol", "skew"] as const;

export const SPOT_VOL_RULE: Record<string, string> = {
  sticky_moneyness: "Sticky moneyness",
  sticky_strike: "Sticky strike",
  realistic: "Realistic (spot-vol betas)",
};

export type RuleSteps = {
  steps?: Record<string, Estimate>;
  predicted?: Record<string, number>;
  unexplained?: Record<string, number>;
};

/** Spot, vol and skew of one rule together: the same amount under every rule, split differently. */
export function together(r: RuleSteps): number {
  return RULE_STEPS.reduce((a, s) => a + (r.steps?.[s]?.value ?? 0), 0);
}

/** The tenors the book reaches: the others moved no value (zero by construction, left out of the tables). */
export function liveTenors(tenors: string[], skipped: number[] | undefined): number[] {
  const out = new Set(skipped ?? []);
  return tenors.map((_, j) => j).filter((j) => !out.has(j));
}

export type Extreme = { strike: string; tenor: string; i: number; j: number; value: number };

/** The node that lost most, and the one that made most (null when none moved either way). */
export function extremes(
  nodes: (Estimate | null)[][] | null | undefined,
  strikes: string[],
  tenors: string[],
): { loss: Extreme | null; gain: Extreme | null } {
  let loss: Extreme | null = null;
  let gain: Extreme | null = null;
  (nodes ?? []).forEach((row, i) => {
    row.forEach((e, j) => {
      const v = e?.value ?? 0;
      const at = { strike: strikes[i] ?? "", tenor: tenors[j] ?? "", i, j, value: v };
      if (v < 0 && (!loss || v < loss.value)) loss = at;
      if (v > 0 && (!gain || v > gain.value)) gain = at;
    });
  });
  return { loss, gain };
}

/** The maturity whose vol move lost most (null when no tenor lost). */
export function worstTenor(byTenor: number[] | undefined, tenors: string[]): { tenor: string; value: number } | null {
  let out: { tenor: string; value: number } | null = null;
  (byTenor ?? []).forEach((v, j) => {
    if (v < 0 && (!out || v < out.value)) out = { tenor: tenors[j] ?? "", value: v };
  });
  return out;
}
