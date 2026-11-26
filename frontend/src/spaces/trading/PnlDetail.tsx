// A day of the explained P&L opened (Trading › P&L explained, a day): spot, vol and skew under each spot-vol rule, and
// under one rule the vol move node by node (or tenor by tenor), each node a full revaluation on the server, read by
// maturity and by delta bucket. A day opened once is stored: it is read back, not run again.
import { Button, Group, SegmentedControl, Select, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ok, type Schemas } from "../../api/client";
import { useScreenJob } from "../../jobs/useScreenJob";
import { formatNumber } from "../../lib/format";
import { deltaNote } from "../../lib/greeks";
import { extremes, liveTenors, RULE_STEPS, SPOT_VOL_RULE, together, worstTenor } from "../../lib/pnlDetail";
import { Panel } from "../../ui/Panel";
import { NodeHeatmap } from "../../ui/Risk";
import { Failure } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable } from "../common";

type Detail = Schemas["PnlDetail"];
type Day = { from: string; to: string };
type Rule = NonNullable<Schemas["PnlDetailIn"]["rule"]>;
type By = NonNullable<Schemas["PnlDetailIn"]["detail"]>;
const isRule = (v: string): v is Rule => v in SPOT_VOL_RULE;
const isBy = (v: string): v is By => v === "node" || v === "tenor";
type Precision = "quick" | "standard";
const PATHS: Record<Precision, number> = { quick: 20_000, standard: 100_000 }; // the server's precisions (api.PRECISION)
const isPrecision = (v: string): v is Precision => v in PATHS;

const STEP: Record<string, string> = { spot: "Spot", vol: "Vol", skew: "Skew" };
const pts = (x: number | undefined) => formatNumber(typeof x === "number" ? 100 * x : null, { digits: 2, sign: true });

type Item = Schemas["RiskRunItem"];
const pathsOf = (r: Item) => Number(((r.params?.settings ?? {}) as Record<string, unknown>).n_paths ?? 0);

/** The stored opening of a day under a rule and a detail: at the paths asked for, else the most precise one. */
function useStored(day: Day, rule: string, detail: string, nPaths: number | null) {
  const runs = useQuery({
    queryKey: ["risk", "runs", "pnl_detail"],
    queryFn: () => ok(api.GET("/api/v1/risk/runs", { params: { query: { kind: "pnl_detail", limit: 200 } } })),
  });
  const items = (runs.data?.items ?? []).filter((r) => {
    const p = (r.params ?? {}) as Record<string, unknown>;
    return r.day === day.to && p.from === day.from && p.rule === rule && p.detail === detail;
  });
  const best = [...items].sort((a, b) => pathsOf(b) - pathsOf(a))[0];
  const item = (nPaths === null ? undefined : items.find((r) => pathsOf(r) === nPaths)) ?? best;
  const run = useQuery({
    queryKey: ["risk", "run", item?.run_id],
    queryFn: () => ok(api.GET("/api/v1/risk/runs/{run_id}", { params: { path: { run_id: item?.run_id ?? "" } } })),
    enabled: Boolean(item),
  });
  return {
    stored: (run.data ?? null) as Detail | null,
    storedPaths: item ? pathsOf(item) : null,
    refresh: () => runs.refetch(),
  };
}

function RulesTable({ d }: { d: Detail }) {
  const rules = Object.entries(d.rules ?? {});
  return (
    <Panel
      title="Spot, vol and skew under each spot-vol rule"
      hint="currency, each step a full revaluation on the seeds of the day before"
      flush
    >
      <DataTable
        caption={`The spot, vol and skew steps of ${d.to} under each spot-vol rule`}
        head={["Rule", ...RULE_STEPS.map((s) => STEP[s] ?? s), "Together", "Unexplained by the rule's Greeks"]}
        num={[1, 2, 3, 4, 5]}
        rows={rules.map(([rule, r]) => ({
          key: rule,
          cells: [
            rule === d.rule ? `${SPOT_VOL_RULE[rule] ?? rule} (opened below)` : (SPOT_VOL_RULE[rule] ?? rule),
            ...RULE_STEPS.map((s) => <Est key={s} value={r.steps?.[s]} signed digits={0} />),
            <Num key="t" value={together(r)} signed />,
            <Num key="u" value={RULE_STEPS.reduce((a, s) => a + (r.unexplained?.[s] ?? 0), 0)} signed />,
          ],
        }))}
      />
      <p className={`${c.faint} ${c.pad}`}>
        The three steps end on the day's surface under every rule, so they add up to the same amount: the rule decides
        how much of the move is spot and how much is vol. Sticky moneyness keeps the surface in moneyness, sticky strike
        keeps each strike's vol, realistic moves the at-the-money vols by the measured spot-vol betas.
        {Object.entries(d.unavailable ?? {}).map(([r, why]) => ` ${SPOT_VOL_RULE[r] ?? r}: ${why}.`)}
      </p>
    </Panel>
  );
}

