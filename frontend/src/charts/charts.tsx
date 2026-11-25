// The desk's charts on ECharts: a ladder (a measure against a spot shift, one line per vol rule), a waterfall (a
// value explained step by step: the P&L cascade, a desk price) and curves (smiles, term structures; quotes as points).
// Each takes the server's numbers as they are.
import { useCallback } from "react";
import { type Estimate, formatNumber, formatPercent } from "../lib/format";
import EChart, { base, type Colors, type Option } from "./EChart";

export type LadderSeries = { name: string; values: Estimate[] };

export function LadderChart({
  shifts,
  series,
  unit,
  height,
  label,
}: {
  /** spot shifts as fractions (−0.3 … +0.1) */
  shifts: number[];
  series: LadderSeries[];
  unit: string;
  height?: number;
  label: string;
}) {
  const build = useCallback(
    (c: Colors): Option => {
      const b = base(c);
      const palette = [c.accent, c.brand, c.muted, c.pos];
      return {
        ...b,
        grid: { ...(b.grid as object), left: 64, top: series.length > 1 ? 32 : 16 },
        xAxis: {
          ...(b.xAxis as object),
          type: "category",
          data: shifts.map((s) => formatPercent(s, { digits: 0, sign: true })),
          name: "spot shift",
          nameLocation: "middle",
          nameGap: 26,
        },
        yAxis: {
          ...(b.yAxis as object),
          type: "value",
          name: unit,
          axisLabel: {
            ...((b.yAxis as { axisLabel: object }).axisLabel ?? {}),
            formatter: (v: number) => formatNumber(v),
          },
        },
        legend: series.length > 1 ? b.legend : { show: false },
        tooltip: {
          valueFormatter: (v: number) => formatNumber(v),
        },
        series: series.map((s, i) => ({
          name: s.name,
          type: "line",
          data: s.values.map((e) => e.value),
          symbolSize: 6,
          lineStyle: { width: 2, color: palette[i % palette.length] },
          itemStyle: { color: palette[i % palette.length] },
          markLine:
            i === 0
              ? {
                  silent: true,
                  symbol: "none",
                  lineStyle: { color: c.faint, type: "dashed" },
                  data: [{ xAxis: shifts.indexOf(0) }],
                  label: { show: false },
                }
              : undefined,
        })),
      };
    },
    [shifts, series, unit],
  );
  return <EChart build={build} height={height} label={label} />;
}

export type Step = { label: string; value: number };

/** The floating bars of a waterfall, split by the side of zero they lie on (ECharts stacks each side apart): for each
 * bar, a transparent base then a visible part, above zero (`up`) and below (`down`). The first bar runs from zero to
 * the start, the last (if any) from zero to the end, the others from the running level to the next. */
export function waterfallBars(start: number, steps: number[], end?: number) {
  const spans: [number, number][] = [[0, start]];
  let level = start;
  for (const v of steps) {
    spans.push([level, level + v]);
    level += v;
  }
  if (end !== undefined) spans.push([0, end]);
  const upBase: number[] = [];
  const up: number[] = [];
  const downBase: number[] = [];
  const down: number[] = [];
  for (const [a, b] of spans) {
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    upBase.push(Math.max(lo, 0));
    up.push(hi > 0 ? hi - Math.max(lo, 0) : 0);
    downBase.push(Math.min(hi, 0));
    down.push(lo < 0 ? lo - Math.min(hi, 0) : 0);
  }
  return { upBase, up, downBase, down };
}

/** start, then each step added to it, then the end: the bars float, positive steps up, negative down. */
export function WaterfallChart({
  start,
  steps,
  end,
  digits = 0,
  height,
  label,
}: {
  start: Step;
  steps: Step[];
  end?: Step;
  digits?: number;
  height?: number;
  label: string;
}) {
  const build = useCallback(
    (c: Colors): Option => {
      const b = base(c);
      const labels = [start.label, ...steps.map((s) => s.label), ...(end ? [end.label] : [])];
      const bars = waterfallBars(
        start.value,
        steps.map((s) => s.value),
        end?.value,
      );
      const colours = [c.faint, ...steps.map((s) => (s.value >= 0 ? c.pos : c.neg)), ...(end ? [c.brand] : [])];
      const coloured = (values: number[]) => values.map((v, i) => ({ value: v, itemStyle: { color: colours[i] } }));
      const signed = [start.value, ...steps.map((s) => s.value), ...(end ? [end.value] : [])];
      return {
        ...b,
        grid: { ...(b.grid as object), left: 72, bottom: 64, top: 16 },
        xAxis: {
          ...(b.xAxis as object),
          type: "category",
          data: labels,
          axisLabel: { color: c.muted, fontSize: 11, interval: 0, rotate: labels.length > 6 ? 30 : 0 },
        },
        yAxis: {
          ...(b.yAxis as object),
          type: "value",
          axisLabel: {
            color: c.muted,
            fontFamily: c.mono,
            fontSize: 11,
            formatter: (v: number) => formatNumber(v, { digits }),
          },
        },
        tooltip: {
          trigger: "axis",
          formatter: (items: { dataIndex: number }[]) => {
            const i = items[0]?.dataIndex ?? 0;
            const sign = i > 0 && (!end || i < labels.length - 1);
            return `${labels[i]}: ${formatNumber(signed[i], { digits, sign })}`;
          },
        },
        // ECharts stacks positive and negative values apart: each bar is drawn on the side of zero it lies on, a
        // transparent part up to where it starts and a visible part to where it ends (both parts when it crosses zero)
        series: [
          { type: "bar", stack: "up", data: bars.upBase, itemStyle: { color: "transparent" }, silent: true },
          { type: "bar", stack: "up", data: coloured(bars.up), barMaxWidth: 36 },
          { type: "bar", stack: "down", data: bars.downBase, itemStyle: { color: "transparent" }, silent: true },
          { type: "bar", stack: "down", data: coloured(bars.down), barMaxWidth: 36, barGap: "-100%" },
        ],
      };
    },
    [start, steps, end, digits],
  );
  return <EChart build={build} height={height} label={label} />;
}

