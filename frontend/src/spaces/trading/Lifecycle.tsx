// The book's lifecycle (Trading › Lifecycle): the fixings that decide coupons, calls and knock-ins (typed, pasted,
// or loaded from the lake's closes), and a trade's timeline: what was paid or missed, what comes next, which
// observation still lacks its fixing.
import { Anchor, Button, Group, NumberInput, Select, Stack, Text, Textarea } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, ok } from "../../api/client";
import { useTrades } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { formatDate, formatNumber } from "../../lib/format";
import { choiceLabel } from "../../lib/sheet";
import { Timeline, type TimelineEvent } from "../../ui/Flow";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable, TradeLink } from "../common";

const PAST: Record<string, TimelineEvent["kind"]> = {
  coupon_paid: "paid",
  coupon_missed: "missed",
  called: "called",
  knocked_in: "knocked_in",
  matured: "done",
};

function Fixings() {
  const me = useMe().data;
  const queries = useQueryClient();
  const fixings = useQuery({ queryKey: ["trading", "fixings"], queryFn: () => ok(api.GET("/api/v1/trading/fixings")) });
  const names = Object.keys(fixings.data?.fixings ?? {});
  const [name, setName] = useState<string | null>(null);
  const shown = name ?? names[0] ?? "SPX";
  const levels = Object.entries(fixings.data?.fixings?.[shown] ?? {}).sort(([a], [b]) => (a < b ? 1 : -1));
  const [text, setText] = useState("");
  const [realSpot, setRealSpot] = useState<number | string>("");
  const refresh = () => void queries.invalidateQueries({ queryKey: ["trading"] });
  const add = useMutation({
    mutationFn: () =>
      ok(
        api.POST("/api/v1/trading/fixings", {
          body: { underlying: shown, text, real_spot: realSpot === "" ? null : Number(realSpot) },
        }),
      ),
    onSuccess: () => {
      setText("");
      refresh();
      notifications.show({
        color: "accent",
        title: "Fixings added",
        message: `${shown}: the trades' events are recomputed.`,
      });
    },
  });
  const fromLake = useMutation({
    mutationFn: () => ok(api.POST("/api/v1/trading/fixings/from-lake", { body: { underlying: shown } })),
    onSuccess: () => {
      refresh();
      notifications.show({
        color: "accent",
        title: "Closes loaded",
        message: `${shown}: the lake's closes, at the model's scale.`,
      });
    },
  });
  const remove = useMutation({
    mutationFn: (date: string) =>
      ok(api.DELETE("/api/v1/trading/fixings/{underlying}/{date}", { params: { path: { underlying: shown, date } } })),
    onSuccess: refresh,
  });
  return (
    <Panel
      title="Fixings"
      hint="levels at the model's scale: 100 is the market's spot"
      actions={
        <Select
          size="xs"
          data={names.length ? names : ["SPX"]}
          value={shown}
          onChange={setName}
          searchable
          aria-label="Underlying"
          allowDeselect={false}
        />
      }
    >
      {fixings.isPending && <Loading />}
      {fixings.error && <Failure error={fixings.error} />}
      {can(me, "add_fixings") && (
        <Stack gap="xs" mb="md">
          <Textarea
            label={`Add fixings of ${shown}`}
            description="one per line: a date and a level (pasted from a sheet or a CSV)"
            autosize
            minRows={3}
            maxRows={8}
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
            classNames={{ input: c.mono }}
          />
          <Group gap="sm" align="flex-end">
            <NumberInput
              label="Real spot of the market"
              description="real levels are converted to the model's scale with it"
              value={realSpot}
              onChange={setRealSpot}
              w={220}
              min={0}
            />
            <Button size="xs" onClick={() => add.mutate()} loading={add.isPending} disabled={!text.trim()}>
              Add
            </Button>
            {can(me, "fixings_from_lake") && (
              <Button size="xs" variant="default" onClick={() => fromLake.mutate()} loading={fromLake.isPending}>
                Load the closes from the lake
              </Button>
            )}
          </Group>
          {(add.error || fromLake.error) && <Failure error={add.error ?? fromLake.error} title="Not added" />}
        </Stack>
      )}
      {levels.length === 0 ? (
        <Empty title={`No fixing of ${shown}`}>
          Fixings decide the coupons, calls and knock-ins of the trades on it.
        </Empty>
      ) : (
        <>
          <Text size="xs" c="dimmed" mb={6}>
            {levels.length} fixings; the last 30 shown.
          </Text>
          <DataTable
            caption={`Fixings of ${shown}`}
            head={["Date", "Level", ""]}
            num={[1]}
            rows={levels.slice(0, 30).map(([d, v]) => ({
              key: d,
              cells: [
                formatDate(d),
                formatNumber(v, { digits: 2 }),
                can(me, "delete_fixing") ? (
                  <Anchor
                    key="x"
                    component="button"
                    type="button"
                    size="xs"
                    c="dimmed"
                    onClick={() => remove.mutate(d)}
                  >
                    delete
                  </Anchor>
                ) : (
                  ""
                ),
              ],
            }))}
          />
        </>
      )}
    </Panel>
  );
}

