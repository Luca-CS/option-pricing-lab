// EOD runs (Risk › EOD runs): the book valued at each close on that day's market from the lake, with its Greeks,
// kept by what determines them (a backfill asked again reuses its runs). A risk manager backfills a period, which also
// explains each day's P&L; any run opens with its positions and market, and two runs compare side by side.
import { Anchor, Button, Checkbox, Group, Text, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { useRiskRuns } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useScreenJob } from "../../jobs/useScreenJob";
import { type Estimate, formatDate, formatSeconds } from "../../lib/format";
import { greekLabel, parseKey, rows } from "../../lib/greeks";
import { JobProgress } from "../../ui/Flow";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { COVERAGE, OriginBadge } from "../../ui/Markers";
import { Panel } from "../../ui/Panel";
import { GreeksTable } from "../../ui/Risk";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable, TradeLink } from "../common";

type Eod = Schemas["EodRun"];

function useEod(id: string | null) {
  return useQuery({
    queryKey: ["risk", "run", id],
    queryFn: async () =>
      (await ok(api.GET("/api/v1/risk/runs/{run_id}", { params: { path: { run_id: id ?? "" } } }))) as unknown as Eod,
    enabled: Boolean(id),
  });
}

/** The Greek of a trade or of the book, at no bucket. */
function greekOf(greeks: Record<string, Estimate> | null | undefined, greek: string): Estimate | undefined {
  const key = Object.keys(greeks ?? {}).find((k) => parseKey(k).greek === greek && parseKey(k).bucket === "");
  return key ? greeks?.[key] : undefined;
}

