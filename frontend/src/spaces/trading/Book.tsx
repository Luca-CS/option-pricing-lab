// The book (Trading › Book): every trade in a grid grouped as the trader wants, and its valuation at the market in use
// as a job: the value with its error, net of the reserve still held, by group and by position, with delta and vega by
// name. The valuation's job is in the URL, so the same figures reopen.
import { Badge, Button, Group, SegmentedControl, Select } from "@mantine/core";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { useTrades } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useScreenJob } from "../../jobs/useScreenJob";
import { type Estimate, formatNumber, formatPercent } from "../../lib/format";
import { couponOrStrike } from "../../lib/sheet";
import { JobProgress } from "../../ui/Flow";
import { Grid, type GridColumn } from "../../ui/Grid";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable, TradeLink } from "../common";

type Trade = Schemas["Trade"];
type Position = Schemas["BookPosition"];
type Row = Trade & { position?: Position | undefined };

const PRECISION = [
  { value: "quick", label: "Quick" },
  { value: "standard", label: "Standard" },
  { value: "precise", label: "Precise" },
];

function columns(valued: boolean): GridColumn<Row>[] {
  const base: GridColumn<Row>[] = [
    { id: "trade", header: "Trade", accessorKey: "trade_id", cell: (x) => <TradeLink id={x.row.original.trade_id} /> },
    {
      id: "product",
      header: "Product",
      accessorFn: (r) => `${r.summary?.type ?? ""} ${r.summary?.maturity_months ?? ""}m`,
    },
    { id: "underlying", header: "Underlying", accessorFn: (r) => r.summary?.underlyings.join(", ") ?? "—" },
    {
      id: "status",
      header: "Status",
      accessorKey: "status",
      cell: (x) => (
        <Badge variant="light" color={x.getValue() === "live" ? "accent" : "gray"}>
          {String(x.getValue())}
        </Badge>
      ),
    },
    { id: "book", header: "Book", accessorKey: "book" },
    {
      id: "coupon",
      header: "Coupon or strike",
      accessorFn: (r) => r.summary?.coupon_rate ?? r.summary?.strike ?? null,
      cell: (x) => couponOrStrike(x.row.original.summary),
      meta: { numeric: true },
    },
    {
      id: "barrier",
      header: "Barrier",
      accessorFn: (r) => r.summary?.barrier ?? null,
      cell: (x) => (x.getValue() === null ? "—" : formatPercent(x.getValue() as number, { digits: 0 })),
      meta: { numeric: true },
    },
    {
      id: "quantity",
      header: "Quantity",
      accessorKey: "quantity",
      cell: (x) => <Num value={x.getValue() as number} digits={2} sign />,
      meta: { numeric: true },
    },
    { id: "counterparty", header: "Counterparty", accessorKey: "counterparty" },
  ];
  if (!valued) return base;
  return [
    ...base,
    {
      id: "unit",
      header: "Unit price, % nominal",
      accessorFn: (r) => r.position?.unit_price.value ?? null,
      cell: (x) => (x.row.original.position ? <Est value={x.row.original.position.unit_price} /> : "—"),
      meta: { numeric: true },
    },
    {
      id: "value",
      header: "Value",
      accessorFn: (r) => r.position?.value.value ?? null,
      cell: (x) => (x.row.original.position ? <Est value={x.row.original.position.value} signed /> : "—"),
      meta: { numeric: true },
    },
    {
      id: "reserve",
      header: "Reserve left",
      accessorFn: (r) => r.position?.reserve?.amount ?? null,
      cell: (x) => <Num value={x.getValue() as number | null} />,
      meta: { numeric: true },
    },
  ];
}

