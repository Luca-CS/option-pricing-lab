// How the desk writes a number (HANDOFF_REFACTOR_2_UX.md §5): tabular, thousands separated, an explicit sign where
// it means something, the minus as a real minus, the standard error after a muted ±, and a value inside its noise
// (|v| < 2 SE) said to be noise. Levels are fractions in the API and percentages on screen: the conversion happens
// here, at the edge, and nowhere else.

export const MINUS = "−";
export const DASH = "—";

export type Estimate = { value: number; se?: number | null };

const formats = new Map<number, Intl.NumberFormat>();

function formatter(digits: number): Intl.NumberFormat {
  let f = formats.get(digits);
  if (f === undefined) {
    f = new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    formats.set(digits, f);
  }
  return f;
}

/** The sign of a value as it will be written with `digits` decimals: a value that rounds to zero has none. */
export function signOf(value: number, digits = 0): -1 | 0 | 1 {
  if (!Number.isFinite(value)) return 0;
  const shown = Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value;
  return shown > 0 ? 1 : shown < 0 ? -1 : 0;
}

export type NumberOptions = {
  /** decimals (default 0) */
  digits?: number;
  /** write + before a positive value (P&L, changes, Greeks) */
  sign?: boolean;
};

/** 1234567.8 → "1,234,568"; −0.5 → "−1"; with sign: "+1,234,568". Not a number: an em dash. */
export function formatNumber(value: number | null | undefined, options: NumberOptions = {}): string {
  const digits = options.digits ?? 0;
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const s = signOf(value, digits);
  const body = formatter(digits).format(Math.abs(value));
  if (s < 0) return MINUS + body;
  if (s > 0 && options.sign) return `+${body}`;
  return s === 0 ? formatter(digits).format(0) : body;
}

/** A fraction as a percentage: 0.2133 → "21.33 %". */
export function formatPercent(fraction: number | null | undefined, options: NumberOptions = {}): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return DASH;
  return `${formatNumber(fraction * 100, { digits: 2, ...options })} %`;
}

/** A fraction in basis points: 0.0025 → "25 bp". */
export function formatBp(fraction: number | null | undefined, options: NumberOptions = {}): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return DASH;
  return `${formatNumber(fraction * 10_000, { digits: 0, ...options })} bp`;
}

/** Is this Monte Carlo number inside its noise: closer to zero than two standard errors? */
export function isNoise(e: Estimate): boolean {
  return e.se !== null && e.se !== undefined && e.se > 0 && Math.abs(e.value) < 2 * e.se;
}

/** Decimals that show the standard error to one significant digit: 191 → 0, 0.0658 → 2, 0.0004 → 4. */
export function digitsForSe(se: number | null | undefined, fallback = 2): number {
  if (se === null || se === undefined || !Number.isFinite(se) || se <= 0) return fallback;
  return Math.min(8, Math.max(0, Math.ceil(-Math.log10(se))));
}

export type FormattedEstimate = {
  value: string;
  /** the standard error without its ±, or null when the number has none (a replication, a closed form) */
  se: string | null;
  noise: boolean;
  sign: -1 | 0 | 1;
};

/** A {value, se} as the desk writes it, both with the same decimals. */
export function formatEstimate(e: Estimate, options: NumberOptions & { scale?: number } = {}): FormattedEstimate {
  const scale = options.scale ?? 1;
  const value = e.value * scale;
  const se = e.se === null || e.se === undefined ? null : e.se * scale;
  const digits = options.digits ?? digitsForSe(se, 2);
  return {
    value: formatNumber(value, { digits, sign: options.sign }),
    se: se === null ? null : formatNumber(se, { digits }),
    noise: isNoise(e),
    sign: signOf(value, digits),
  };
}

/** For an input: a fraction of the API as the percentage a person types (0.07 → 7, not 7.000000000000001). */
export function toPercentInput(fraction: number | null | undefined): number | null {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return null;
  return Number((fraction * 100).toPrecision(12));
}

/** From an input: the percentage typed, as the fraction the API takes (7 → 0.07). */
export function fromPercentInput(percent: number | null | undefined): number | null {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return null;
  return Number((percent / 100).toPrecision(12));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** An ISO date as people read it: "2021-06-30" → "30 Jun 2021" (the same on every browser and locale). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  const month = m ? MONTHS[Number(m[2]) - 1] : undefined;
  return m && month ? `${Number(m[3])} ${month} ${m[1]}` : iso;
}

/** Seconds as a short duration: 2.6 → "2.6 s", 95 → "1 min 35 s". */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return DASH;
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - 60 * m);
  return s === 0 ? `${m} min` : `${m} min ${s} s`;
}
