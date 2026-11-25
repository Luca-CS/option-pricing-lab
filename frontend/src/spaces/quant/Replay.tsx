// The replay lab (Quant › Replay lab, and a replay's page `/quant/replay/:id`): a product struck on a past date and
// lived on real closes: its value and cash at each valuation, its events, each name's performance since the strike,
// the P&L explained by the Greeks with what they leave, and the reserve released. The stored replays are listed, the
// desk's and the repository's.
import { Anchor, Button, Group, NumberInput, Select, Text, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { SeriesChart, WaterfallChart } from "../../charts";
import { useScreenJob } from "../../jobs/useScreenJob";
import { formatDate, formatNumber, formatPercent, formatSeconds } from "../../lib/format";
import { JobProgress } from "../../ui/Flow";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Num } from "../../ui/Value";
import { commonClasses as c, DataTable } from "../common";

type Run = Schemas["Replay"];

const STEP: Record<string, string> = { time: "Time", delta: "Delta", gamma: "Gamma", vega: "Vega", reserve: "Reserve" };

/** The cash paid by each date: the events' amounts whose payment falls on or before it. */
export function cashPaid(
  dates: string[],
  events: { date: string; payment?: string | null; amount?: number | null }[],
): number[] {
  return dates.map((d) => events.reduce((acc, e) => ((e.payment ?? e.date) <= d ? acc + (e.amount ?? 0) : acc), 0));
}

function ReplayView({ run }: { run: Run }) {
  const days = run.daily ?? [];
  const names = run.underlyings ?? [];
  const rec = run.reconciliation as Record<string, number | undefined>;
  const ex = run.explain as {
    sum?: Record<string, number>;
    sum_pnl?: number;
    sum_residual?: number;
    residual_share?: number;
  };
  const steps = Object.entries(ex.sum ?? {});
  const inception = run.inception as { strike?: string; policy?: string; coupon?: { value?: number } };
  return (
    <>
      <KpiRow label="The replay">
        <Kpi label="Struck" sub={run.trade_id}>
          <span className={c.mono}>{formatDate(inception.strike)}</span>
        </Kpi>
        <Kpi label="P&L over the life" unit="per 100" sub={`${run.n_valuations ?? days.length} valuations`}>
          <Num value={rec.total_pnl} signed digits={2} />
        </Kpi>
        <Kpi label="Cash paid" unit="per 100" sub={`${(run.events ?? []).length} events`}>
          <Num value={rec.cash_booked} digits={2} />
        </Kpi>
        <Kpi
          label="Reserve released"
          unit="per 100"
          sub={`of ${formatNumber(rec.reserve_start, { digits: 2 })} at issue`}
        >
          <Num value={rec.reserve_released} digits={2} />
        </Kpi>
        <Kpi label="Unexplained" sub="the share of the P&L the Greeks leave">
          <span className={c.mono}>{formatPercent(ex.residual_share ?? null, { digits: 0 })}</span>
        </Kpi>
      </KpiRow>
      {inception.policy && (
        <Text size="sm" c="dimmed">
          Priced at the strike by {inception.policy}
          {inception.coupon?.value !== undefined ? `, coupon ${formatPercent(inception.coupon.value)} p.a.` : ""}.
        </Text>
      )}
      <div className={c.grid}>
        <Panel title="Value and cash" hint="per 100 of notional, at each valuation">
          <SeriesChart
            x={days.map((d) => d.date)}
            series={[
              { name: "value", values: days.map((d) => d.value ?? null), kind: "line" },
              {
                name: "cash paid",
                values: cashPaid(
                  days.map((d) => d.date),
                  run.events ?? [],
                ),
                kind: "line",
              },
            ]}
            unit="per 100"
            digits={1}
            height={260}
            label="The product's value and the cash paid so far over its life"
          />
        </Panel>
        <Panel title="Since the strike" hint="each name's close over its strike level">
          <SeriesChart
            x={days.map((d) => d.date)}
            series={names.map((n) => ({
              name: n,
              values: days.map((d) => d.perf?.[n] ?? null),
              kind: "line" as const,
            }))}
            unit="close / strike"
            digits={2}
            height={260}
            label="Each name's performance since the strike"
          />
        </Panel>
      </div>
      <div className={c.grid}>
        <Panel title="The P&L explained" hint="the Greeks' sum over the life, and what they leave">
          <WaterfallChart
            start={{ label: "issue", value: 0 }}
            steps={[
              ...steps.map(([k, v]) => ({ label: STEP[k] ?? k, value: v })),
              { label: "Unexplained", value: ex.sum_residual ?? 0 },
            ]}
            end={{ label: "life", value: ex.sum_pnl ?? 0 }}
            digits={2}
            height={260}
            label="The P&L over the life explained by the Greeks"
          />
        </Panel>
        <Panel title="Events" hint="what the product paid, and when it ended" flush>
          <DataTable
            caption="The product's events"
            head={["Day", "Event", "Amount", "Paid"]}
            num={[2]}
            rows={(run.events ?? []).map((e, i) => ({
              key: `${e.date}-${e.kind}-${i}`,
              cells: [
                <span key="d" className={c.mono}>
                  {formatDate(e.date)}
                </span>,
                e.kind.replaceAll("_", " "),
                formatNumber(e.amount, { digits: 2 }),
                formatDate(e.payment),
              ],
            }))}
          />
        </Panel>
      </div>
      <Panel title="Day by day" hint="value ± SE, the day's P&L and its explanation" flush>
        <DataTable
          caption="Each valuation of the replay"
          head={["Day", "Value", "± SE", "P&L", ...Object.keys(STEP).map((k) => STEP[k] ?? k), "Unexplained"]}
          num={[1, 2, 3, 4, 5, 6, 7, 8, 9]}
          rows={days.map((d) => ({
            key: d.date,
            cells: [
              <span key="d" className={c.mono}>
                {formatDate(d.date)}
              </span>,
              formatNumber(d.value, { digits: 2 }),
              formatNumber(d.se, { digits: 2 }),
              <Num key="p" value={d.pnl} signed digits={2} />,
              ...Object.keys(STEP).map((k) => formatNumber(d.explain?.[k], { digits: 2, sign: true })),
              formatNumber(d.residual, { digits: 2, sign: true }),
            ],
          }))}
        />
      </Panel>
    </>
  );
}