function Market({ provenance }: { provenance: Record<string, unknown> }) {
  return (
    <dl className={c.facts}>
      {Object.entries(provenance).map(([name, parts]) => (
        <div key={name} className={c.contents}>
          <dt>{name}</dt>
          <dd>
            {Object.entries((parts ?? {}) as Record<string, { origin?: string }>).map(([part, p]) => (
              <OriginBadge key={part} label={part} origin={p?.origin} to={COVERAGE} />
            ))}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Detail({ run }: { run: Eod }) {
  return (
    <>
      <KpiRow label={`The book at the close of ${run.day}`}>
        <Kpi label="Value" unit="currency" sub={`the close of ${formatDate(run.day)}`}>
          <Est value={run.value} signed digits={0} />
        </Kpi>
        <Kpi label="Trades alive" sub={`${run.groups?.length ?? 0} groups valued apart`}>
          <span className={c.mono}>{run.positions?.length ?? 0}</span>
        </Kpi>
        <Kpi label="Took" sub={run.code ?? ""}>
          <span className={c.mono}>{formatSeconds(run.seconds)}</span>
        </Kpi>
      </KpiRow>
      <div className={c.grid}>
        <Panel title="Positions" hint="value at the close and the reserve still held" flush>
          <DataTable
            caption={`The positions of ${run.day}`}
            head={["Trade", "Group", "Value ± SE", "Reserve", "Delta", "Vega"]}
            num={[2, 3, 4, 5]}
            rows={(run.positions ?? []).map((p) => ({
              key: p.trade_id,
              cells: [
                <TradeLink key="t" id={p.trade_id} />,
                p.group ?? "—",
                <Est key="v" value={p.value} signed digits={0} />,
                <Num key="r" value={p.reserve} />,
                <Est key="d" value={greekOf(p.greeks, "delta")} signed />,
                <Est key="g" value={greekOf(p.greeks, "vega")} signed />,
              ],
            }))}
          />
        </Panel>
        <Panel title="The book's Greeks" hint="each a full revaluation" flush>
          {run.greeks ? (
            <GreeksTable rows={rows(run.greeks)} caption={`The Greeks of the book at ${run.day}`} />
          ) : (
            <p className={`${c.faint} ${c.pad}`}>This run has no Greeks.</p>
          )}
        </Panel>
      </div>
      <Panel title="The market of the day" hint="what each part was made of">
        <Market provenance={run.provenance ?? {}} />
      </Panel>
    </>
  );
}

const COMPARED = ["delta", "gamma", "vega", "theta", "rho"];

function Compare({ a, b }: { a: Eod; b: Eod }) {
  const ids = [...new Set([...(a.positions ?? []), ...(b.positions ?? [])].map((p) => p.trade_id))];
  const at = (run: Eod, id: string) => run.positions?.find((p) => p.trade_id === id);
  const change = b.value.value - a.value.value;
  return (
    <>
      <KpiRow label="The two closes">
        <Kpi label={formatDate(a.day)} unit="currency" sub={a.run_id}>
          <Est value={a.value} signed digits={0} />
        </Kpi>
        <Kpi label={formatDate(b.day)} unit="currency" sub={b.run_id}>
          <Est value={b.value} signed digits={0} />
        </Kpi>
        <Kpi label="Change" unit="currency" sub="the second less the first">
          <Num value={change} signed />
        </Kpi>
      </KpiRow>
      <Text size="sm">
        <Anchor component={Link} to={`/trading/pnl?from=${a.day}&to=${b.day}`}>
          Explain the P&L from {formatDate(a.day)} to {formatDate(b.day)}, step by step
        </Anchor>
      </Text>
      <div className={c.grid}>
        <Panel title="Trades" hint="value at each close" flush>
          <DataTable
            caption="Each trade's value at the two closes"
            head={["Trade", formatDate(a.day), formatDate(b.day), "Change", ""]}
            num={[1, 2, 3]}
            rows={ids.map((id) => {
              const x = at(a, id);
              const y = at(b, id);
              return {
                key: id,
                cells: [
                  <TradeLink key="t" id={id} />,
                  x ? <Num key="a" value={x.value.value} signed /> : "—",
                  y ? <Num key="b" value={y.value.value} signed /> : "—",
                  x && y ? <Num key="c" value={y.value.value - x.value.value} signed /> : "—",
                  <span key="s" className={c.faint}>
                    {!x ? "new" : !y ? "ended" : ""}
                  </span>,
                ],
              };
            })}
          />
        </Panel>
        <Panel title="The book's Greeks" hint="at each close" flush>
          <DataTable
            caption="The book's Greeks at the two closes"
            head={["Greek", formatDate(a.day), formatDate(b.day), "Change"]}
            num={[1, 2, 3]}
            rows={COMPARED.filter((g) => greekOf(a.greeks, g) || greekOf(b.greeks, g)).map((g) => {
              const x = greekOf(a.greeks, g);
              const y = greekOf(b.greeks, g);
              return {
                key: g,
                cells: [
                  greekLabel(g),
                  <Est key="a" value={x} signed />,
                  <Est key="b" value={y} signed />,
                  x && y ? <Num key="c" value={y.value - x.value} signed /> : "—",
                ],
              };
            })}
          />
        </Panel>
      </div>
    </>
  );
}

function Backfill() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [greeks, setGreeks] = useState(true);
  const job = useScreenJob("eod", "EOD backfill", "job");
  const result = job.envelope?.result;
  const order = from && to && from > to;
  return (
    <Panel
      title="Backfill a period"
      hint="every business day's close, then each day's P&L explained; runs already stored are reused"
    >
      {job.running && job.tracked ? (
        <JobProgress job={job.tracked} label="EOD backfill" onCancel={() => void job.cancel()} />
      ) : (
        <Group gap="sm" align="flex-end" wrap="wrap">
          <TextInput type="date" label="From" value={from} onChange={(e) => setFrom(e.currentTarget.value)} w={170} />
          <TextInput
            type="date"
            label="To"
            value={to}
            onChange={(e) => setTo(e.currentTarget.value)}
            w={170}
            error={order ? "after the start" : undefined}
          />
          <Checkbox
            label="With the Greeks"
            checked={greeks}
            onChange={(e) => setGreeks(e.currentTarget.checked)}
            mb={8}
          />
          <Button
            size="sm"
            disabled={!from || !to || Boolean(order)}
            loading={job.start.isPending}
            onClick={() =>
              job.start.mutate(() =>
                ok(api.POST("/api/v1/risk/eod-runs", { body: { from, to, greeks, options: { precision: "quick" } } })),
              )
            }
          >
            Run the backfill
          </Button>
        </Group>
      )}
      {job.failed && <Failure error={new Error(job.failed)} title="The backfill failed" />}
      {job.start.error && <Failure error={job.start.error} title="Not started" />}
      {result && (
        <>
          <Text size="sm" mt="md">
            {result.runs.length} closes from {formatDate(result.from)} to {formatDate(result.to)},{" "}
            {result.runs.filter((r) => r.reused).length} reused.{" "}
            <Anchor component={Link} to={`/trading/pnl?from=${result.from}&to=${result.to}`} underline="always">
              The explained P&L of the period
            </Anchor>
          </Text>
          <DataTable
            caption="The closes of the backfill"
            head={["Day", "Run", "", "Value"]}
            num={[3]}
            rows={result.runs.map((r) => ({
              key: r.day,
              cells: [
                <span key="d" className={c.mono}>
                  {formatDate(r.day)}
                </span>,
                <span key="r" className={c.mono}>
                  {r.run_id}
                </span>,
                <span key="u" className={c.faint}>
                  {r.reused ? "reused" : "computed"}
                </span>,
                <Num key="v" value={r.value} signed />,
              ],
            }))}
          />
        </>
      )}
    </Panel>
  );
}

export default function EodRuns() {
  const me = useMe().data;
  const [params, setParams] = useSearchParams();
  const runs = useRiskRuns("eod");
  const items = runs.data?.items ?? [];
  // the picks are held here and the URL follows: the router applies a search change in a transition, which would
  // leave a ticked box unticked until it lands
  const inUrl = [params.get("a"), params.get("b")].filter((x): x is string => Boolean(x)).join(",");
  const [picks, setPicks] = useState(inUrl);
  useEffect(() => setPicks(inUrl), [inUrl]);
  const picked = picks ? picks.split(",") : [];
  const first = useEod(picked[0] ?? null);
  const second = useEod(picked[1] ?? null);
  const toggle = (id: string) => {
    const now = picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id].slice(-2);
    // the earlier close first
    const sorted = now
      .map((x) => ({ x, day: items.find((r) => r.run_id === x)?.day ?? "" }))
      .sort((p, q) => p.day.localeCompare(q.day))
      .map((p) => p.x);
    const next = new URLSearchParams(params);
    next.delete("a");
    next.delete("b");
    if (sorted[0]) next.set("a", sorted[0]);
    if (sorted[1]) next.set("b", sorted[1]);
    setPicks(sorted.join(","));
    setParams(next, { replace: true });
  };
  return (
    <SpacePage>
      {can(me, "eod_runs") && <Backfill />}
      {runs.isPending && <Loading />}
      {runs.error && <Failure error={runs.error} />}
      {runs.data && items.length === 0 && (
        <Empty title="No EOD run yet">
          A backfill values the book at each close of a period on the lake's market of that day; the explained P&L reads
          them.
        </Empty>
      )}
      {first.error && <Failure error={first.error} />}
      {first.data && !second.data && <Detail run={first.data} />}
      {first.data && second.data && <Compare a={first.data} b={second.data} />}
      {items.length > 0 && (
        <Panel
          title="Stored EOD runs"
          hint={
            picked.length ? "tick a second run to compare the two closes" : "tick a run to open it, two to compare them"
          }
          flush
        >
          <DataTable
            caption="Stored EOD runs, newest first"
            head={["", "Close", "Run", "Version", "Took", "Code"]}
            num={[3, 4]}
            rows={items.map((r) => ({
              key: r.run_id,
              cells: [
                <Checkbox
                  key="p"
                  size="xs"
                  checked={picked.includes(r.run_id)}
                  onChange={() => toggle(r.run_id)}
                  aria-label={`Pick ${r.run_id}, the close of ${r.day}`}
                />,
                <span key="d" className={c.mono}>
                  {formatDate(r.day)}
                </span>,
                <span key="r" className={c.mono}>
                  {r.run_id}
                </span>,
                r.version,
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