function NameView({ d, name }: { d: Detail; name: string }) {
  const n = d.names?.[name];
  const strikes = (d.grid?.strikes as string[] | undefined) ?? [];
  const tenors = (d.grid?.tenors as string[] | undefined) ?? [];
  const buckets = (d.grid?.buckets as string[] | undefined) ?? [];
  if (!n) return null;
  const cols = liveTenors(tenors, n.skipped);
  const shown = cols.map((j) => tenors[j] ?? "");
  const rule = SPOT_VOL_RULE[d.rule] ?? d.rule;
  const { loss, gain } = extremes(n.nodes, strikes, tenors);
  const worst = worstTenor(n.by_tenor, tenors);
  const noteOf = (i: number, j: number) => deltaNote(n.deltas?.[i]?.[j]);
  const atm = strikes.indexOf("100%");
  return (
    <>
      {n.nodes && (
        <Panel
          title={`${name}: the vol move node by node`}
          hint={`currency, after the spot step under ${rule.toLowerCase()} · each node's call delta below it`}
        >
          <NodeHeatmap
            strikes={strikes}
            tenors={shown}
            cells={n.nodes.map((row) => cols.map((j) => row[j] ?? null))}
            notes={strikes.map((_, i) => cols.map((j) => noteOf(i, j)))}
            tones={["loss", "gain"]}
            caption={`The P&L of ${name}'s vol move on ${d.to}, by strike and tenor`}
          />
          <Text size="sm" mt="xs">
            {loss
              ? `Lost most: ${loss.strike} at ${loss.tenor} (${noteOf(loss.i, loss.j)}), ${formatNumber(loss.value, { sign: true })}. `
              : "No node lost. "}
            {gain
              ? `Made most: ${gain.strike} at ${gain.tenor} (${noteOf(gain.i, gain.j)}), ${formatNumber(gain.value, { sign: true })}.`
              : ""}
          </Text>
        </Panel>
      )}
      <Panel
        title={`${name}: by maturity`}
        hint="the level is the at-the-money move; the shape, the skew around it"
        flush
      >
        <DataTable
          caption={`${name}'s vol P&L of ${d.to} by maturity`}
          head={["Tenor", "ATM move, pts", "Level", "Shape (skew)", "Together", "Vega × ATM move"]}
          num={[1, 2, 3, 4, 5]}
          rows={cols.map((j) => ({
            key: tenors[j] ?? String(j),
            cells: [
              tenors[j] ?? "",
              pts(n.moves?.[atm]?.[j]),
              <Est key="l" value={n.level?.[j]} signed digits={0} />,
              typeof n.shape?.[j] === "number" ? (
                <Num key="s" value={n.shape[j] as number} signed />
              ) : (
                <Est key="s" value={n.shape?.[j] as Schemas["Estimate"] | undefined} signed digits={0} />
              ),
              <Num key="t" value={n.by_tenor?.[j]} signed />,
              <Num key="p" value={n.predicted_level?.[j]} signed />,
            ],
          }))}
        />
        <p className={`${c.faint} ${c.pad}`}>
          {worst ? `The maturity that lost most: ${worst.tenor}, ${formatNumber(worst.value, { sign: true })}. ` : ""}
          {(n.skipped?.length ?? 0) > 0 &&
            `Tenors beyond the book's last date (${(n.skipped ?? []).map((j) => tenors[j]).join(", ")}) move no value.`}
        </p>
      </Panel>
      {n.buckets && (
        <Panel title={`${name}: by delta bucket`} hint="each node split between the two deltas around it" flush>
          <DataTable
            caption={`${name}'s vol P&L of ${d.to} by delta bucket`}
            head={["Bucket", ...shown, "Together"]}
            num={[...shown.map((_, i) => i + 1), shown.length + 1]}
            rows={buckets.map((b, m) => ({
              key: b,
              cells: [
                b,
                ...cols.map((j) => formatNumber(n.buckets?.[m]?.[j], { sign: true })),
                <Num key="t" value={(n.buckets?.[m] ?? []).reduce((a, x) => a + x, 0)} signed />,
              ],
            }))}
          />
        </Panel>
      )}
    </>
  );
}