export default function Replay() {
  const me = useMe().data;
  const { id } = useParams();
  const [params] = useSearchParams();
  const runs = useQuery({
    queryKey: ["quant", "replay", "runs"],
    queryFn: () => ok(api.GET("/api/v1/quant/replay/runs")),
  });
  const stored = useQuery({
    queryKey: ["quant", "replay", "run", id],
    queryFn: () => ok(api.GET("/api/v1/quant/replay/runs/{run_id}", { params: { path: { run_id: id ?? "" } } })),
    enabled: Boolean(id),
  });
  const job = useScreenJob("replay", "Replay", "job");
  const [preset, setPreset] = useState<string | null>(null);
  const [underlying, setUnderlying] = useState("SPX");
  const [strike, setStrike] = useState("");
  const [model, setModel] = useState<"lv" | "slv">("lv");
  const [every, setEvery] = useState<number | string>(5);
  const [paths, setPaths] = useState<number | string>(20_000);
  const r = runs.data;
  const run = job.envelope?.result ?? stored.data ?? null;
  const trade = params.get("trade");
  return (
    <SpacePage title={id ? `Replay ${id}` : undefined}>
      {trade && (
        <Text size="sm" c="dimmed">
          A replay starts from a preset and a strike date: pick the preset of {trade}'s kind, struck on a past date.
        </Text>
      )}
      {r && !r.available && (
        <Empty title="No closes in this lake">A replay lives the product on the lake's closes.</Empty>
      )}
      {r?.available && can(me, "replay_run") && !id && (
        <Panel title="Replay a product" hint="struck on a past date, then valued every few business days on the closes">
          {job.running && job.tracked ? (
            <JobProgress job={job.tracked} label="Replay" onCancel={() => void job.cancel()} />
          ) : (
            <Group gap="sm" align="flex-end" wrap="wrap">
              <Select
                label="Preset"
                size="sm"
                w={240}
                data={(r.presets ?? []).map((p) => ({ value: p, label: p.replaceAll("_", " ") }))}
                value={preset ?? r.presets?.[0] ?? null}
                onChange={setPreset}
                allowDeselect={false}
              />
              <TextInput
                label="Name"
                size="sm"
                w={100}
                value={underlying}
                onChange={(e) => setUnderlying(e.currentTarget.value.toUpperCase())}
              />
              <TextInput
                type="date"
                label="Strike date"
                size="sm"
                w={170}
                value={strike}
                onChange={(e) => setStrike(e.currentTarget.value)}
              />
              <Select
                label="Model"
                size="sm"
                w={150}
                data={[
                  { value: "lv", label: "Local vol" },
                  { value: "slv", label: "SLV" },
                ]}
                value={model}
                onChange={(v) => setModel((v ?? "lv") as "lv" | "slv")}
                allowDeselect={false}
              />
              <NumberInput
                label="Every, business days"
                size="sm"
                w={150}
                min={1}
                max={60}
                value={every}
                onChange={setEvery}
              />
              <NumberInput
                label="Paths"
                size="sm"
                w={120}
                min={1000}
                max={200_000}
                step={5000}
                value={paths}
                onChange={setPaths}
              />
              <Button
                size="sm"
                disabled={!strike}
                loading={job.start.isPending}
                onClick={() =>
                  job.start.mutate(() =>
                    ok(
                      api.POST("/api/v1/quant/replay/runs", {
                        body: {
                          preset: preset ?? r.presets?.[0] ?? "barrier_reverse_convertible",
                          underlying,
                          strike,
                          model,
                          every: typeof every === "number" ? every : 5,
                          n_paths: typeof paths === "number" ? paths : 20_000,
                        },
                      }),
                    ),
                  )
                }
              >
                Replay
              </Button>
            </Group>
          )}
          {job.failed && <Failure error={new Error(job.failed)} title="The replay failed" />}
          {job.start.error && <Failure error={job.start.error} title="Not started" />}
        </Panel>
      )}
      {stored.isPending && id && <Loading />}
      {stored.error && <Failure error={stored.error} />}
      {run && <ReplayView run={run} />}
      <Panel title="Stored replays" hint="the desk's runs, and the repository's versioned ones" flush>
        {runs.isPending && <Loading lines={2} />}
        {(r?.runs ?? []).length === 0 && !runs.isPending && <p className={`${c.faint} ${c.pad}`}>No replay yet.</p>}
        <DataTable
          caption="Stored replays"
          head={["Replay", "Kind", "Names", "Strike", "Valuations", "Events", "Took"]}
          num={[4, 5, 6]}
          rows={(r?.runs ?? []).map((x) => ({
            key: x.id,
            cells: [
              <Anchor key="id" component={Link} to={`/quant/replay/${encodeURIComponent(x.id)}`} className={c.mono}>
                {x.id}
              </Anchor>,
              x.kind,
              (x.underlyings ?? []).join(", "),
              formatDate(x.strike),
              x.n_valuations ?? "—",
              x.events ?? "—",
              formatSeconds(x.seconds),
            ],
          }))}
        />
      </Panel>
    </SpacePage>
  );
}