export type Curve = { name: string; points: [number, number][]; kind?: "line" | "points" };

/** Curves on numeric axes, levels as fractions shown in percent (a smile: moneyness and implied vol). */
export function CurveChart({
  curves,
  xName,
  yName,
  xPercent = true,
  yPercent = true,
  height,
  label,
}: {
  curves: Curve[];
  xName: string;
  yName: string;
  xPercent?: boolean;
  yPercent?: boolean;
  height?: number;
  label: string;
}) {
  const build = useCallback(
    (c: Colors): Option => {
      const b = base(c);
      const palette = [c.brand, c.accent, c.muted, c.pos, c.neg];
      const fmtX = (v: number) => (xPercent ? formatPercent(v, { digits: 0 }) : formatNumber(v, { digits: 2 }));
      const fmtY = (v: number) => (yPercent ? formatPercent(v, { digits: 1 }) : formatNumber(v, { digits: 2 }));
      return {
        ...b,
        grid: { ...(b.grid as object), left: 56, top: 32, bottom: 40 },
        xAxis: {
          ...(b.xAxis as object),
          type: "value",
          scale: true,
          name: xName,
          nameLocation: "middle",
          nameGap: 26,
          axisLabel: { color: c.muted, fontFamily: c.mono, fontSize: 11, formatter: fmtX },
        },
        yAxis: {
          ...(b.yAxis as object),
          type: "value",
          scale: true,
          name: yName,
          axisLabel: { color: c.muted, fontFamily: c.mono, fontSize: 11, formatter: fmtY },
        },
        tooltip: {
          trigger: "item",
          formatter: (p: { seriesName: string; value: [number, number] }) =>
            `${p.seriesName}: ${fmtX(p.value[0])}, ${fmtY(p.value[1])}`,
        },
        series: curves.map((cv, i) => ({
          name: cv.name,
          type: cv.kind === "points" ? "scatter" : "line",
          data: cv.kind === "points" ? cv.points : [...cv.points].sort((a, b) => a[0] - b[0]),
          showSymbol: cv.kind === "points",
          symbolSize: cv.kind === "points" ? 5 : 0,
          smooth: cv.kind !== "points",
          lineStyle: { width: 2, color: palette[i % palette.length] },
          itemStyle: { color: palette[i % palette.length] },
        })),
      };
    },
    [curves, xName, yName, xPercent, yPercent],
  );
  return <EChart build={build} height={height} label={label} />;
}

export type Series = { name: string; values: (number | null)[]; kind?: "line" | "bar" };

/** Values along labelled points (dates, tenors): lines or bars, one colour per series, signed bars in pos/neg. */
export function SeriesChart({
  x,
  series,
  unit,
  digits = 0,
  height,
  label,
  signedBars = false,
}: {
  x: string[];
  series: Series[];
  unit: string;
  digits?: number;
  height?: number;
  label: string;
  /** colour each bar by its sign (a P&L by day) */
  signedBars?: boolean;
}) {
  const build = useCallback(
    (c: Colors): Option => {
      const b = base(c);
      const palette = [c.accent, c.brand, c.muted, c.pos];
      return {
        ...b,
        grid: { ...(b.grid as object), left: 72, top: series.length > 1 ? 32 : 16, bottom: 40 },
        xAxis: {
          ...(b.xAxis as object),
          type: "category",
          data: x,
          axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true },
        },
        yAxis: {
          ...(b.yAxis as object),
          type: "value",
          name: unit,
          axisLabel: {
            color: c.muted,
            fontFamily: c.mono,
            fontSize: 11,
            formatter: (v: number) => formatNumber(v, { digits }),
          },
        },
        legend: series.length > 1 ? b.legend : { show: false },
        tooltip: { valueFormatter: (v: number) => formatNumber(v, { digits, sign: true }) },
        series: series.map((sr, i) => ({
          name: sr.name,
          type: sr.kind ?? "line",
          data: sr.values.map((v) =>
            sr.kind === "bar" && signedBars && v !== null
              ? { value: v, itemStyle: { color: v >= 0 ? c.pos : c.neg } }
              : v,
          ),
          showSymbol: false,
          lineStyle: { width: 2, color: palette[i % palette.length] },
          itemStyle: { color: palette[i % palette.length] },
          barMaxWidth: 18,
        })),
      };
    },
    [x, series, unit, digits, signedBars],
  );
  return <EChart build={build} height={height} label={label} />;
}
