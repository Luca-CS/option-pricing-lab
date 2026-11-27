// Trading › Hedging (ADR-0025): the book's hedges are trades. The blotter lists them, with the what-ifs to promote or
// discard, the futures to roll and the expired ones to settle; the ticket books one, its form built from the server's
// description of each instrument type and marked at the market in use first; the listed chain picks a vanilla; the
// optimizer proposes the hedges that bring the book's Greeks to their targets at the least cost, with the grid and the
// VaR before and after, and books its proposal in one call.
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Tabs,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { useMarketContext, useTrades } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useScreenJob } from "../../jobs/useScreenJob";
import { formatDate, formatNumber, formatPercent } from "../../lib/format";
import { type FieldSpec, initialValues, instrumentBody, missing, type Values } from "../../lib/hedges";
import { type DeskSide, signedQuantity } from "../../lib/side";
import { JobProgress } from "../../ui/Flow";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { NodeHeatmap } from "../../ui/Risk";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable, DeskSideBadge, SideSize, TradeLink } from "../common";

type Trade = Schemas["Trade"];
type TypeSpec = Schemas["InstrumentType"];
type Mark = Schemas["InstrumentMark"];
type Run = Schemas["HedgeRun"];

const PRECISION = [
  { value: "quick", label: "Quick" },
  { value: "standard", label: "Standard" },
  { value: "precise", label: "Precise" },
];
const FAMILIES = [
  { value: "delta", label: "Delta per name" },
  { value: "vega_tenor", label: "Vega per tenor" },
  { value: "rho_tenor", label: "Rho per tenor" },
  { value: "dividend_year", label: "Dividends per year" },
  { value: "funding_tenor", label: "Funding per tenor" },
  { value: "gamma", label: "Gamma" },
];

function useHedgeTypes() {
  return useQuery({
    queryKey: ["trading", "hedge-types"],
    queryFn: () => ok(api.GET("/api/v1/trading/hedges/types")),
    staleTime: 60 * 60_000,
  });
}

// --- the blotter -------------------------------------------------------------------------------------------------
function Blotter({ trades }: { trades: Trade[] }) {
  const me = useMe().data;
  const valuation = useMarketContext().data?.valuation;
  const queries = useQueryClient();
  const act = useMutation({
    mutationFn: ({ id, what }: { id: string; what: "promote" | "discard" | "roll" | "settle" }) => {
      const params = { path: { trade_id: id } };
      if (what === "promote") return ok(api.POST("/api/v1/trading/trades/{trade_id}/promote", { params }));
      if (what === "discard") return ok(api.POST("/api/v1/trading/trades/{trade_id}/discard", { params }));
      if (what === "roll") return ok(api.POST("/api/v1/trading/trades/{trade_id}/roll", { params }));
      return ok(api.POST("/api/v1/trading/trades/{trade_id}/settle", { params }));
    },
    onSuccess: (_, v) => {
      void queries.invalidateQueries({ queryKey: ["trading"] });
      notifications.show({ color: "accent", title: `${v.id}: ${v.what} done`, message: "The blotter shows it." });
    },
  });
  if (!trades.length) {
    return <Empty title="No hedge in the book yet">Book one from the ticket, or let the optimizer propose them.</Empty>;
  }
  return (
    <Panel title="The hedges" hint="desk side; a what-if counts only where a run includes it" flush>
      {act.error && <Failure error={act.error} title="Not done" />}
      <DataTable
        caption="The book's hedges"
        head={["Trade", "Instrument", "Side", "Quantity", "Status", "Maturity", "Fill per unit", "Fees", ""]}
        num={[3, 6, 7]}
        rows={trades.map((t) => {
          const type = t.summary?.type ?? "";
          const maturity = t.summary?.maturity ?? null;
          const expired = Boolean(valuation && maturity && maturity <= valuation);
          const product = t.product as { fill?: number; fees?: number };
          return {
            key: t.trade_id,
            cells: [
              <TradeLink key="id" id={t.trade_id} />,
              t.summary?.label ?? type,
              <DeskSideBadge key="side" trade={t} />,
              <Num key="q" value={t.quantity} digits={2} sign />,
              <Badge
                key="s"
                variant="light"
                color={t.status === "simulated" ? "amber" : t.status === "live" ? "accent" : "gray"}
              >
                {t.status === "simulated" ? "what-if" : t.status}
              </Badge>,
              maturity ? formatDate(maturity) : "—",
              <Num key="f" value={product.fill ?? 0} digits={2} />,
              <Num key="fees" value={product.fees ?? 0} digits={2} />,
              <Group key="a" gap={4} wrap="nowrap">
                {t.status === "simulated" && can(me, "promote_trade") && (
                  <Button size="compact-xs" onClick={() => act.mutate({ id: t.trade_id, what: "promote" })}>
                    Promote
                  </Button>
                )}
                {t.status === "simulated" && can(me, "discard_trade") && (
                  <Button
                    size="compact-xs"
                    variant="default"
                    onClick={() => act.mutate({ id: t.trade_id, what: "discard" })}
                  >
                    Discard
                  </Button>
                )}
                {t.status === "live" && type === "future" && !expired && can(me, "roll_trade") && (
                  <Button
                    size="compact-xs"
                    variant="default"
                    onClick={() => act.mutate({ id: t.trade_id, what: "roll" })}
                  >
                    Roll
                  </Button>
                )}
                {t.status === "live" &&
                  expired &&
                  (type === "future" || type === "vanilla") &&
                  can(me, "settle_trade") && (
                    <Button
                      size="compact-xs"
                      variant="default"
                      onClick={() => act.mutate({ id: t.trade_id, what: "settle" })}
                    >
                      Settle
                    </Button>
                  )}
              </Group>,
            ],
          };
        })}
      />
    </Panel>
  );
}

