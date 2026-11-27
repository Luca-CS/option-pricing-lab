// Historical VaR (Risk › VaR): the book revalued in full on each historical move of its window, 1 day, 10 days and a
// stressed year. For each: the distribution of the P&L with the VaR and the ES, the VaR day explained one factor at a
// time, what each trade contributes, the same VaR from the Greeks against the full revaluation, and the worst moves.
import { Anchor, Checkbox, NumberInput, Tabs, Text } from "@mantine/core";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { usePnl } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { CurveChart, DistributionChart, WaterfallChart } from "../../charts";
import { useScreenJob } from "../../jobs/useScreenJob";
import { useStoredRun } from "../../jobs/useStoredRun";
import { type Estimate, formatDate, formatNumber, formatPercent, formatSeconds } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { COVERAGE, OriginBadge } from "../../ui/Markers";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable, TradeLink, WhatIfSwitch } from "../common";
import { RunBar } from "../runs";

type Run = Schemas["VarRun"];
type Horizon = Schemas["VarHorizon"];

const FACTOR: Record<string, string> = {
  spot: "Spot",
  vol: "Vol",
  skew: "Skew",
  dividend: "Dividends",
  rates: "Rates",
  corr: "Correlation",
  fx: "FX",
  interaction: "Together",
};

const HORIZONS = [
  { key: "one_day", label: "1 day" },
  { key: "ten_day", label: "10 days" },
  { key: "stressed", label: "Stressed" },
] as const;

function Origins({ origins }: { origins: Record<string, unknown> | undefined }) {
  const entries = Object.entries(origins ?? {});
  if (entries.length === 0) return null;
  return (
    <span className={c.faint}>
      {entries.map(([part, o]) => (
        <OriginBadge key={part} label={part} origin={Array.isArray(o) ? o.join(", ") : String(o)} to={COVERAGE} />
      ))}
    </span>
  );
}

