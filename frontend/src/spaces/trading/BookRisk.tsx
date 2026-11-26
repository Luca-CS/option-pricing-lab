// The book's risk (Trading › Book risk): the last Greeks run or a new one, read four ways. Greeks with their errors;
// vega by strike and tenor, where a node leads to the trades that carry it and to the surface behind it; the spot
// ladder under three vol rules; the signed scenarios. Every figure is a full revaluation of the book (optlab.risk).
import { Anchor, Group, Select, Tabs, Text } from "@mantine/core";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { LadderChart } from "../../charts";
import { useScreenJob } from "../../jobs/useScreenJob";
import { useStoredRun } from "../../jobs/useStoredRun";
import { type Estimate, formatNumber, isNoise } from "../../lib/format";
import { deltaNote, greekLabel, HEADLINE, ladderSeries, nodeDeltas, nodeGrid, parseKey, rows } from "../../lib/greeks";
import { Panel } from "../../ui/Panel";
import { type Cell, GreeksTable, NodeHeatmap, shortUnit } from "../../ui/Risk";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est } from "../../ui/Value";
import { commonClasses as c, DataTable, TradeLink } from "../common";
import { RunBar } from "../runs";
import { ScenariosView } from "../scenarios";

type Run = Schemas["RiskRun"];

function GreeksView({ run, trade }: { run: Run; trade: string | null }) {
  const book = run.book as Record<string, Estimate>;
  const positions = run.positions ?? [];
  const keys = [...HEADLINE].filter((g) =>
    Object.keys(book).some((k) => parseKey(k).greek === g && parseKey(k).bucket === ""),
  );
  return (
    <div className={c.grid}>
      <Panel title="The book's Greeks" hint="each a full revaluation; ± one SE" flush>
        <GreeksTable rows={rows(book)} units={run.units} caption="Greeks of the book" />
      </Panel>
      <Panel title="By trade" hint="delta per +1 % spot, vega per vol point, theta per business day" flush>
        <DataTable
          caption="Greeks by trade"
          head={["Trade", ...keys.filter((k) => ["delta", "vega", "theta"].includes(k)).map(greekLabel)]}
          num={[1, 2, 3]}
          rows={positions
            .filter((p) => !trade || p.name === trade)
            .map((p) => ({
              key: p.name,
              cells: [
                <TradeLink key="t" id={p.name} />,
                ...["delta", "vega", "theta"]
                  .filter((g) => keys.includes(g as (typeof HEADLINE)[number]))
                  .map((g) => {
                    const k = Object.keys(p.greeks ?? {}).find(
                      (x) => parseKey(x).greek === g && parseKey(x).bucket === "",
                    );
                    const e = k ? p.greeks?.[k] : undefined;
                    return e ? <Est key={g} value={e} signed /> : "—";
                  }),
              ],
            }))}
        />
      </Panel>
    </div>
  );
}

