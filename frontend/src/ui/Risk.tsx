// The risk views every space shares: a table of Greeks with their units and errors, and the strike × tenor heatmap
// of node vegas, where a cell is a button (keyboard and screen reader included) that opens what lies behind it.
import type { CSSProperties } from "react";
import { type Estimate, formatEstimate, isNoise } from "../lib/format";
import { greekLabel, type Row, unitOf } from "../lib/greeks";
import classes from "./Risk.module.css";
import { Est } from "./Value";

export function GreeksTable({
  rows,
  units,
  showName = true,
  caption,
}: {
  rows: Row[];
  units?: Record<string, unknown>;
  showName?: boolean;
  caption?: string;
}) {
  return (
    <table className={classes.table}>
      {caption && <caption className="visually-hidden">{caption}</caption>}
      <thead>
        <tr>
          <th scope="col">Greek</th>
          {showName && <th scope="col">Name</th>}
          <th scope="col">Value ± SE</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const unit = unitOf(r.greek, units);
          return (
            <tr key={r.key}>
              <th scope="row">
                {greekLabel(r.greek)}
                {unit && <span className={classes.unit}>{shortUnit(unit)}</span>}
              </th>
              {showName && <td>{r.name === "*" ? "book" : r.name}</td>}
              <td>
                <Est value={r.value} signed />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** The API's units are sentences ("currency per +1 % spot (sticky moneyness)"): the header keeps their core. */
export function shortUnit(unit: string): string {
  return unit
    .replace(/^currency\s*/, "")
    .replace(/\s*\(.*\)$/, "")
    .trim();
}

export type Cell = { row: number; col: number };

export function NodeHeatmap({
  strikes,
  tenors,
  cells,
  selected,
  onSelect,
  caption,
  cornerLabel = "K / F",
  tones = ["short", "long"],
  notes,
  digits = 0,
}: {
  strikes: string[];
  tenors: string[];
  cells: (Estimate | null)[][];
  selected?: Cell | null;
  /** without it, the cells are figures to read, not buttons */
  onSelect?: (cell: Cell) => void;
  caption: string;
  cornerLabel?: string;
  /** what the two colours mean: [negative, positive] */
  tones?: [string, string];
  /** a second line per cell, as the cells (a node's delta: "52Δ") */
  notes?: (string | null)[][];
  digits?: number;
}) {
  const max = Math.max(1e-12, ...cells.flat().map((e) => (e && !isNoise(e) ? Math.abs(e.value) : 0)));
  return (
    <div>
      <table className={classes.heat}>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className={classes.corner}>
              {cornerLabel}
            </th>
            {tenors.map((t) => (
              <th scope="col" key={t}>
                {t}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {strikes.map((k, i) => (
            <tr key={k}>
              <th scope="row">{k}</th>
              {tenors.map((t, j) => {
                const e = cells[i]?.[j] ?? null;
                const note = notes?.[i]?.[j] ?? null;
                const sub = note ? <span className={classes.note}>{note}</span> : null;
                if (e === null || (e.value === 0 && !e.se)) {
                  return (
                    <td key={t}>
                      <span className={`${classes.cell} ${classes.empty}`}>
                        0{sub && " "}
                        {sub}
                      </span>
                    </td>
                  );
                }
                const f = formatEstimate(e, { digits, sign: true });
                const noise = isNoise(e);
                const tone = noise ? classes.noise : f.sign < 0 ? classes.neg : f.sign > 0 ? classes.pos : "";
                const isSelected = selected?.row === i && selected?.col === j;
                // at most 50 %: the strongest cell stays dark enough for the light text on it (WCAG AA)
                const share = `${Math.round(12 + (38 * Math.abs(e.value)) / max)}%`;
                // the name reads as the cell does, value then delta, so the visible text is part of it (WCAG 2.5.3)
                const shown = note ? `${f.value} ${note}` : f.value;
                const title = `${k} at ${t}: ${shown} ± ${f.se}${noise ? " (in the noise)" : ""}`;
                return (
                  <td key={t}>
                    {onSelect ? (
                      <button
                        type="button"
                        className={[classes.cell, tone, isSelected ? classes.selected : ""].join(" ")}
                        style={{ "--a": share } as CSSProperties}
                        aria-pressed={isSelected}
                        aria-label={`${k} at ${t}: ${shown} plus or minus ${f.se}${noise ? ", in the noise" : ""}`}
                        title={title}
                        onClick={() => onSelect({ row: i, col: j })}
                      >
                        {f.value}
                        {sub && " "}
                        {sub}
                      </button>
                    ) : (
                      <span
                        className={[classes.cell, tone].join(" ")}
                        style={{ "--a": share } as CSSProperties}
                        title={title}
                      >
                        {f.value}
                        {sub && " "}
                        {sub}
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className={classes.legend} aria-hidden="true">
        <span>
          <span className={`${classes.swatch} ${classes.swatchNeg}`} />
          {tones[0]}
        </span>
        <span>
          <span className={`${classes.swatch} ${classes.swatchPos}`} />
          {tones[1]}
        </span>
        <span>
          <span className={`${classes.swatch} ${classes.swatchNoise}`} />
          in the noise (|v| &lt; 2 SE)
        </span>
      </div>
    </div>
  );
}