// --- the ticket --------------------------------------------------------------------------------------------------
function FieldInput({
  f,
  value,
  onChange,
}: {
  f: Schemas["InstrumentField"];
  value: Values[string];
  onChange: (v: Values[string]) => void;
}) {
  const label = f.unit ? `${f.label} (${f.unit})` : f.label;
  if (f.kind === "choice")
    return (
      <Select
        label={label}
        required={f.required}
        data={(f.choices ?? []).map(String)}
        value={value === undefined || value === null ? null : String(value)}
        onChange={(v) => onChange(v ?? undefined)}
        allowDeselect={!f.required}
      />
    );
  if (f.kind === "bool")
    return <Switch label={label} checked={Boolean(value)} onChange={(e) => onChange(e.currentTarget.checked)} />;
  if (f.kind === "number" || f.kind === "int")
    return (
      <NumberInput
        label={label}
        required={f.required}
        value={value === undefined || value === null ? "" : (value as number | string)}
        onChange={(v) => onChange(v === "" ? undefined : v)}
        decimalScale={f.kind === "int" ? 0 : 6}
        thousandSeparator=","
      />
    );
  return (
    <TextInput
      label={label}
      required={f.required}
      type={f.kind === "date" ? "date" : "text"}
      value={value === undefined || value === null ? "" : String(value)}
      onChange={(e) => onChange(e.currentTarget.value || undefined)}
    />
  );
}

function ListedPicker({ name, onPick }: { name: string; onPick: (row: Schemas["ListedRow"]) => void }) {
  const [expiry, setExpiry] = useState<string | null>(null);
  const chain = useQuery({
    queryKey: ["trading", "listed", name],
    queryFn: () => ok(api.GET("/api/v1/trading/hedges/listed", { params: { query: { name } } })),
    retry: false,
  });
  if (chain.isPending) return <Loading />;
  if (chain.error) return <Failure error={chain.error} title="No listed chain" />;
  const data = chain.data;
  const shown = data.rows.filter((r) => r.expiry === (expiry ?? data.expiries[0]));
  return (
    <Panel
      title={`${name} listed options`}
      hint={`the chain of ${formatDate(data.day)}, ${data.origin}; a row fills the ticket`}
      flush
    >
      <Group p="xs">
        <Select
          size="xs"
          label="Expiry"
          data={data.expiries}
          value={expiry ?? data.expiries[0] ?? null}
          onChange={setExpiry}
          allowDeselect={false}
        />
      </Group>
      <DataTable
        caption="Listed options of the expiry"
        head={["Strike", "Kind", "Bid", "Ask", "Mid", "Delta", ""]}
        num={[0, 2, 3, 4, 5]}
        rows={shown.map((r) => ({
          key: `${r.kind}${r.strike}`,
          cells: [
            formatNumber(r.strike, { digits: 0 }),
            r.kind,
            formatNumber(r.bid, { digits: 2 }),
            formatNumber(r.ask, { digits: 2 }),
            formatNumber(r.mid, { digits: 2 }),
            r.delta === null || r.delta === undefined ? "—" : formatPercent(r.delta, { digits: 0 }),
            <Button key="pick" size="compact-xs" variant="default" onClick={() => onPick(r)}>
              Use
            </Button>,
          ],
        }))}
      />
    </Panel>
  );
}

