// A number on screen: monospace, tabular, signed where the sign means something. Colour is never the only carrier:
// a signed value keeps its sign and gets an arrow. A Monte Carlo number carries its standard error, and one inside
// its noise is grey and italic, with a title that says so.
import { type Estimate, formatEstimate, formatNumber, formatPercent, isNoise, signOf } from "../lib/format";
import classes from "./Value.module.css";

const NOISE = "Within two standard errors of zero: read it as noise.";

function signClass(sign: -1 | 0 | 1): string {
  return (sign > 0 ? classes.pos : sign < 0 ? classes.neg : "") ?? "";
}

function Arrow({ sign }: { sign: -1 | 0 | 1 }) {
  if (sign === 0) return null;
  return (
    <span className={classes.arrow} aria-hidden="true">
      {sign > 0 ? "▲" : "▼"}
    </span>
  );
}

type Common = {
  /** decimals; for an estimate, from its standard error by default */
  digits?: number;
  /** write + before positive values */
  sign?: boolean;
  /** colour by sign, with an arrow: P&L, changes, Greeks */
  signed?: boolean;
  /** a short unit after the number ("%", "bp", "USD") */
  unit?: string;
  className?: string;
};

export function Num({
  value,
  digits = 0,
  sign,
  signed,
  unit,
  className,
}: Common & { value: number | null | undefined }) {
  const s = value === null || value === undefined ? 0 : signOf(value, digits);
  return (
    <span className={[classes.num, signed ? signClass(s) : "", className].filter(Boolean).join(" ")}>
      {signed && <Arrow sign={s} />}
      {formatNumber(value, { digits, sign: sign ?? signed })}
      {unit && <span className={classes.unit}>{unit}</span>}
    </span>
  );
}

/** A fraction shown as a percentage. */
export function Pct({ value, digits = 2, sign, signed, className }: Common & { value: number | null | undefined }) {
  const s = value === null || value === undefined ? 0 : signOf(value * 100, digits);
  return (
    <span className={[classes.num, signed ? signClass(s) : "", className].filter(Boolean).join(" ")}>
      {signed && <Arrow sign={s} />}
      {formatPercent(value, { digits, sign: sign ?? signed })}
    </span>
  );
}

/** A Monte Carlo number: value ± SE, grey and italic inside its noise. `scale` 100 shows a fraction in percent. */
export function Est({
  value,
  digits,
  sign,
  signed,
  unit,
  scale,
  hideSe,
  className,
}: Common & { value: Estimate | null | undefined; scale?: number; hideSe?: boolean }) {
  if (value === null || value === undefined) {
    return <Num value={null} className={className} />;
  }
  const f = formatEstimate(value, { digits, sign: sign ?? signed, scale });
  const noise = isNoise(value);
  const cls = [classes.num, noise ? classes.noise : signed ? signClass(f.sign) : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={cls} title={noise ? NOISE : undefined}>
      {signed && !noise && <Arrow sign={f.sign} />}
      {f.value}
      {unit && <span className={classes.unit}>{unit}</span>}
      {f.se !== null && !hideSe && (
        <span className={classes.se}>
          <span aria-hidden="true">± </span>
          <span className="visually-hidden">plus or minus </span>
          {f.se}
        </span>
      )}
      {noise && <span className="visually-hidden"> (in the noise)</span>}
    </span>
  );
}
