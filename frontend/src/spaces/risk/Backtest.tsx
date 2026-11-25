// The VaR's backtest (Risk › Backtest): a VaR run's 1-day VaR set against each stored day of the explained P&L. A
// loss beyond it is an exception; Kupiec's test says whether their count is what the VaR's level expects. One VaR for
// the whole period, not a VaR per day: the screen says which run and which days it compares.
import { Anchor, Button, Group, Select, Text, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ok } from "../../api/client";
import { useRiskRuns } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { SeriesChart } from "../../charts";
import { formatDate, formatNumber, formatPercent } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Num } from "../../ui/Value";
import { commonClasses as c, DataTable } from "../common";

type Kupiec = { exceptions?: number; n?: number; expected?: number; rate?: number; lr?: number; p_value?: number };

export default function Backtest() {
  const [params, setParams] = useSearchParams();
  const varRuns = useRiskRuns("var");
  const [from, setFrom] = useState(params.get("from") ?? "");
  const [to, setTo] = useState(params.get("to") ?? "");
  const query = {
    var_run: params.get("var_run") || undefined,
    from: params.get("from") || undefined,
    to: params.get("to") || undefined,
  };
  const test = useQuery({
    queryKey: ["risk", "backtest", query],
    queryFn: () => ok(api.GET("/api/v1/risk/backtest", { params: { query } })),
  });
  const b = test.data;
  const days = b?.days ?? [];
  const k = (b?.kupiec ?? null) as Kupiec | null;
  const exceptions = days.filter((d) => d.exception);
  const set = (change: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, v] of Object.entries(change)) {
      if (v) next.set(key, v);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };
  return (
    <SpacePage>
      <Group gap="sm" align="flex-end" wrap="wrap">
        <Select
          label="VaR run"
          size="sm"
          w={240}
          data={(varRuns.data?.items ?? []).map((r) => ({ value: r.run_id, label: `${r.run_id}` }))}
          value={params.get("var_run") ?? b?.var_run ?? null}
          onChange={(v) => set({ var_run: v })}
          placeholder="the last one"
          allowDeselect={false}
        />
        <TextInput type="date" label="From" value={from} onChange={(e) => setFrom(e.currentTarget.value)} w={170} />
        <TextInput type="date" label="To" value={to} onChange={(e) => setTo(e.currentTarget.value)} w={170} />
        <Button size="sm" variant="default" onClick={() => set({ from, to })}>
          Test
        </Button>
      </Group>
      {test.isPending && <Loading />}
      {test.error && <Failure error={test.error} />}
      {b?.note && (
        <Empty
          title="Nothing to test yet"
          action={
            <Group gap="md">
              <Anchor component={Link} to="/risk/var">
                VaR
              </Anchor>
              <Anchor component={Link} to="/risk/runs">
                EOD runs
              </Anchor>
            </Group>
          }
        >
          {`${b.note[0]?.toUpperCase()}${b.note.slice(1)}.`}
        </Empty>
      )}
      {b && !b.note && (
        <>
          <Text size="sm" c="dimmed">
            The 1-day VaR of{" "}
            <Anchor
              component={Link}
              to={`/risk/var/${encodeURIComponent(b.var_run ?? "")}`}
              className={c.mono}
              underline="always"
            >
              {b.var_run}
            </Anchor>{" "}
            (the book of {formatDate(b.var_day)}) against the explained P&L of {formatDate(b.from)} to{" "}
            {formatDate(b.to)}, {days.length} days.
          </Text>
          <KpiRow label="The backtest">
            <Kpi label={`VaR ${formatPercent(b.level, { digits: 0 })}`} unit="currency" sub="one VaR, every day">
              <Num value={b.var} />
            </Kpi>
            <Kpi label="Exceptions" sub={`a loss beyond the VaR, out of ${k?.n ?? days.length} days`}>
              <span className={c.mono}>{k?.exceptions ?? exceptions.length}</span>
            </Kpi>
            <Kpi label="Expected" sub={`${formatPercent(1 - b.level, { digits: 0 })} of the days`}>
              <span className={c.mono}>{formatNumber(k?.expected, { digits: 1 })}</span>
            </Kpi>
            <Kpi
              label="Kupiec p-value"
              sub={
                k?.p_value === undefined
                  ? "no test"
                  : k.p_value < 0.05
                    ? "below 5 %: the count rejects the VaR's level"
                    : "above 5 %: the count does not reject the level"
              }
            >
              <span className={c.mono}>{formatNumber(k?.p_value, { digits: 3 })}</span>
            </Kpi>
          </KpiRow>
          <Panel title="Each day against the VaR" hint="currency; a bar below the line is an exception">
            <SeriesChart
              x={days.map((d) => d.day)}
              series={[
                { name: "day's P&L", values: days.map((d) => d.pnl), kind: "bar" },
                { name: "− VaR", values: days.map(() => -(b.var ?? 0)), kind: "line" },
              ]}
              unit="currency"
              signedBars
              height={280}
              label="Each day's explained P&L against the loss at the VaR"
            />
          </Panel>
          <Panel title="Exceptions" hint="the days the loss went beyond the VaR" flush>
            {exceptions.length === 0 ? (
              <p className={`${c.faint} ${c.pad}`}>No day lost more than the VaR over the period.</p>
            ) : (
              <DataTable
                caption="Days whose loss went beyond the VaR"
                head={["Day", "P&L", "Beyond the VaR by", ""]}
                num={[1, 2]}
                rows={exceptions.map((d) => ({
                  key: d.day,
                  cells: [
                    <span key="d" className={c.mono}>
                      {formatDate(d.day)}
                    </span>,
                    <Num key="p" value={d.pnl} signed />,
                    <Num key="b" value={-d.pnl - (b.var ?? 0)} />,
                    <Anchor key="x" component={Link} to={`/trading/pnl?day=${d.day}`} size="sm">
                      Explain the day
                    </Anchor>,
                  ],
                }))}
              />
            )}
          </Panel>
        </>
      )}
    </SpacePage>
  );
}
