// A trade (Trading › a trade): what it is, its sheet as booked, its value at today's market, the reserve it froze and
// what is left, its events (paid, missed, to come), its versions and the quote it came from. A trader edits it (each
// edit a new version), deletes it (with an undo) or quotes a copy.
import { Anchor, Badge, Button, Group, Modal, NumberInput, Select, Stack, TextInput } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useScreenJob } from "../../jobs/useScreenJob";
import { type Estimate, formatDate, formatNumber, formatPercent } from "../../lib/format";
import { choiceLabel, describeSheet, display, suffix } from "../../lib/sheet";
import { JobProgress, Timeline, type TimelineEvent } from "../../ui/Flow";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable, QuoteLink } from "../common";

type Detail = Schemas["TradeDetail"];

/** A reserve line's cost at issue: a plain number on the trade, {value, se} in a valuation. */
export function costOf(x: Estimate | number): Estimate {
  return typeof x === "number" ? { value: x, se: null } : x;
}

const PAST: Record<string, TimelineEvent["kind"]> = {
  coupon_paid: "paid",
  coupon_missed: "missed",
  called: "called",
  knocked_in: "knocked_in",
  matured: "done",
};

function EditDialog({ detail, opened, onClose }: { detail: Detail; opened: boolean; onClose: () => void }) {
  const t = detail.trade;
  const [quantity, setQuantity] = useState<number | string>(t.quantity);
  const [counterparty, setCounterparty] = useState(t.counterparty ?? "");
  const [book, setBook] = useState(t.book);
  const [status, setStatus] = useState(t.status);
  const queries = useQueryClient();
  const save = useMutation({
    mutationFn: () => {
      const body: Schemas["TradeUpdate"] = {
        quantity: Number(quantity),
        counterparty,
        book,
        status: status as Schemas["TradeUpdate"]["status"],
      };
      return ok(api.PUT("/api/v1/trading/trades/{trade_id}", { params: { path: { trade_id: t.trade_id } }, body }));
    },
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["trading"] });
      onClose();
    },
  });
  return (
    <Modal opened={opened} onClose={onClose} title={`Edit ${t.trade_id}`}>
      <Stack gap="sm">
        <NumberInput label="Quantity" value={quantity} onChange={setQuantity} decimalScale={4} />
        <TextInput label="Counterparty" value={counterparty} onChange={(e) => setCounterparty(e.currentTarget.value)} />
        <TextInput label="Book" value={book} onChange={(e) => setBook(e.currentTarget.value)} />
        <Select
          label="Status"
          data={["live", "called", "matured", "cancelled"]}
          value={status}
          onChange={(v) => setStatus(v ?? status)}
          allowDeselect={false}
        />
        {save.error && <Failure error={save.error} title="Not saved" />}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            Save a new version
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export default function TradePage() {
  const { id = "" } = useParams();
  const me = useMe().data;
  const queries = useQueryClient();
  const detail = useQuery({
    queryKey: ["trading", "trade", id],
    queryFn: () => ok(api.GET("/api/v1/trading/trades/{trade_id}", { params: { path: { trade_id: id } } })),
  });
  const events = useQuery({
    queryKey: ["trading", "trade", id, "events"],
    queryFn: () => ok(api.GET("/api/v1/trading/trades/{trade_id}/events", { params: { path: { trade_id: id } } })),
  });
  const job = useScreenJob("value", `Valuation of ${id}`);
  const v = job.envelope?.result;
  const [editing, setEditing] = useState(false);
  const restore = useMutation({
    mutationFn: () => ok(api.POST("/api/v1/trading/trades/{trade_id}/restore", { params: { path: { trade_id: id } } })),
    onSuccess: () => void queries.invalidateQueries({ queryKey: ["trading"] }),
  });
  const remove = useMutation({
    mutationFn: () => ok(api.DELETE("/api/v1/trading/trades/{trade_id}", { params: { path: { trade_id: id } } })),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["trading"] });
      notifications.show({
        title: `${id} deleted`,
        message: (
          <Anchor component="button" type="button" size="sm" onClick={() => restore.mutate()}>
            Undo: restore it
          </Anchor>
        ),
        autoClose: 10_000,
      });
    },
  });
  const d = detail.data;
  const t = d?.trade;
  const sheet = (t?.product.term_sheet ?? null) as Record<string, unknown> | null;
  const timeline: TimelineEvent[] = [
    ...(events.data?.past ?? []).map((e) => ({
      id: `past-${e.date}-${e.kind}`,
      date: e.date,
      kind: PAST[e.kind] ?? ("done" as const),
      label: choiceLabel(e.kind),
      detail:
        e.amount !== null && e.amount !== undefined
          ? `${formatNumber(e.amount)} paid ${formatDate(e.payment)}`
          : undefined,
    })),
    ...(events.data?.upcoming ?? []).map((e) => ({
      id: `next-${String(e.observation)}`,
      date: String(e.observation),
      kind: "upcoming" as const,
      label: "observation",
      detail: e.payment ? `paid ${formatDate(String(e.payment))}` : undefined,
    })),
  ];

  return (
    <SpacePage title={`Trade ${id}`}>
      {detail.isPending && <Loading />}
      {detail.error && <Failure error={detail.error} title="This trade cannot be read" />}
      {d && t && (
        <>
          <Group gap="sm" wrap="wrap">
            <Badge variant="light" color={t.status === "live" ? "accent" : "gray"}>
              {t.status}
            </Badge>
            <span>{describeSheet(sheet)}</span>
            <span className={c.faint}>
              {t.counterparty || "no counterparty"} · book {t.book} · traded {formatDate(t.trade_date)} · struck{" "}
              {formatDate(t.strike_date)}
            </span>
            <span className={c.spacer} />
            {can(me, "value_trade") &&
              (job.running && job.tracked ? (
                <JobProgress job={job.tracked} label="Valuation" onCancel={() => void job.cancel()} />
              ) : (
                <Button
                  size="xs"
                  loading={job.start.isPending}
                  onClick={() =>
                    job.start.mutate(() =>
                      ok(
                        api.POST("/api/v1/trading/trades/{trade_id}/valuations", {
                          params: { path: { trade_id: id } },
                          body: { options: { precision: "standard" } },
                        }),
                      ),
                    )
                  }
                >
                  Value at today's market
                </Button>
              ))}
            {can(me, "update_trade") && (
              <Button size="xs" variant="default" onClick={() => setEditing(true)}>
                Edit
              </Button>
            )}
            {can(me, "delete_trade") && (
              <Button
                size="xs"
                variant="subtle"
                color="red"
                onClick={() =>
                  modals.openConfirmModal({
                    title: `Delete ${id}?`,
                    children: "It leaves the book; it stays in the store and can be restored.",
                    labels: { confirm: "Delete", cancel: "Keep it" },
                    confirmProps: { color: "red" },
                    onConfirm: () => remove.mutate(),
                  })
                }
              >
                Delete
              </Button>
            )}
          </Group>
          {job.failed && <Failure error={new Error(job.failed)} title="The valuation failed" />}
          <KpiRow label="The trade">
            <Kpi
              label="Quantity"
              sub={`notional ${formatNumber((sheet?.general as { notional?: number } | undefined)?.notional)}`}
            >
              <Num value={t.quantity} digits={2} sign />
            </Kpi>
            {v && (
              <>
                <Kpi
                  label="Unit price"
                  unit="% of nominal"
                  sub={`${formatNumber(v.result.n_paths)} paths, ${v.result.method}`}
                >
                  <Est value={v.unit_price} />
                </Kpi>
                <Kpi label="Value" unit="currency, desk side" sub={`on ${formatDate(v.valuation)}`}>
                  <Est value={v.value} signed />
                </Kpi>
              </>
            )}
            {d.reserve && (
              <Kpi
                label="Reserve left"
                unit="currency"
                meter={d.reserve.initial ? d.reserve.amount / d.reserve.initial : undefined}
                sub={`of ${formatNumber(d.reserve.initial)}; ${formatPercent(d.reserve.fraction, { digits: 0 })} of the life gone`}
              >
                <Num value={d.reserve.amount} />
              </Kpi>
            )}
          </KpiRow>
          <div className={c.grid}>
            <Panel title="The sheet as booked" hint={d.form?.label ?? ""}>
              <dl className={c.facts}>
                {(d.form?.sections ?? []).flatMap((s) =>
                  s.fields
                    .filter((f) => f.value !== null && f.value !== undefined && s.name !== "costs")
                    .map((f) => [
                      <dt key={`${f.path}-t`}>{f.label}</dt>,
                      <dd key={`${f.path}-d`}>
                        {f.kind === "choice" ? choiceLabel(f.value) : display(f, f.value)} {suffix(f)}
                      </dd>,
                    ]),
                )}
              </dl>
            </Panel>
            <div className={c.list}>
              <Panel
                title="Its events"
                hint={events.data?.maturity ? `matures ${formatDate(events.data.maturity)}` : ""}
              >
                {events.isPending && <Loading lines={3} />}
                {timeline.length > 0 ? (
                  <Timeline events={timeline} label={`Events of ${id}`} />
                ) : (
                  events.data && <p className={c.muted}>No event yet.</p>
                )}
              </Panel>
              <Panel title="Where it came from">
                <ul className={c.list}>
                  <li>
                    {d.quote_id ? (
                      <>
                        Quote <QuoteLink id={d.quote_id} />
                      </>
                    ) : (
                      "Booked from a sheet, not a quote."
                    )}
                  </li>
                  <li>
                    <Link to={`/structuring/builder?trade=${encodeURIComponent(id)}`}>Quote a copy in the builder</Link>
                  </li>
                  <li>
                    <Link to={`/trading/risk?trade=${encodeURIComponent(id)}`}>Its Greeks in the book risk</Link>
                  </li>
                  <li>
                    <Link to={`/trading/pnl?trade=${encodeURIComponent(id)}`}>Its P&L explained</Link>
                  </li>
                  <li>
                    <Link to={`/trading/lifecycle/${encodeURIComponent(id)}`}>Its lifecycle and fixings</Link>
                  </li>
                  <li>
                    <Link to={`/quant/replay?trade=${encodeURIComponent(id)}`}>Its replay on past closes</Link>
                  </li>
                </ul>
              </Panel>
            </div>
          </div>
          {(v?.reserve ?? d.reserve) && (
            <Panel title="The reserve" hint={(v?.reserve ?? d.reserve)?.method} flush>
              <DataTable
                caption="The reserve, line by line"
                head={["Line", "Kind", "Cost at issue, % nominal", "Frozen", "Left"]}
                num={[2, 3, 4]}
                rows={((v?.reserve ?? d.reserve)?.lines ?? []).map((l) => ({
                  key: l.key,
                  cells: [
                    l.label,
                    <span key="k" className={c.faint}>
                      {l.kind}
                    </span>,
                    <Est key="c" value={costOf(l.cost)} digits={3} />,
                    formatNumber(l.initial),
                    formatNumber(l.amount),
                  ],
                }))}
              />
            </Panel>
          )}
          <Panel title="Versions" hint="each edit is a new version, kept" flush>
            <DataTable
              caption="The trade's versions"
              head={["Version", "Recorded", "Status", "Quantity", "Book", "Counterparty", ""]}
              num={[0, 3]}
              rows={d.versions.map((x) => ({
                key: String(x.version),
                cells: [
                  x.version,
                  formatDate(x.recorded_at),
                  x.status,
                  formatNumber(x.quantity, { digits: 2 }),
                  x.book,
                  x.counterparty,
                  x.deleted ? "deleted" : "",
                ],
              }))}
            />
          </Panel>
          <EditDialog detail={d} opened={editing} onClose={() => setEditing(false)} />
        </>
      )}
    </SpacePage>
  );
}
