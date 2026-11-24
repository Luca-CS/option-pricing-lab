// The one ECharts mount of the app (ADR-0024): loaded on demand, drawn on a canvas, themed from the desk's tokens and
// redrawn when the colour scheme changes. Tooltips are drawn on the canvas too (`renderMode: "richText"`): ECharts'
// HTML tooltips carry inline style attributes, which the page CSP refuses.
import { useComputedColorScheme } from "@mantine/core";
import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { type CSSProperties, useEffect, useRef } from "react";

echarts.use([
  LineChart,
  BarChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  AriaComponent,
  CanvasRenderer,
]);

export type Colors = {
  text: string;
  muted: string;
  faint: string;
  line: string;
  panel: string;
  accent: string;
  brand: string;
  pos: string;
  neg: string;
  mono: string;
  sans: string;
};

function read(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function colors(): Colors {
  return {
    text: read("--ol-text", "#e7e9ec"),
    muted: read("--ol-muted", "#a0a8b1"),
    faint: read("--ol-faint", "#7f8893"),
    line: read("--ol-line", "#2b3036"),
    panel: read("--ol-panel", "#1a1d21"),
    accent: read("--ol-accent", "#56c2de"),
    brand: read("--ol-brand", "#f0a93a"),
    pos: read("--ol-pos", "#5dbb8a"),
    neg: read("--ol-neg", "#e57a70"),
    mono: '"JetBrains Mono Variable", ui-monospace, monospace',
    sans: '"Inter Variable", system-ui, sans-serif',
  };
}

export type Option = echarts.EChartsCoreOption;

/** The axes, grid and tooltip every chart of the desk shares. */
export function base(c: Colors): Option {
  const axis = {
    axisLine: { lineStyle: { color: c.line } },
    axisTick: { lineStyle: { color: c.line } },
    axisLabel: { color: c.muted, fontFamily: c.mono, fontSize: 11 },
    splitLine: { lineStyle: { color: c.line, opacity: 0.6 } },
    nameTextStyle: { color: c.faint, fontFamily: c.sans, fontSize: 11 },
  };
  return {
    animationDuration: 300,
    textStyle: { fontFamily: c.sans, color: c.text },
    grid: { left: 56, right: 16, top: 28, bottom: 36, containLabel: false },
    xAxis: axis,
    yAxis: axis,
    legend: {
      textStyle: { color: c.muted, fontSize: 12 },
      icon: "roundRect",
      itemWidth: 12,
      itemHeight: 8,
      itemGap: 18,
      top: 0,
    },
    tooltip: {
      trigger: "axis",
      renderMode: "richText",
      backgroundColor: c.panel,
      borderColor: c.line,
      textStyle: { color: c.text, fontFamily: c.mono, fontSize: 12 },
    },
    aria: { enabled: true },
  };
}

export default function EChart({
  build,
  height = 260,
  label,
}: {
  /** the option, from the current colours */
  build: (c: Colors) => Option;
  height?: number;
  /** what the chart shows, for screen readers */
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const scheme = useComputedColorScheme("dark");

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const c = echarts.init(el, undefined, { renderer: "canvas" });
    chart.current = c;
    const resize = new ResizeObserver(() => c.resize());
    resize.observe(el);
    return () => {
      resize.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    // the scheme is read from the document's attribute, which Mantine sets: the colours follow it
    void scheme;
    const c = colors();
    const option = build(c);
    const tooltip = {
      ...((base(c).tooltip as object) ?? {}),
      ...((option.tooltip as object) ?? {}),
      renderMode: "richText",
    };
    chart.current?.setOption({ ...option, tooltip }, true);
  }, [build, scheme]);

  return <div ref={ref} role="img" aria-label={label} style={{ height, width: "100%" } as CSSProperties} />;
}
