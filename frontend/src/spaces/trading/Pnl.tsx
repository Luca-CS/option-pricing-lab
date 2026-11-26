// The explained P&L (Trading › P&L explained): the book's P&L from the EOD runs over a period, cumulated, explained
// step by step by full revaluations (time, spot, vol, skew, rates, dividends, correlation, FX), set against what the
// Greeks predicted, with what they leave unexplained, by day and by trade. The day that moved the book most, either way,
// is one click away: on a crisis day this is where a trader sees what moved it.
import { Anchor, Button, Group, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { SeriesChart, WaterfallChart } from "../../charts";
import { formatDate, formatNumber } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable, TradeLink } from "../common";
import { PnlDetailPanel } from "./PnlDetail";

type Day = Schemas["PnlDay"];

const STEP: Record<string, string> = {
  time: "Time",
  spot: "Spot",
  vol: "Vol",
  skew: "Skew",
  rates: "Rates",
  dividends: "Dividends",
  corr: "Correlation",
  fx: "FX",
};

function stepLabel(k: string): string {
  return STEP[k] ?? k;
}

/** The day whose P&L moved most, either way. */
export function largestDay(daily: { total: number }[]): number {
  let at = -1;
  for (let i = 0; i < daily.length; i++) {
    const d = daily[i];
    const best = at >= 0 ? daily[at] : undefined;
    if (d && (!best || Math.abs(d.total) > Math.abs(best.total))) at = i;
  }
  return at;
}

function DayDetail({ day, order }: { day: Day; order: string[] }) {
  const steps = day.steps ?? {};
  const predicted = day.predicted ?? {};
  const unexplained = day.unexplained ?? {};
  return (
    <div className={c.grid}>
      <Panel
        title={`${formatDate(day.to)}, step by step`}
        hint="each step a full revaluation, on the seeds of the day before"
      >
        <WaterfallChart
          start={{ label: "start", value: 0 }}
          steps={order.map((k) => ({ label: stepLabel(k), value: steps[k]?.[0] ?? 0 }))}
          end={{ label: "day", value: day.total }}
          height={280}
          label={`The P&L of ${day.to} explained step by step`}
        />
      </Panel>
      <Panel title="Against the Greeks' prediction" hint="currency, desk side" flush>
        <DataTable
          caption={`Steps of ${day.to} against the prediction of the Greeks`}
          head={["Step", "Revalued ± SE", "Predicted", "Unexplained"]}
          num={[1, 2, 3]}
          rows={order.map((k) => ({
            key: k,
            cells: [
              stepLabel(k),
              <Est key="r" value={{ value: steps[k]?.[0] ?? 0, se: steps[k]?.[1] || null }} signed digits={0} />,
              <Num key="p" value={predicted[k]} signed />,
              <Num key="u" value={unexplained[k]} signed />,
            ],
          }))}
        />
        <p className={`${c.faint} ${c.pad}`}>
          Reserve released that day: {formatNumber(day.release)}.{" "}
          {(day.new_trades?.length ?? 0) > 0 ? `New trades: ${(day.new_trades ?? []).join(", ")}.` : ""}
        </p>
      </Panel>
    </div>
  );
}