function HorizonView({ run, h }: { run: Run; h: Horizon }) {
  const values = h.pnl.map((e) => e.value);
  const var_ = h.var.value;
  const factors = [...(run.factors ?? []), "interaction"].filter((f) => h.factors?.[f]);
  // bridge 6: the VaR day's factor moves, then the desk's own explained P&L of that day when an EOD run covers it
  const pnl = usePnl();
  const explained = (pnl.data?.daily ?? []).some((d) => d.to === h.var_date);
  const contributions = Object.entries(h.contributions?.var ?? {})
    .map(([name, v]) => ({ name, var: v, es: h.contributions?.es?.[name] }))
    .sort((a, b) => Math.abs(b.var) - Math.abs(a.var));
  const worst = h.dates
    .flatMap((day, i): { day: string; pnl: Estimate }[] => {
      const pnl = h.pnl[i];
      return pnl ? [{ day, pnl }] : [];
    })
    .sort((a, b) => a.pnl.value - b.pnl.value)
    .slice(0, 10);
  const taylor = h.taylor;
  const pairs: [number, number][] = (taylor?.pnl ?? []).flatMap((t, i) => {
    const full = values[i];
    return full === undefined ? [] : [[full, t] as [number, number]];
  });
  const span = pairs.flat();
  const lo = Math.min(...span);
  const hi = Math.max(...span);
  return (
    <>
      <Text size="sm" c="dimmed">
        {h.n} historical moves from {formatDate(h.start)} to {formatDate(h.end)}
        {h.horizon > 1 ? `, over ${h.horizon} business days` : ""}, each a full revaluation of the book.{" "}
        {h.note ? `${h.note[0]?.toUpperCase()}${h.note.slice(1)}.` : ""} <Origins origins={h.origins} />
      </Text>
      {h.window && (
        <Text size="sm" c="dimmed">
          The stressed window, {formatDate(String(h.window.start))} to {formatDate(String(h.window.end))}, was found by{" "}
          {String(h.window.method ?? "a search")} over{" "}
          {((h.window.searched as string[] | undefined) ?? []).map(formatDate).join(" to ")}, then frozen (version{" "}
          {String(h.window.version ?? 1)}).
        </Text>
      )}
      <div className={c.grid}>
        <Panel title="The P&L over the window" hint={`desk side; ${h.n} moves; the VaR and the ES as losses`}>
          <DistributionChart
            values={values}
            marks={[
              { label: `VaR ${formatPercent(run.level, { digits: 0 })}`, value: -var_ },
              { label: `ES ${formatPercent(run.es_level, { digits: 1 })}`, value: -h.es },
            ]}
            tail={-var_}
            unit="P&L, currency"
            height={280}
            label={`The book's P&L over the ${h.label} window, with the VaR and the ES`}
          />
        </Panel>
        <Panel
          title={`The VaR day: ${formatDate(h.var_date)}`}
          hint="that day's move, one factor at a time"
          actions={
            explained && (
              <Anchor component={Link} to={`/trading/pnl?day=${h.var_date}`} size="xs" underline="always">
                The book's P&L explained that day
              </Anchor>
            )
          }
        >
          <WaterfallChart
            start={{ label: "base", value: 0 }}
            steps={factors.map((f) => ({ label: FACTOR[f] ?? f, value: h.factors?.[f]?.value ?? 0 }))}
            end={{ label: "the day", value: -var_ }}
            height={280}
            label={`The P&L of the VaR day ${h.var_date}, factor by factor`}
          />
        </Panel>
      </div>
      <div className={c.grid}>
        <Panel
          title="Contributions"
          hint="desk side; each trade's P&L on the VaR day, and its mean over the tail"
          flush
        >
          <DataTable
            caption="What each trade contributes to the VaR and the ES"
            head={["Trade", "On the VaR day", "In the tail (ES)"]}
            num={[1, 2]}
            rows={contributions.map((x) => ({
              key: x.name,
              cells: [
                <TradeLink key="t" id={x.name} />,
                <Num key="v" value={x.var} signed />,
                <Num key="e" value={x.es} signed />,
              ],
            }))}
          />
        </Panel>
        <Panel title="Against the Greeks" hint="the same moves through delta, gamma and vega">
          {taylor ? (
            <>
              <KpiRow label="The VaR from the Greeks">
                <Kpi label="VaR from the Greeks" unit="currency" sub={`full revaluation ${formatNumber(var_)}`}>
                  <Num value={taylor.var} />
                </Kpi>
                <Kpi label="ES from the Greeks" unit="currency" sub={`full revaluation ${formatNumber(h.es)}`}>
                  <Num value={taylor.es} />
                </Kpi>
                <Kpi label="Mean gap" unit="currency" sub="|full − Greeks|, per move">
                  <Num value={taylor.mean_abs_gap} />
                </Kpi>
              </KpiRow>
              {pairs.length > 0 && (
                <CurveChart
                  curves={[
                    { name: "moves", points: pairs, kind: "points" },
                    {
                      name: "equal",
                      points: [
                        [lo, lo],
                        [hi, hi],
                      ],
                    },
                  ]}
                  xName="full revaluation"
                  yName="Greeks"
                  xPercent={false}
                  yPercent={false}
                  digits={0}
                  height={240}
                  label="Each move's P&L from the Greeks against its full revaluation"
                />
              )}
            </>
          ) : (
            <Text size="sm" c="dimmed">
              This run has no Greeks check.
            </Text>
          )}
        </Panel>
      </div>
      <Panel title="The worst moves" hint="the ten largest losses of the window" flush>
        <DataTable
          caption="The ten worst historical moves of the window"
          head={["Day", "P&L ± SE", ""]}
          num={[1]}
          rows={worst.map((w) => ({
            key: w.day,
            cells: [
              <span key="d" className={c.mono}>
                {formatDate(w.day)}
              </span>,
              <Est key="p" value={w.pnl} signed digits={0} />,
              <span key="v" className={c.faint}>
                {w.day === h.var_date ? "the VaR day" : ""}
              </span>,
            ],
          }))}
        />
      </Panel>
    </>
  );
}

