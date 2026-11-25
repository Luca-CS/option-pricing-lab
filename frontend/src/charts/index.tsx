// The charts, loaded on demand: ECharts stays out of the first page's JavaScript (HANDOFF_REFACTOR_2_UX.md §7).
import { Skeleton } from "@mantine/core";
import { type ComponentProps, lazy, Suspense } from "react";
import type { CurveChart as C, LadderChart as L, SeriesChart as S, WaterfallChart as W } from "./charts";

const charts = () => import("./charts");
const Ladder = lazy(() => charts().then((m) => ({ default: m.LadderChart })));
const Waterfall = lazy(() => charts().then((m) => ({ default: m.WaterfallChart })));
const Curves = lazy(() => charts().then((m) => ({ default: m.CurveChart })));
const Lines = lazy(() => charts().then((m) => ({ default: m.SeriesChart })));

function Wait({ height = 260 }: { height?: number | undefined }) {
  return (
    <div role="status" aria-label="Loading the chart">
      <Skeleton height={height} radius="sm" />
    </div>
  );
}

export function LadderChart(props: ComponentProps<typeof L>) {
  return (
    <Suspense fallback={<Wait height={props.height} />}>
      <Ladder {...props} />
    </Suspense>
  );
}

export function WaterfallChart(props: ComponentProps<typeof W>) {
  return (
    <Suspense fallback={<Wait height={props.height} />}>
      <Waterfall {...props} />
    </Suspense>
  );
}

export function CurveChart(props: ComponentProps<typeof C>) {
  return (
    <Suspense fallback={<Wait height={props.height} />}>
      <Curves {...props} />
    </Suspense>
  );
}

export function SeriesChart(props: ComponentProps<typeof S>) {
  return (
    <Suspense fallback={<Wait height={props.height} />}>
      <Lines {...props} />
    </Suspense>
  );
}