export default function Pnl() {
  const [params, setParams] = useSearchParams();
  const [from, setFrom] = useState(params.get("from") ?? "");
  const [to, setTo] = useState(params.get("to") ?? "");
  const pnl = useQuery({
    queryKey: ["risk", "pnl", params.get("from"), params.get("to")],
    queryFn: () =>
      ok(
        api.GET("/api/v1/risk/pnl", {
          params: { query: { from: params.get("from") || undefined, to: params.get("to") || undefined } },
        }),
      ),
  });
  const p = pnl.data;
  const daily = p?.daily ?? [];
  const order = p?.order ?? [];
  const largest = largestDay(daily);
  // a trade's page links here with ?trade=: its own P&L over the period comes first
  const tradeId = params.get("trade");
  const trade = tradeId ? p?.trades?.[tradeId] : undefined;
  const pickedDay = params.get("day");
  const dayAt = pickedDay ? daily.findIndex((d) => d.to === pickedDay) : largest;
  const day = dayAt >= 0 ? daily[dayAt] : undefined;
  const sum = (m: Record<string, number> | undefined) => Object.values(m ?? {}).reduce((a, b) => a + b, 0);
  const released = daily.reduce((a, d) => a + (d.release ?? 0), 0);
  const pick = (d: string) => {
    const next = new URLSearchParams(params);
    next.set("day", d);
    setParams(next, { replace: true });
  };

  return (
    <SpacePage>
      <Group gap="sm" align="flex-end">
        <TextInput type="date" label="From" value={from} onChange={(e) => setFrom(e.currentTarget.value)} w={170} />
        <TextInput type="date" label="To" value={to} onChange={(e) => setTo(e.currentTarget.value)} w={170} />
        <Button
          size="sm"
          variant="default"
          onClick={() =>
            setParams(Object.fromEntries(Object.entries({ from, to }).filter(([, v]) => v)), { replace: true })
          }
        >
          Show
        </Button>
        {p?.from && (
          <span className={c.faint}>
            Stored from {formatDate(p.from)} to {formatDate(p.to)}, {p.days?.length ?? 0} days.
          </span>
        )}
      </Group>
      {pnl.isPending && <Loading />}
      {pnl.error && <Failure error={pnl.error} />}
      {p && daily.length === 0 && (
        <Empty
          title="No explained P&L yet"
          action={
            <Anchor component={Link} to="/risk/runs">
              EOD runs (Risk)
            </Anchor>
          }
        >
          {p.note ?? "The explained P&L reads the EOD runs."} A risk manager runs the backfill over a period; each day
          is then a full revaluation of the book, step by step.
        </Empty>
      )}
      {p && daily.length > 0 && (
        <>
          <KpiRow label="The period">
            <Kpi label="P&L" unit="currency" sub={`${formatDate(p.from)} → ${formatDate(p.to)}`}>
              <Num value={p.total} signed />
            </Kpi>
            <Kpi label="The Greeks predicted" unit="currency" sub="delta, gamma, vega, theta… step by step">
              <Num value={sum(p.predicted)} signed />
            </Kpi>
            <Kpi label="Unexplained" unit="currency" sub="what the Greeks miss: convexity, cross terms">
              <Num value={sum(p.unexplained)} signed />
            </Kpi>
            <Kpi label="Reserve released" unit="currency" sub="as the trades' lives run">
              <Num value={released} />
            </Kpi>
            {daily[largest] && (
              <Kpi label="Largest day" unit="currency" sub={formatDate(daily[largest].to)}>
                <Num value={daily[largest].total} signed />
              </Kpi>
            )}
          </KpiRow>
          {tradeId && (
            <Panel title={`${tradeId} over the period, step by step`} hint="currency, desk side">
              {trade ? (
                <WaterfallChart
                  start={{ label: "start", value: 0 }}
                  steps={order.map((k) => ({ label: stepLabel(k), value: trade.steps?.[k] ?? 0 }))}
                  end={{ label: tradeId, value: trade.total ?? 0 }}
                  height={240}
                  label={`The P&L of ${tradeId} over the period explained step by step`}
                />
              ) : (
                <p className={c.muted}>
                  {tradeId} has no explained P&L between {formatDate(p.from)} and {formatDate(p.to)}: it was not in the
                  book on those days, or no EOD run covers them.
                </p>
              )}
            </Panel>
          )}
          <div className={c.grid}>
            <Panel title="Cumulated, and day by day" hint="currency, desk side">
              <SeriesChart
                x={p.days ?? []}
                series={[
                  { name: "cumulated", values: p.curve ?? [], kind: "line" },
                  { name: "day", values: daily.map((d) => d.total), kind: "bar" },
                ]}
                unit="currency"
                signedBars
                height={280}
                label="The book's P&L cumulated over the period, and each day's"
              />
            </Panel>
            <Panel title="The period, step by step" hint="the cascade, cumulated">
              <WaterfallChart
                start={{ label: "start", value: 0 }}
                steps={order.map((k) => ({ label: stepLabel(k), value: p.steps?.[k] ?? 0 }))}
                end={{ label: "period", value: p.total ?? 0 }}
                height={280}
                label="The period's P&L explained step by step"
              />
            </Panel>
          </div>
          {day && <DayDetail day={day} order={order} />}
          {day && <PnlDetailPanel key={day.to} day={{ from: day.from, to: day.to }} />}
          <Panel title="Day by day" hint="click a day to explain it" flush>
            <DataTable
              caption="The P&L of each day, by step"
              head={["Day", "P&L", ...order.map(stepLabel), "Unexplained", ""]}
              num={[1, ...order.map((_, i) => i + 2), order.length + 2]}
              rows={daily.map((d, i) => ({
                key: d.to,
                cells: [
                  <Anchor key="d" component="button" type="button" onClick={() => pick(d.to)} className={c.mono}>
                    {formatDate(d.to)}
                  </Anchor>,
                  <Num key="t" value={d.total} signed />,
                  ...order.map((k) => formatNumber(d.steps?.[k]?.[0], { sign: true })),
                  formatNumber(sum(d.unexplained), { sign: true }),
                  i === largest ? (
                    <span key="w" className={c.faint}>
                      largest
                    </span>
                  ) : (
                    ""
                  ),
                ],
              }))}
            />
          </Panel>
          <Panel title="By trade" hint="desk side, over the period" flush>
            <DataTable
              caption="The period's P&L by trade"
              head={["Trade", "P&L", ...order.map(stepLabel)]}
              num={[1, ...order.map((_, i) => i + 2)]}
              rows={Object.entries(p.trades ?? {}).map(([id, t]) => ({
                key: id,
                cells: [
                  <TradeLink key="id" id={id} />,
                  <Num key="t" value={t.total} signed />,
                  ...order.map((k) => formatNumber(t.steps?.[k], { sign: true })),
                ],
              }))}
            />
          </Panel>
        </>
      )}
    </SpacePage>
  );
}