function Ticket({ types }: { types: TypeSpec[] }) {
  const me = useMe().data;
  const queries = useQueryClient();
  const [type, setType] = useState(types[0]?.type ?? "future");
  const spec = types.find((t) => t.type === type) ?? types[0];
  const fields = (spec?.fields ?? []) as (FieldSpec & Schemas["InstrumentField"])[];
  const [values, setValues] = useState<Values>(() => initialValues(fields));
  const [side, setSide] = useState<DeskSide>("bought");
  const [size, setSize] = useState<number | string>(1);
  const [mark, setMark] = useState<Mark | null>(null);
  const choose = (t: string) => {
    const s = types.find((x) => x.type === t);
    setType(t);
    setValues(initialValues((s?.fields ?? []) as FieldSpec[]));
    setMark(null);
  };
  const body = () => instrumentBody(type, fields, values);
  const quantity = signedQuantity(side, Number(size));
  const lacking = missing(fields, values);
  const doMark = useMutation({
    mutationFn: () =>
      ok(api.POST("/api/v1/trading/hedges/marks", { body: { instrument: body(), quantity: quantity || 1 } })),
    onSuccess: setMark,
  });
  const book = useMutation({
    mutationFn: (status: "live" | "simulated") =>
      ok(api.POST("/api/v1/trading/hedges", { body: { instrument: body(), quantity, status } })),
    onSuccess: (t) => {
      void queries.invalidateQueries({ queryKey: ["trading"] });
      notifications.show({
        color: "accent",
        title: `Booked as ${t.trade_id}`,
        message: t.status === "simulated" ? "A what-if: promote it from the blotter." : "In the book.",
      });
    },
  });
  const fair = spec?.fair_field;
  return (
    <div className={c.grid}>
      <Panel title="A hedge" hint={spec ? `size in ${spec.size}` : ""}>
        <Stack gap="sm">
          <Select
            label="Instrument"
            data={types.map((t) => ({ value: t.type, label: t.label }))}
            value={type}
            onChange={(v) => v && choose(v)}
            allowDeselect={false}
          />
          {fields.map((f) => (
            <FieldInput
              key={`${type}-${f.name}`}
              f={f}
              value={values[f.name]}
              onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
            />
          ))}
          <SideSize side={side} onSide={setSide} size={size} onSize={setSize} unit={spec?.size ?? "units"} />
          {doMark.error && <Failure error={doMark.error} title="Not marked" />}
          {book.error && <Failure error={book.error} title="Not booked" />}
          <Group justify="flex-end">
            <Button
              variant="default"
              disabled={lacking.length > 0}
              loading={doMark.isPending}
              onClick={() => doMark.mutate()}
            >
              Mark
            </Button>
            {can(me, "create_hedge") && (
              <>
                <Button
                  variant="default"
                  disabled={lacking.length > 0 || !(Number(size) > 0)}
                  loading={book.isPending && book.variables === "simulated"}
                  onClick={() => book.mutate("simulated")}
                >
                  Book as a what-if
                </Button>
                <Button
                  color="amber"
                  disabled={lacking.length > 0 || !(Number(size) > 0)}
                  loading={book.isPending && book.variables === "live"}
                  onClick={() => book.mutate("live")}
                >
                  Book
                </Button>
              </>
            )}
          </Group>
        </Stack>
      </Panel>
      <Stack gap="md">
        {mark && (
          <Panel title={mark.label} hint={`on ${formatDate(mark.valuation)}, ${mark.origin}`}>
            <KpiRow>
              <Kpi label="Value per unit" unit={mark.currency}>
                <Num value={mark.unit.value} digits={2} />
              </Kpi>
              <Kpi label="Fair level" sub={fair ? `the ${fair} of a fair trade` : undefined}>
                <Num value={mark.unit.fair} digits={4} />
              </Kpi>
              <Kpi
                label="Cost"
                unit={mark.currency}
                sub={`half-spread ${formatNumber(mark.unit.half_spread, { digits: 2 })} per unit`}
              >
                <Num value={mark.cost} digits={2} />
              </Kpi>
            </KpiRow>
            {fair && (
              <Button
                size="xs"
                variant="default"
                mt="sm"
                onClick={() => setValues((prev) => ({ ...prev, [fair]: Number(mark.unit.fair.toFixed(6)) }))}
              >
                Use the fair level
              </Button>
            )}
          </Panel>
        )}
        {type === "vanilla" && Boolean(values.listed) && typeof values.name === "string" && (
          <ListedPicker
            name={values.name}
            onPick={(r) =>
              setValues((prev) => ({ ...prev, expiry: r.expiry, strike: r.strike, kind: r.kind, price: undefined }))
            }
          />
        )}
        {type === "vanilla" && Boolean(values.listed) && (
          <Text size="sm" c="dimmed">
            Without a premium, a listed option is filled at the chain's ask when the desk buys and at its bid when it
            sells; an OTC one at the surface's mid plus or minus the cost table's vol spread.
          </Text>
        )}
      </Stack>
    </div>
  );
}

