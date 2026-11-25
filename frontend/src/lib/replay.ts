// A quote's backtest (Structuring › a quote › Backtest): the replay lab lives a preset struck on a past date, with
// dotted changes (`POST /quant/replay/runs` takes `preset`, `underlying`, `changes`). This finds the preset of the
// quote's shape closest to its sheet and the fields that differ from it, so the replay lives the quote's own product.
// The dates are the replay's (its strike date), never the quote's.

type Flat = Record<string, unknown>;

export type ReplayStart = {
  preset: string;
  underlying: string;
  changes: Flat;
  /** fields of the sheet the replay sets itself: its dates */
  dates: string[];
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** section -> field -> value, flattened to "section.field" (the sheet's `type` is its preset's shape, not a field). */
export function flatten(sheet: Record<string, unknown>): Flat {
  const out: Flat = {};
  for (const [section, fields] of Object.entries(sheet)) {
    if (section === "type" || !fields || typeof fields !== "object" || Array.isArray(fields)) continue;
    for (const [field, value] of Object.entries(fields as Flat)) out[`${section}.${field}`] = value;
  }
  return out;
}

function isDateField(key: string, value: unknown): boolean {
  return key.endsWith("_date") || (typeof value === "string" && ISO_DATE.test(value));
}

/** The replay of a quote's sheet: the closest preset of its shape among `allowed`, and the fields that differ. */
export function replayStart(
  sheet: Record<string, unknown>,
  presets: { name: string; sheet: Record<string, unknown> }[],
  allowed: string[],
): ReplayStart | null {
  const own = flatten(sheet);
  const general = (sheet.general ?? {}) as Flat;
  const names = Array.isArray(general.underlyings) ? (general.underlyings as string[]) : [];
  const underlying = typeof general.underlying === "string" && general.underlying ? general.underlying : names[0];
  if (!underlying) return null;
  let best: ReplayStart | null = null;
  for (const p of presets) {
    if (!allowed.includes(p.name) || p.sheet.type !== sheet.type) continue;
    const base = flatten(p.sheet);
    const changes: Flat = {};
    const dates: string[] = [];
    for (const [key, value] of Object.entries(own)) {
      if (key === "general.underlying") continue;
      if (isDateField(key, value)) {
        if (value !== null && value !== undefined) dates.push(key);
        continue;
      }
      if (JSON.stringify(value) !== JSON.stringify(base[key])) changes[key] = value;
    }
    if (!best || Object.keys(changes).length < Object.keys(best.changes).length) {
      best = { preset: p.name, underlying, changes, dates };
    }
  }
  return best;
}