function VegaView({ run }: { run: Run }) {
  const [params, setParams] = useSearchParams();
  const book = run.book as Record<string, Estimate>;
  const names = [
    ...new Set(
      Object.keys(book)
        .filter((k) => k.startsWith("vega_node|"))
        .map((k) => parseKey(k).name),
    ),
  ];
  const name = params.get("name") ?? names[0] ?? "";
  if (!run.grid || names.length === 0) {
    return (
      <Empty title="No node vegas in this run">
        Run the Greeks with the strike × tenor grid (the default) to see them.
      </Empty>
    );
  }
  const grid = nodeGrid(book, name, run.grid);
  // the tenors where the book has no exposure at all are left out
  const cols = grid.tenors.map((_, j) => j).filter((j) => grid.cells.some((r) => (r[j]?.value ?? 0) !== 0));
  const shown = {
    strikes: grid.strikes,
    tenors: cols.map((j) => grid.tenors[j] ?? ""),
    cells: grid.cells.map((r) => cols.map((j) => r[j] ?? null)),
  };
  // each node read in delta too: its forward call delta on the surface of the run
  const deltas = nodeDeltas(run.groups as Record<string, unknown>[] | undefined, name);
  const notes = deltas ? grid.strikes.map((_, i) => cols.map((j) => deltaNote(deltas[i]?.[j]))) : undefined;
  const node = params.get("node");
  const [k, t] = node ? node.split("@") : [undefined, undefined];
  const selected: Cell | null = k && t ? { row: shown.strikes.indexOf(k), col: shown.tenors.indexOf(t) } : null;
  const key = k && t ? `vega_node|${name}|${k}@${t}` : null;
  const value = key ? book[key] : undefined;
  const exposed = key
    ? (run.positions ?? [])
        .flatMap((p) => {
          const e = p.greeks?.[key];
          return e && e.value !== 0 ? [{ name: p.name, e: e as Estimate }] : [];
        })
        .sort((a, b) => Math.abs(b.e.value) - Math.abs(a.e.value))
    : [];
  const select = (cell: Cell) => {
    const next = new URLSearchParams(params);
    next.set("node", `${shown.strikes[cell.row]}@${shown.tenors[cell.col]}`);
    setParams(next, { replace: true });
  };
  return (
    <div className={c.wide}>
      <Panel
        title="Vega by node"
        hint={`${shortUnit(String(run.units?.vega ?? "per vol point"))} at one node · ${run.grid.moneyness ?? "K / F(T)"}${notes ? " · each node's call delta below it" : ""}`}
        actions={
          names.length > 1 && (
            <Select
              size="xs"
              data={names}
              value={name}
              onChange={(v) => setParams({ view: "vega", name: v ?? names[0] ?? "" }, { replace: true })}
              aria-label="Name"
              allowDeselect={false}
            />
          )
        }
      >
        <NodeHeatmap
          strikes={shown.strikes}
          tenors={shown.tenors}
          cells={shown.cells}
          selected={selected && selected.row >= 0 && selected.col >= 0 ? selected : null}
          onSelect={select}
          notes={notes}
          caption={`Vega of the book on ${name}, by strike and tenor`}
        />
      </Panel>
      <Panel title={key ? `${name} · ${k} · ${t}` : "Pick a node"} hint={key ? "one vol point at this node only" : ""}>
        {!key && (
          <Text c="dimmed" size="sm">
            A node is one strike and one tenor of the surface. Click one to see which trades carry it.
          </Text>
        )}
        {key && value && (
          <>
            <div className={c.big}>
              <Est value={value} signed />
            </div>
            {isNoise(value) && (
              <Text size="sm" c="dimmed">
                Within two standard errors of zero: noise, not exposure.
              </Text>
            )}
            <Text fw={600} size="sm" mt="md" mb={6}>
              Trades exposed
            </Text>
            {exposed.length === 0 ? (
              <Text size="sm" c="dimmed">
                None of the trades moves with this node.
              </Text>
            ) : (
              <DataTable
                caption="Trades exposed to this node"
                head={["Trade", "Vega at the node"]}
                num={[1]}
                rows={exposed.map((x) => ({
                  key: x.name,
                  cells: [<TradeLink key="t" id={x.name} />, <Est key="e" value={x.e} signed />],
                }))}
              />
            )}
            <Group gap="xs" mt="md">
              <Anchor
                component={Link}
                to={`/quant/market/${encodeURIComponent(name)}?strike=${encodeURIComponent(k ?? "")}&tenor=${encodeURIComponent(t ?? "")}`}
                size="sm"
              >
                Open the surface at this node ↗
              </Anchor>
            </Group>
          </>
        )}
      </Panel>
    </div>
  );
}

function LadderView({ run }: { run: Schemas["LadderRun"] }) {
  const book = run.book as Record<string, Estimate>;
  const measures = [...new Set(Object.keys(book).map((k) => k.split("@")[0] ?? ""))].filter(Boolean);
  const [measure, setMeasure] = useState(measures.includes("delta") ? "delta" : (measures[0] ?? "delta"));
  const series = ladderSeries(book, measure, run.rules, run.points);
  return (
    <Panel
      title={`${greekLabel(measure)} against a spot shift`}
      hint="sticky moneyness, sticky strike, and the realistic rule (spot-vol betas from the lake)"
      actions={
        <Select
          size="xs"
          data={measures}
          value={measure}
          onChange={(v) => setMeasure(v ?? "delta")}
          aria-label="Measure"
          allowDeselect={false}
        />
      }
    >
      <LadderChart
        shifts={run.points}
        series={series}
        unit={measure}
        height={320}
        label={`${measure} of the book against a spot shift`}
      />
    </Panel>
  );
}

