import type { CSSProperties, ReactNode } from "react";
import classes from "./Kpi.module.css";

/** A row of tiles that wraps on narrow screens. */
export function KpiRow({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <section className={classes.row} aria-label={label}>
      {children}
    </section>
  );
}

/** A key figure: its label and unit, the value (a Num, Pct or Est), a line under it, and an optional meter (0 to 1)
 * whose meaning the line under it says in words. */
export function Kpi({
  label,
  unit,
  children,
  sub,
  meter,
}: {
  label: ReactNode;
  unit?: ReactNode;
  children: ReactNode;
  sub?: ReactNode;
  meter?: number;
}) {
  const fill = meter === undefined ? undefined : Math.max(0, Math.min(1, meter));
  return (
    <div className={classes.tile}>
      <span className={classes.label}>
        {label}
        {unit && <span className={classes.unit}>{unit}</span>}
      </span>
      <span className={classes.value}>{children}</span>
      {fill !== undefined && (
        // the line under it says the same in words: the bar is decoration
        <span className={classes.meter} aria-hidden="true">
          <span className={classes.fill} style={{ "--fill": `${fill * 100}%` } as CSSProperties} />
        </span>
      )}
      {sub && <span className={classes.sub}>{sub}</span>}
    </div>
  );
}
