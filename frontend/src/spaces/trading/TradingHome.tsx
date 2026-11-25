// The trader's home (HANDOFF_REFACTOR_2_UX.md §3): the book at a glance, what is due, the last risk run and what
// deserves a look. Figures that need a computation (the book's value, its Greeks) come from the stored runs or from a
// job the trader starts; the home never computes on its own.
import { Anchor, Badge, Button } from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, ok } from "../../api/client";
import { useMarketContext, useRiskRuns, useTrades, useUpcoming } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useJobs } from "../../jobs/jobs";
import { formatDate, formatPercent, formatSeconds } from "../../lib/format";
import { Timeline, type TimelineEvent } from "../../ui/Flow";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Num } from "../../ui/Value";
import { commonClasses as c, DataTable, TradeLink } from "../common";

export function TradingHome() {
  const me = useMe().data;
  const { track } = useJobs();
  const revalue = useMutation({
    mutationFn: () => ok(api.POST("/api/v1/trading/book/valuations", { body: { options: { precision: "quick" } } })),
    onSuccess: (accepted) => track(accepted.job_id, { kind: "book", label: "Book valuation" }),
  });
  const trades = useTrades();
  const upcoming = useUpcoming(90);
  const runs = useRiskRuns("risk");
  const context = useMarketContext().data;
  const items = trades.data?.items ?? [];
  const live = items.filter((t) => t.status === "live");
  const ended = items.filter((t) => t.status !== "live" && t.status !== "deleted");
  const lastRun = runs.data?.items[0];
  const events: TimelineEvent[] = (upcoming.data?.events ?? []).map((e) => ({
    id: `${e.trade_id}-${e.observation}`,
    date: e.observation,
    kind: "upcoming",
    label: <TradeLink id={e.trade_id} />,
    detail: e.final ? "final observation" : e.payment ? `paid ${formatDate(e.payment)}` : "observation",
  }));

  return (
    <SpacePage>
      <KpiRow label="The book">
        <Kpi label="Live trades" sub={`${ended.length} called or matured`}>
          <Num value={live.length} />
        </Kpi>
        <Kpi label="Next event" sub={events[0] ? `${upcoming.data?.events[0]?.trade_id}` : "none within 90 days"}>
          <span className={c.mono}>{events[0] ? formatDate(events[0].date) : "—"}</span>
        </Kpi>
        <Kpi
          label="Last Greeks run"
          sub={lastRun ? `${lastRun.run_id} · ${formatSeconds(lastRun.seconds)}` : "none yet"}
        >
          <span className={c.mono}>{lastRun ? formatDate(lastRun.day) : "—"}</span>
        </Kpi>
        <Kpi label="Market" sub={context?.snapshot_id ?? ""}>
          <span className={c.mono}>{context ? formatDate(context.valuation) : "—"}</span>
        </Kpi>
      </KpiRow>

      <div className={c.wide}>
        <Panel
          title="Positions"
          hint={`${items.length} trades`}
          flush
          actions={
            <>
              {can(me, "value_book") && (
                <Button
                  size="xs"
                  variant="default"
                  loading={revalue.isPending}
                  onClick={() => revalue.mutate()}
                  title="A quick valuation of every live trade on the market in use"
                >
                  Revalue the book
                </Button>
              )}
              <Button component={Link} to="/trading/book" size="xs">
                Open the book
              </Button>
            </>
          }
        >
          {revalue.error && <Failure error={revalue.error} title="The book was not revalued" />}
          {trades.isPending && <Loading />}
          {trades.error && <Failure error={trades.error} />}
          {trades.data && items.length === 0 && (
            <Empty
              title="No trade yet"
              action={
                <Anchor component={Link} to="/structuring/builder">
                  Structure one
                </Anchor>
              }
            >
              A trade is booked from a quote or a sheet.
            </Empty>
          )}
          {items.length > 0 && (
            <DataTable
              caption="Trades of the book"
              head={["Trade", "Product", "Underlying", "Coupon", "Barrier", "Quantity", "Status", "Counterparty"]}
              num={[3, 4, 5]}
              rows={items.map((t) => ({
                key: t.trade_id,
                cells: [
                  <TradeLink key="id" id={t.trade_id} />,
                  `${t.summary?.type ?? "—"} ${t.summary?.maturity_months ? `${t.summary.maturity_months}m` : ""}`,
                  t.summary?.underlyings.join(", ") ?? "—",
                  t.summary?.coupon_rate !== null && t.summary?.coupon_rate !== undefined
                    ? formatPercent(t.summary.coupon_rate)
                    : "—",
                  t.summary?.barrier !== null && t.summary?.barrier !== undefined
                    ? formatPercent(t.summary.barrier, { digits: 0 })
                    : "—",
                  <Num key="q" value={t.quantity} digits={2} sign />,
                  <Badge
                    key="s"
                    variant={t.status === "live" ? "light" : "outline"}
                    color={t.status === "live" ? "accent" : "gray"}
                  >
                    {t.status}
                  </Badge>,
                  t.counterparty || "—",
                ],
              }))}
            />
          )}
        </Panel>
        <div className={c.list}>
          <Panel title="Next 90 days" hint="observations and payments">
            {upcoming.isPending && <Loading lines={2} />}
            {upcoming.data && events.length === 0 && (
              <p className={c.muted}>Nothing due before {formatDate(upcoming.data.until)}.</p>
            )}
            {events.length > 0 && <Timeline events={events} label="Events to come" />}
          </Panel>
          <Panel title="Worth a look">
            <ul className={c.list}>
              {!lastRun && (
                <li>
                  No Greeks run on this book yet:{" "}
                  <Anchor component={Link} to="/trading/risk">
                    run the Greeks
                  </Anchor>
                  .
                </li>
              )}
              {context && (
                <li className={c.muted}>
                  Market of {formatDate(context.valuation)}, surface {context.origin.surface ?? "of unknown origin"}.
                </li>
              )}
              {items.some((t) => t.status === "live" && !t.reserves) && (
                <li>Some live trades carry no reserve frozen at issue.</li>
              )}
            </ul>
          </Panel>
        </div>
      </div>
    </SpacePage>
  );
}