export default function BookRisk() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") ?? "greeks";
  const trade = params.get("trade");
  const greeksJob = useScreenJob("risk", "Greeks of the book", "job");
  const ladderJob = useScreenJob("ladder", "Ladder of the book", "ladderJob");
  const scenarioJob = useScreenJob("scenarios", "Scenarios on the book", "scenarioJob");
  const greeks = useStoredRun("risk", greeksJob.envelope?.result, params.get("run"));
  const ladder = useStoredRun("ladder", ladderJob.envelope?.result, params.get("ladder"));
  const scenarios = useStoredRun("scenarios", scenarioJob.envelope?.result, params.get("scenarios"));
  const failed = greeksJob.failed ?? ladderJob.failed ?? scenarioJob.failed;
  const setView = (v: string | null) => {
    const next = new URLSearchParams(params);
    next.set("view", v ?? "greeks");
    setParams(next, { replace: true });
  };
  return (
    <SpacePage>
      {trade && (
        <Text size="sm" c="dimmed">
          Showing {trade}'s contributions.{" "}
          <Anchor component={Link} to="/trading/risk">
            Show the whole book
          </Anchor>
        </Text>
      )}
      {failed && <Failure error={new Error(failed)} title="The run failed" />}
      <Tabs value={view} onChange={setView} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="greeks">Greeks</Tabs.Tab>
          <Tabs.Tab value="vega">Vega K × T</Tabs.Tab>
          <Tabs.Tab value="ladder">Ladder</Tabs.Tab>
          <Tabs.Tab value="scenarios">Scenarios</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="greeks" pt="md">
          <RunBar
            operation="greeks"
            label="Run the Greeks"
            run={greeks.run}
            job={greeksJob}
            submit={(p) => ok(api.POST("/api/v1/risk/greeks", { body: { options: { precision: p } } }))}
          />
          {greeks.pending && <Loading />}
          {greeks.none && !greeks.run && (
            <Empty title="No Greeks run on this book yet">Run them: a minute or so at the quick precision.</Empty>
          )}
          {greeks.run && <GreeksView run={greeks.run} trade={trade} />}
        </Tabs.Panel>
        <Tabs.Panel value="vega" pt="md">
          <RunBar
            operation="greeks"
            label="Run the Greeks"
            run={greeks.run}
            job={greeksJob}
            submit={(p) => ok(api.POST("/api/v1/risk/greeks", { body: { options: { precision: p } } }))}
          />
          {greeks.pending && <Loading />}
          {greeks.run && <VegaView run={greeks.run} />}
        </Tabs.Panel>
        <Tabs.Panel value="ladder" pt="md">
          <RunBar
            operation="ladder"
            label="Run the ladder"
            run={ladder.run}
            job={ladderJob}
            submit={(p) => ok(api.POST("/api/v1/risk/ladder", { body: { options: { precision: p } } }))}
          />
          {ladder.pending && <Loading />}
          {ladder.none && !ladder.run && (
            <Empty title="No ladder yet">The ladder revalues the book along a spot shift under three vol rules.</Empty>
          )}
          {ladder.run && <LadderView run={ladder.run} />}
        </Tabs.Panel>
        <Tabs.Panel value="scenarios" pt="md">
          <RunBar
            operation="run_scenarios"
            label="Run the scenarios"
            run={scenarios.run}
            job={scenarioJob}
            submit={(p) => ok(api.POST("/api/v1/risk/scenarios/runs", { body: { options: { precision: p } } }))}
          />
          {scenarios.pending && <Loading />}
          {scenarios.none && !scenarios.run && (
            <Empty title="No scenario run yet">
              The library is signed by Risk; anyone with the trader role can run it on the book.
            </Empty>
          )}
          {scenarios.run && <ScenariosView run={scenarios.run} />}
        </Tabs.Panel>
      </Tabs>
      <p className={c.faint}>{formatNumber(greeks.run?.positions?.length ?? 0)} positions in the last Greeks run.</p>
    </SpacePage>
  );
}