export default function Var() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const job = useScreenJob("var", "Historical VaR", "job");
  const stored = useStoredRun("var", job.envelope?.result, id ?? params.get("run"));
  const [days, setDays] = useState<number | string>("");
  const [stressed, setStressed] = useState(true);
  const [simulated, setSimulated] = useState(false);
  const [tenDay, setTenDay] = useState(true);
  const run = stored.run;
  const shown = HORIZONS.filter((x) => run?.[x.key]);
  const h = params.get("h") ?? "one_day";
  const pick = (v: string | null) => {
    const next = new URLSearchParams(params);
    next.set("h", v ?? "one_day");
    setParams(next, { replace: true });
  };
  const others = stored.runs.filter((r) => r.run_id !== run?.run_id);
  return (
    <SpacePage title={id ? `VaR run ${id}` : undefined}>
      <RunBar
        operation="var"
        label="Run the VaR"
        run={run as { run_id: string; seconds?: number | null; meta?: Record<string, unknown> } | null}
        job={job}
        submit={(precision) =>
          ok(
            api.POST("/api/v1/risk/var", {
              body: {
                options: { precision, include_simulated: simulated },
                stressed,
                ten_day: tenDay,
                ...(typeof days === "number" ? { days } : {}),
              },
            }),
          )
        }
      >
        <WhatIfSwitch checked={simulated} onChange={setSimulated} />
        <NumberInput
          size="xs"
          w={120}
          min={20}
          max={2500}
          value={days}
          onChange={setDays}
          placeholder="default"
          aria-label="Window, business days"
          rightSection={<span className={c.muted}>days</span>}
          rightSectionWidth={40}
        />
        <Checkbox
          size="xs"
          label="Stressed"
          checked={stressed}
          onChange={(e) => setStressed(e.currentTarget.checked)}
        />
        <Checkbox size="xs" label="10 days" checked={tenDay} onChange={(e) => setTenDay(e.currentTarget.checked)} />
      </RunBar>
      {job.failed && <Failure error={new Error(job.failed)} title="The VaR failed" />}
      {stored.error && <Failure error={stored.error} />}
      {stored.pending && <Loading />}
      {stored.none && !run && (
        <Empty title="No VaR run yet">
          A risk manager runs it: the book revalued on every historical move of the window, a few minutes on the CPU,
          about a minute on the GPU.
        </Empty>
      )}
      {run && (
        <>
          <KpiRow label="The VaR">
            <Kpi
              label={`1-day VaR ${formatPercent(run.level, { digits: 0 })}`}
              unit={run.currency ?? "currency"}
              sub={`VaR day ${formatDate(run.one_day.var_date)}`}
            >
              <Est value={run.one_day.var} digits={0} />
            </Kpi>
            <Kpi
              label={`1-day ES ${formatPercent(run.es_level, { digits: 1 })}`}
              unit={run.currency ?? "currency"}
              sub="the mean loss beyond"
            >
              <Num value={run.one_day.es} />
            </Kpi>
            {run.ten_day && (
              <Kpi label="10-day VaR" unit={run.currency ?? "currency"} sub="overlapping 10-day moves">
                <Est value={run.ten_day.var} digits={0} />
              </Kpi>
            )}
            {run.stressed && (
              <Kpi
                label="Stressed VaR"
                unit={run.currency ?? "currency"}
                sub={`${formatDate(run.stressed.start)} → ${formatDate(run.stressed.end)}`}
              >
                <Est value={run.stressed.var} digits={0} />
              </Kpi>
            )}
            <Kpi label="Revaluations" sub={`${formatSeconds(run.seconds)}, on ${formatDate(run.day)}'s book`}>
              <span className={c.mono}>{formatNumber(run.scenarios)}</span>
            </Kpi>
          </KpiRow>
          <Tabs value={shown.some((x) => x.key === h) ? h : "one_day"} onChange={pick} keepMounted={false}>
            <Tabs.List>
              {shown.map((x) => (
                <Tabs.Tab key={x.key} value={x.key}>
                  {x.label}
                </Tabs.Tab>
              ))}
            </Tabs.List>
            {shown.map((x) => {
              const horizon = run[x.key];
              return (
                <Tabs.Panel key={x.key} value={x.key} pt="md">
                  {horizon && (
                    <div className={c.stack}>
                      <HorizonView run={run} h={horizon} />
                    </div>
                  )}
                </Tabs.Panel>
              );
            })}
          </Tabs>
          <Text size="sm">
            <Anchor component={Link} to={`/risk/backtest?var_run=${encodeURIComponent(run.run_id)}`}>
              Backtest this VaR on the explained P&L
            </Anchor>
          </Text>
        </>
      )}
      {others.length > 0 && (
        <Panel title="Other VaR runs" hint="newest first" flush>
          <DataTable
            caption="Other stored VaR runs"
            head={["Run", "Book of", "Took", "Code"]}
            num={[2]}
            rows={others.map((r) => ({
              key: r.run_id,
              cells: [
                <Anchor key="r" component={Link} to={`/risk/var/${encodeURIComponent(r.run_id)}`} className={c.mono}>
                  {r.run_id}
                </Anchor>,
                formatDate(r.day),
                formatSeconds(r.seconds),
                <span key="c" className={c.faint}>
                  {r.code ?? "—"}
                </span>,
              ],
            }))}
          />
        </Panel>
      )}
    </SpacePage>
  );
}