// --- the optimizer -----------------------------------------------------------------------------------------------
function gridCells(rows: number[][]) {
  return rows.map((r) => r.map((v) => ({ value: v, se: null })));
}

function OptimizerResult({ run, jobId }: { run: Run; jobId: string }) {
  const me = useMe().data;
  const queries = useQueryClient();
  const book = useMutation({
    mutationFn: (status: "simulated" | "live") =>
      ok(api.POST("/api/v1/trading/hedges/proposals", { body: { job_id: jobId, status } })),
    onSuccess: (r) => {
      void queries.invalidateQueries({ queryKey: ["trading"] });
      notifications.show({
        color: "accent",
        title: `${r.trades.length} hedges booked`,
        message: r.status === "simulated" ? "As what-ifs: promote them from the blotter." : "In the book.",
      });
    },
  });
  const grid = run.grid;
  const head = grid ? grid.vols.map((v) => `${formatNumber(v * 100, { digits: 0, sign: true })} pts`) : [];
  const spots = grid ? grid.spots.map((s) => formatPercent(s, { digits: 0, sign: true })) : [];
  return (
    <Stack gap="md">
      <KpiRow label="The proposal">
        <Kpi label="Cost" unit="currency" sub={`${run.proposal.length} trades of ${run.candidates} candidates`}>
          <Num value={run.cost} digits={0} />
        </Kpi>
        <Kpi label="Status" sub={run.missed.length ? `missed: ${run.missed.join(", ")}` : "every target met"}>
          {run.missed.length ? "closest" : "optimal"}
        </Kpi>
        {run.var && (
          <Kpi label="1-day VaR" unit="currency" sub={`${run.var.days} days; before → after`}>
            <Num value={run.var.before.var} /> → <Num value={run.var.after.var} />
          </Kpi>
        )}
      </KpiRow>
      {run.missed.length > 0 && (
        <Alert color="amber" title="Some targets are out of reach">
          The universe or its limits cannot bring {run.missed.join(", ")} within the tolerance: the proposal is the
          closest.
        </Alert>
      )}
      <div className={c.grid}>
        <Panel title="Targets" hint="desk side; after ± the candidates' Monte Carlo error" flush>
          <DataTable
            caption="Each target before and after"
            head={["Greek", "Target", "Tolerance", "Before", "After"]}
            num={[1, 2, 3, 4]}
            rows={run.targets.map((t) => ({
              key: t.key,
              cells: [
                <span key="k">
                  {t.key}{" "}
                  {t.missed && (
                    <Badge size="xs" color="amber">
                      missed
                    </Badge>
                  )}
                </span>,
                formatNumber(t.target, { digits: 0 }),
                formatNumber(t.tolerance, { digits: 0 }),
                <Num key="b" value={t.before} signed />,
                <Est key="a" value={t.after} signed />,
              ],
            }))}
          />
        </Panel>
        <Panel title="The trades" hint="each at its fair level; the cost is the half-spread" flush>
          <DataTable
            caption="The proposed trades"
            head={["Instrument", "Quantity", "Cost", "Group"]}
            num={[1, 2]}
            rows={run.proposal.map((p) => ({
              key: String(p.index),
              cells: [
                p.label,
                <Num key="q" value={p.quantity} digits={p.integer ? 0 : 2} sign />,
                formatNumber(p.cost, { digits: 0 }),
                p.group,
              ],
            }))}
          />
        </Panel>
      </div>
      {grid && (
        <div className={c.grid}>
          <Panel title="Spot × vol, before" hint="the book's P&L, currency">
            <NodeHeatmap
              strikes={spots}
              tenors={head}
              cells={gridCells(grid.before)}
              caption="The book before"
              cornerLabel="spot \ vol"
              tones={["loss", "gain"]}
            />
          </Panel>
          <Panel title="Spot × vol, after" hint="with the proposal, on the same simulations">
            <NodeHeatmap
              strikes={spots}
              tenors={head}
              cells={gridCells(grid.after)}
              caption="The book after"
              cornerLabel="spot \ vol"
              tones={["loss", "gain"]}
            />
          </Panel>
        </div>
      )}
      {run.proposal.length > 0 && can(me, "book_proposal") && (
        <Group justify="flex-end">
          {book.error && <Failure error={book.error} title="Not booked" />}
          <Button
            variant="default"
            loading={book.isPending && book.variables === "simulated"}
            onClick={() => book.mutate("simulated")}
          >
            Book as what-ifs
          </Button>
          <Button
            color="amber"
            loading={book.isPending && book.variables === "live"}
            onClick={() => book.mutate("live")}
          >
            Book them
          </Button>
        </Group>
      )}
    </Stack>
  );
}