export default function Book() {
  const trades = useTrades();
  const me = useMe().data;
  const [params, setParams] = useSearchParams();
  const groupBy = params.get("group") ?? "underlying";
  const show = params.get("show") ?? "live";
  const [precision, setPrecision] = useState("quick");
  const job = useScreenJob("book", "Book valuation");
  const valuation = job.envelope?.result;
  // the grid's rows keep their identity while nothing they show changes (the grid recomputes on a new array)
  const rows: Row[] = useMemo(() => {
    const byName = new Map((valuation?.positions ?? []).map((p) => [p.name, p]));
    return (trades.data?.items ?? [])
      .filter((t) => show === "all" || t.status === "live")
      .map((t) => ({ ...t, position: byName.get(t.trade_id) }));
  }, [trades.data, show, valuation]);
  const cols = useMemo(() => columns(Boolean(valuation)), [valuation]);
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };
  const reserveShare =
    valuation?.reserve !== null && valuation?.reserve !== undefined && valuation.reserve_initial
      ? valuation.reserve / valuation.reserve_initial
      : undefined;

  return (
    <SpacePage>
      <Group gap="sm" wrap="wrap">
        <SegmentedControl
          size="xs"
          value={show}
          onChange={(v) => set("show", v)}
          data={[
            { value: "live", label: "Live" },
            { value: "all", label: "All" },
          ]}
          aria-label="Trades shown"
        />
        <Select
          size="xs"
          value={groupBy}
          onChange={(v) => set("group", v ?? "none")}
          data={[
            { value: "none", label: "No grouping" },
            { value: "underlying", label: "By underlying" },
            { value: "status", label: "By status" },
            { value: "book", label: "By book" },
            { value: "counterparty", label: "By counterparty" },
          ]}
          allowDeselect={false}
          aria-label="Group by"
          w={180}
        />
        <span className={c.spacer} />
        {can(me, "value_book") &&
          (job.running && job.tracked ? (
            <JobProgress job={job.tracked} label="Book valuation" onCancel={() => void job.cancel()} />
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
                loading={job.start.isPending}
                onClick={() =>
                  job.start.mutate(() =>
                    ok(
                      api.POST("/api/v1/trading/book/valuations", {
                        body: { options: { precision: precision as "quick" | "standard" | "precise" } },
                      }),
                    ),
                  )
                }
              >
                Value the book
              </Button>
            </>
          ))}
        {import.meta.env.MODE !== "demo" && (
          <Button
            component="a"
            href="/api/v1/trading/book/report"
            target="_blank"
            rel="noopener"
            size="xs"
            variant="subtle"
          >
            Report
          </Button>
        )}
      </Group>
      {job.failed && <Failure error={new Error(job.failed)} title="The valuation failed" />}
      {job.start.error && <Failure error={job.start.error} />}
      {valuation && (
        <KpiRow label="The book's value">
          <Kpi
            label="Value"
            unit="currency, desk side"
            sub={`${valuation.method}, ${formatNumber(valuation.n_paths)} paths`}
          >
            <Est value={valuation.value} signed hideSe />
          </Kpi>
          <Kpi label="Its error" unit="one SE" sub="the groups are independent simulations">
            <Num value={valuation.value.se} />
          </Kpi>
          <Kpi label="Net of the reserve" unit="currency" sub="the value less what the desk still holds back">
            <Num value={valuation.value_net} signed />
          </Kpi>
          <Kpi
            label="Reserve left"
            unit="currency"
            meter={reserveShare}
            sub={`of ${formatNumber(valuation.reserve_initial)} frozen at issue`}
          >
            <Num value={valuation.reserve} />
          </Kpi>
          {Object.entries(valuation.greeks?.by_name ?? {}).map(([name, g]) => (
            <Kpi
              key={name}
              label={`${name} delta`}
              unit="per +1 % spot"
              sub={g.vega ? `vega ${formatNumber(g.vega.value)}` : ""}
            >
              {g.delta ? <Est value={g.delta as Estimate} signed hideSe /> : "—"}
            </Kpi>
          ))}
        </KpiRow>
      )}
      <Panel title="Trades" hint={`${rows.length} shown`} flush>
        {trades.isPending && <Loading />}
        {trades.error && <Failure error={trades.error} />}
        {trades.data && rows.length === 0 && <Empty title="No trade here">Book one from a quote in Structuring.</Empty>}
        {rows.length > 0 && (
          <Grid
            data={rows}
            columns={cols}
            groupBy={groupBy === "none" ? null : groupBy}
            caption="The book's trades"
            rowKey={(r) => r.trade_id}
            initialSort={[{ id: "trade", desc: false }]}
          />
        )}
      </Panel>
      {valuation && (
        <Panel title="Simulations" hint="one per independent group (a single name, a worst-of)" flush>
          <DataTable
            caption="The valuation's groups"
            head={["Group", "Method", "Model", "Paths", "Positions", "Value", "Took", "Why this method"]}
            num={[3, 4, 5, 6]}
            rows={valuation.groups.map((g) => ({
              key: g.label,
              cells: [
                g.label,
                g.method,
                g.model ?? "—",
                formatNumber(g.n_paths),
                g.positions,
                <Est key="v" value={g.value} signed />,
                `${formatNumber(g.seconds, { digits: 2 })} s`,
                <span key="r" className={c.faint}>
                  {g.reason ?? ""}
                </span>,
              ],
            }))}
          />
        </Panel>
      )}
    </SpacePage>
  );
}