function TradeTimeline({ id }: { id: string }) {
  const events = useQuery({
    queryKey: ["trading", "trade", id, "events"],
    queryFn: () => ok(api.GET("/api/v1/trading/trades/{trade_id}/events", { params: { path: { trade_id: id } } })),
  });
  const e = events.data;
  const timeline: TimelineEvent[] = [
    ...(e?.past ?? []).map((x) => ({
      id: `p-${x.date}-${x.kind}`,
      date: x.date,
      kind: PAST[x.kind] ?? ("done" as const),
      label: choiceLabel(x.kind),
      detail: x.amount !== null && x.amount !== undefined ? `${formatNumber(x.amount)}` : undefined,
    })),
    ...(e?.upcoming ?? []).map((x) => ({
      id: `u-${String(x.observation)}`,
      date: String(x.observation),
      kind: "upcoming" as const,
      label: "observation",
      detail: x.payment ? `paid ${formatDate(String(x.payment))}` : undefined,
    })),
  ];
  return (
    <Panel
      title={
        <>
          Timeline of <TradeLink id={id} />
        </>
      }
      hint={e?.maturity ? `struck ${formatDate(e.strike_date)}, matures ${formatDate(e.maturity)}` : ""}
    >
      {events.isPending && <Loading />}
      {events.error && <Failure error={events.error} />}
      {e?.missing && (
        <Text size="sm" c="var(--ol-brand)" mb="sm">
          The past stops early: {e.missing}. Add the fixing to see the events after it.
        </Text>
      )}
      {timeline.length > 0 ? (
        <Timeline events={timeline} label={`Events of ${id}`} />
      ) : (
        e && <Text c="dimmed">No event yet.</Text>
      )}
    </Panel>
  );
}

export default function Lifecycle() {
  const { id } = useParams();
  const navigate = useNavigate();
  const trades = useTrades().data?.items ?? [];
  const live = trades.filter((t) => t.status === "live");
  const current = id ?? live[0]?.trade_id;
  return (
    <SpacePage>
      <div className={c.grid}>
        <Fixings />
        <div className={c.list}>
          <Select
            label="Trade"
            data={trades.map((t) => ({ value: t.trade_id, label: `${t.trade_id} · ${t.status}` }))}
            value={current ?? null}
            onChange={(v) => v && navigate(`/trading/lifecycle/${encodeURIComponent(v)}`)}
            searchable
            allowDeselect={false}
          />
          {current ? <TradeTimeline id={current} /> : <Empty title="No trade">Book one to follow its life here.</Empty>}
        </div>
      </div>
    </SpacePage>
  );
}
