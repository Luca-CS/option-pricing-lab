// Trading › Calls: the issuer's calls on the desk's notes (ADR-0026). The next call date of each live issuer callable,
// the review job that prices each note going on and called on the same random numbers, and the call itself (the
// trader's, audited). The decision is the server's: the issuer calls when the note is worth more going on.
import { Badge, Button, Group, SegmentedControl, Stack, Text } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useScreenJob } from "../../jobs/useScreenJob";
import { formatDate, formatNumber } from "../../lib/format";
import { JobProgress } from "../../ui/Flow";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { DataTable, TradeLink } from "../common";

type Review = Schemas["CallReviewRow"];

const PRECISION = [
  { value: "quick", label: "Quick" },
  { value: "standard", label: "Standard" },
];
const DECISION: Record<string, { label: string; color: string }> = {
  call: { label: "call", color: "orange" },
  keep: { label: "keep", color: "gray" },
  close: { label: "close", color: "yellow" },
};

function useCalls() {
  return useQuery({ queryKey: ["calls"], queryFn: () => ok(api.GET("/api/v1/trading/calls")) });
}

function CallButton({ review }: { review: Review }) {
  const queries = useQueryClient();
  const call = useMutation({
    mutationFn: () =>
      ok(
        api.POST("/api/v1/trading/trades/{trade_id}/call", {
          params: { path: { trade_id: review.trade_id } },
          body: { call_date: review.call_date ?? null },
        }),
      ),
    onSuccess: () => {
      notifications.show({ message: `${review.trade_id} called on ${formatDate(review.call_date)}` });
      void queries.invalidateQueries({ queryKey: ["calls"] });
      void queries.invalidateQueries({ queryKey: ["trades"] });
    },
    onError: (e) => notifications.show({ color: "red", message: String(e) }),
  });
  return (
    <Button
      size="compact-xs"
      variant="light"
      loading={call.isPending}
      onClick={() =>
        modals.openConfirmModal({
          title: `Call ${review.trade_id} on ${formatDate(review.call_date)}?`,
          children: "The note is redeemed at its call price on that date: the trade ends, called.",
          labels: { confirm: "Call", cancel: "Not now" },
          onConfirm: () => call.mutate(),
        })
      }
    >
      Call
    </Button>
  );
}

function Reviews({ reviews, canCall }: { reviews: Review[]; canCall: boolean }) {
  return (
    <DataTable
      head={["Trade", "Call date", "Going on", "Called", "Difference", "Delta going on", "Decision", ""]}
      num={[2, 3, 4, 5]}
      rows={reviews.map((r) => ({
        key: r.trade_id,
        cells: [
          <TradeLink key="t" id={r.trade_id} />,
          formatDate(r.call_date),
          r.going_on ? (
            <Est key="o" value={{ value: r.going_on.value, se: r.going_on.se }} digits={2} unit=" %" />
          ) : (
            "—"
          ),
          r.called ? <Est key="c" value={{ value: r.called.value, se: r.called.se }} digits={2} unit=" %" /> : "—",
          r.gap ? <Est key="g" value={r.gap} digits={2} unit=" %" signed /> : "—",
          r.going_on ? <Num key="d" value={r.going_on.delta} /> : "—",
          r.decision ? (
            <Badge key="b" color={DECISION[r.decision]?.color} variant="light" title={r.note}>
              {DECISION[r.decision]?.label ?? r.decision}
            </Badge>
          ) : (
            <Text key="n" size="xs">
              {r.note}
            </Text>
          ),
          canCall && r.decision ? <CallButton key="a" review={r} /> : null,
        ],
      }))}
    />
  );
}

export default function Calls() {
  const me = useMe().data;
  const calls = useCalls();
  const job = useScreenJob("call_review", "Call review", "callJob");
  const [precision, setPrecision] = useState("quick");
  const result = job.envelope?.result;
  const rows = calls.data?.calls ?? [];
  return (
    <SpacePage title="Calls">
      <Stack gap="md">
        <Panel title="The calls to come" hint="the next call of each live issuer callable, the soonest notice first">
          {calls.isLoading ? (
            <Loading />
          ) : calls.error ? (
            <Failure error={calls.error} />
          ) : rows.length ? (
            <DataTable
              head={["Trade", "Underlyings", "Notice", "Call date", "Call price", "Calls left"]}
              num={[4, 5]}
              rows={rows.map((r) => ({
                key: r.trade_id,
                cells: [
                  <TradeLink key="t" id={r.trade_id} />,
                  r.underlyings.join(", "),
                  formatDate(r.notice_date),
                  formatDate(r.call_date),
                  `${formatNumber(100 * r.call_price, { digits: 2 })} %`,
                  r.calls_left,
                ],
              }))}
            />
          ) : (
            <Empty title="No live issuer callable in the book" />
          )}
        </Panel>
        <Panel
          title="Review"
          hint="each note going on and called at its next date, on the same random numbers: the issuer calls when it pays less"
        >
          <Stack gap="sm">
            <Group justify="flex-end">
              {can(me, "review_calls") &&
                (job.running && job.tracked ? (
                  <JobProgress job={job.tracked} label="Call review" onCancel={() => void job.cancel()} />
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
                      disabled={!rows.length}
                      loading={job.start.isPending}
                      onClick={() =>
                        job.start.mutate(() =>
                          ok(
                            api.POST("/api/v1/trading/calls/reviews", {
                              body: { options: { precision: precision as "quick" } },
                            }),
                          ),
                        )
                      }
                    >
                      Review the calls
                    </Button>
                  </>
                ))}
            </Group>
            {job.failed ? <Failure error={job.failed} /> : null}
            {result ? (
              <Reviews reviews={result.reviews} canCall={can(me, "call_trade")} />
            ) : (
              <Text size="sm" c="dimmed">
                A review prices every note on the market in use, at the precision chosen.
              </Text>
            )}
          </Stack>
        </Panel>
      </Stack>
    </SpacePage>
  );
}
