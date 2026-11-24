// The risk manager's home: the stored runs (Greeks, ladders, scenarios, VaR, EOD) and where the explained P&L stands.
import { Anchor } from "@mantine/core";
import { Link } from "react-router";
import { usePnl, useRiskRuns } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { formatDate, formatSeconds } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

const KIND: Record<string, string> = {
  risk: "Greeks",
  ladder: "Ladder",
  scenarios: "Scenarios",
  var: "VaR",
  eod: "EOD",
  pnl: "P&L",
};

export function RiskHome() {
  const runs = useRiskRuns();
  const pnl = usePnl();
  const items = runs.data?.items ?? [];
  const last = (kind: string) => items.find((r) => r.kind === kind);
  const lastVar = last("var");
  const lastEod = last("eod");
  return (
    <SpacePage>
      <KpiRow label="Risk at a glance">
        <Kpi label="Last VaR" sub={lastVar?.run_id ?? "none yet"}>
          <span className={c.mono}>{lastVar ? formatDate(lastVar.day) : "—"}</span>
        </Kpi>
        <Kpi label="Last EOD" sub={lastEod?.run_id ?? "none yet"}>
          <span className={c.mono}>{lastEod ? formatDate(lastEod.day) : "—"}</span>
        </Kpi>
        <Kpi label="Explained P&L" sub={pnl.data?.note ?? `${pnl.data?.days?.length ?? 0} days`}>
          <span className={c.mono}>{pnl.data?.days?.length ? `${pnl.data.days.length} days` : "—"}</span>
        </Kpi>
        <Kpi label="Stored runs" sub="every kind">
          <span className={c.mono}>{items.length}</span>
        </Kpi>
      </KpiRow>
      <Panel title="Stored runs" hint="newest first; each is rerun on the same seed when asked again" flush>
        {runs.isPending && <Loading />}
        {runs.error && <Failure error={runs.error} />}
        {runs.data && items.length === 0 && (
          <Empty
            title="No run yet"
            action={
              <Anchor component={Link} to="/risk/var">
                Run a VaR
              </Anchor>
            }
          >
            Greeks, ladders, scenarios, VaR and EOD runs are kept here with the market and code they ran on.
          </Empty>
        )}
        {items.length > 0 && (
          <DataTable
            caption="Stored risk runs"
            head={["Run", "Kind", "Day", "Version", "Took", "Code"]}
            num={[3, 4]}
            rows={items.map((r) => ({
              key: r.run_id,
              cells: [
                <span key="id" className={c.mono}>
                  {r.run_id}
                </span>,
                KIND[r.kind] ?? r.kind,
                formatDate(r.day),
                r.version,
                formatSeconds(r.seconds),
                <span key="code" className={c.faint}>
                  {r.code ?? "—"}
                </span>,
              ],
            }))}
          />
        )}
      </Panel>
    </SpacePage>
  );
}