function Optimizer() {
  const me = useMe().data;
  const job = useScreenJob("hedge", "Hedge optimizer", "hedgeJob");
  const [precision, setPrecision] = useState("quick");
  const [families, setFamilies] = useState<string[]>([
    "delta",
    "vega_tenor",
    "rho_tenor",
    "dividend_year",
    "funding_tenor",
  ]);
  const [relative, setRelative] = useState<number | string>(10);
  const [universe, setUniverse] = useState({
    futures: true,
    vanillas: true,
    listed: false,
    rates: true,
    dividends: true,
    funding: true,
  });
  const [simulated, setSimulated] = useState(false);
  const run = job.envelope?.result;
  const submit = () =>
    ok(
      api.POST("/api/v1/trading/hedges/optimizations", {
        body: {
          options: { precision: precision as "quick", include_simulated: simulated },
          families: families as Schemas["HedgeOptimizeIn"]["families"],
          relative: Number(relative) / 100,
          universe,
        },
      }),
    );
  return (
    <Stack gap="md">
      <Panel title="Targets and universe" hint="every Greek of the chosen families to zero, within a share of its size">
        <Stack gap="sm">
          <Checkbox.Group label="Families" value={families} onChange={setFamilies}>
            <Group gap="md" mt={4}>
              {FAMILIES.map((f) => (
                <Checkbox key={f.value} value={f.value} label={f.label} />
              ))}
            </Group>
          </Checkbox.Group>
          <NumberInput
            label="Tolerance, % of each Greek"
            value={relative}
            onChange={setRelative}
            min={0}
            max={100}
            w={220}
          />
          <Group gap="md">
            {(Object.keys(universe) as (keyof typeof universe)[]).map((k) => (
              <Switch
                key={k}
                label={k === "listed" ? "vanillas from the chain" : k}
                checked={universe[k]}
                onChange={(e) => {
                  const on = e.currentTarget.checked;
                  setUniverse((u) => ({ ...u, [k]: on }));
                }}
              />
            ))}
            <Switch
              label="include the what-ifs"
              checked={simulated}
              onChange={(e) => setSimulated(e.currentTarget.checked)}
            />
          </Group>
          <Group justify="flex-end">
            {can(me, "optimize_hedges") &&
              (job.running && job.tracked ? (
                <JobProgress job={job.tracked} label="Hedge optimizer" onCancel={() => void job.cancel()} />
              ) : (
                <>
                  <SegmentedControl
                    size="xs"
                    value={precision}
                    onChange={setPrecision}
                    data={PRECISION}
                    aria-label="Precision"
                  />
                  <Button
                    size="xs"
                    disabled={!families.length}
                    loading={job.start.isPending}
                    onClick={() => job.start.mutate(submit)}
                  >
                    Propose hedges
                  </Button>
                </>
              ))}
          </Group>
        </Stack>
      </Panel>
      {job.failed && <Failure error={new Error(job.failed)} title="The optimizer failed" />}
      {run && job.jobId && <OptimizerResult run={run} jobId={job.jobId} />}
    </Stack>
  );
}

export default function Hedging() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") ?? "blotter";
  const trades = useTrades();
  const types = useHedgeTypes();
  const hedges = useMemo(
    () => (trades.data?.items ?? []).filter((t) => (t.product as { kind?: string }).kind === "instrument"),
    [trades.data],
  );
  const setView = (v: string | null) => {
    const next = new URLSearchParams(params);
    next.set("view", v ?? "blotter");
    setParams(next, { replace: true });
  };
  return (
    <SpacePage>
      {(trades.isPending || types.isPending) && <Loading />}
      {trades.error && <Failure error={trades.error} />}
      {types.error && <Failure error={types.error} />}
      <Tabs value={view} onChange={setView} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="blotter">Blotter</Tabs.Tab>
          <Tabs.Tab value="ticket">Ticket</Tabs.Tab>
          <Tabs.Tab value="optimizer">Optimizer</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="blotter" pt="md">
          <Blotter trades={hedges} />
        </Tabs.Panel>
        <Tabs.Panel value="ticket" pt="md">
          {types.data && <Ticket types={types.data.types} />}
        </Tabs.Panel>
        <Tabs.Panel value="optimizer" pt="md">
          <Optimizer />
        </Tabs.Panel>
      </Tabs>
    </SpacePage>
  );
}