export function PnlDetailPanel({ day }: { day: Day }) {
  const [rule, setRule] = useState<Rule>("sticky_moneyness");
  const [detail, setDetail] = useState<By>("node");
  // the precision asked for; until then, that of the day's stored opening (standard when there is none)
  const [asked, setAsked] = useState<Precision | null>(null);
  const { stored, storedPaths, refresh } = useStored(day, rule, detail, asked ? PATHS[asked] : null);
  const precision: Precision = asked ?? (storedPaths === PATHS.quick ? "quick" : "standard");
  const job = useScreenJob("pnl_detail", `The P&L of ${day.to} opened`, "opened");
  const fresh = job.envelope?.result;
  // a day just opened is stored: the list of stored days is read again once its job is done
  useEffect(() => {
    if (fresh) void refresh();
  }, [fresh, refresh]);
  const same =
    fresh?.to === day.to && fresh.rule === rule && fresh.detail === detail && fresh.n_paths === PATHS[precision];
  const d = fresh && same ? fresh : stored;
  const atPrecision = d?.n_paths === PATHS[precision];
  const names = Object.keys(d?.names ?? {});
  const [picked, setPicked] = useState<string | null>(null);
  const name = picked && names.includes(picked) ? picked : names[0];
  const open = () =>
    job.start.mutate(() =>
      ok(
        api.POST("/api/v1/risk/pnl/detail", {
          body: { from: day.from, to: day.to, rule, detail, options: { precision } },
        }),
      ),
    );
  return (
    <>
      <Panel
        title="The vol move, opened"
        labelledBy="vol-move-opened"
        hint="which node, which maturity, which delta made or lost the vol and skew steps"
        actions={
          names.length > 1 && (
            <Select
              size="xs"
              data={names}
              value={name ?? null}
              onChange={setPicked}
              aria-label="Name"
              allowDeselect={false}
            />
          )
        }
      >
        <Group gap="sm" align="flex-end" wrap="wrap">
          <SegmentedControl
            size="xs"
            value={rule}
            onChange={(v) => isRule(v) && setRule(v)}
            aria-label="Spot-vol rule"
            data={Object.entries(SPOT_VOL_RULE).map(([value, label]) => ({ value, label }))}
          />
          <SegmentedControl
            size="xs"
            value={detail}
            onChange={(v) => isBy(v) && setDetail(v)}
            aria-label="Detail"
            data={[
              { value: "node", label: "Node by node" },
              { value: "tenor", label: "Tenor by tenor" },
            ]}
          />
          <SegmentedControl
            size="xs"
            value={precision}
            onChange={(v) => isPrecision(v) && setAsked(v)}
            aria-label="Precision"
            data={[
              { value: "quick", label: "Quick, 20,000 paths" },
              { value: "standard", label: "Standard, 100,000" },
            ]}
          />
          <Button size="xs" onClick={open} loading={job.running} disabled={job.running || atPrecision}>
            {atPrecision ? "Opened" : d ? "Open at this precision" : "Open the day"}
          </Button>
        </Group>
        <Text size="sm" c="dimmed" mt="xs">
          {d
            ? `${d.revaluations} revaluations of ${formatNumber(d.n_paths)} paths; the nodes and the residual (${formatNumber(d.residual?.value, { sign: true })}, the part of the move between the grid's nodes) add up to the vol and skew steps, ${formatNumber(d.vol_total, { sign: true })}.`
            : "Each node is a full revaluation of the book on the seeds of the day before: a minute or more, then stored."}
        </Text>
        {job.failed && <Failure error={new Error(job.failed)} title="The day could not be opened" />}
      </Panel>
      {d && <RulesTable d={d} />}
      {d && name && <NameView d={d} name={name} />}
    </>
  );
}
