// The quant's home: the data behind every number (coverage, origin, freshness, quality) and the engine that computes.
import { useEngine, useLakeCoverage, useLakeQuality, useSnapshots } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { formatDate, formatNumber } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { OriginBadge } from "../../ui/Markers";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

export function QuantHome() {
  const coverage = useLakeCoverage();
  const quality = useLakeQuality();
  const engine = useEngine().data;
  const snapshots = useSnapshots().data?.items ?? [];
  const rows = coverage.data?.coverage ?? [];
  const last = rows
    .map((r) => r.last ?? "")
    .sort()
    .at(-1);
  const issues = quality.data?.issues?.length ?? 0;
  return (
    <SpacePage>
      <KpiRow label="Data and engine">
        <Kpi label="Lake up to" sub={`${rows.length} datasets`}>
          <span className={c.mono}>{last ? formatDate(last) : "—"}</span>
        </Kpi>
        <Kpi label="Quality issues" sub={quality.data?.available ? "open now" : "no lake"}>
          <span className={c.mono}>{quality.data?.available ? issues : "—"}</span>
        </Kpi>
        <Kpi label="Snapshots stored" sub={snapshots.find((s) => s.active)?.snapshot_id ?? ""}>
          <span className={c.mono}>{snapshots.length}</span>
        </Kpi>
        <Kpi
          label="Engine"
          sub={engine ? `${engine.cpu_threads ?? "?"} CPU threads${engine.cuda_build ? ", CUDA build" : ""}` : ""}
        >
          <span className={c.mono}>{engine ? engine.resolved.toUpperCase() : "—"}</span>
        </Kpi>
      </KpiRow>
      <Panel title="The lake" hint="every row says whether it was observed, derived or reconstructed" flush>
        {coverage.isPending && <Loading />}
        {coverage.error && <Failure error={coverage.error} />}
        {coverage.data && !coverage.data.available && (
          <Empty title="No lake on this desk">
            The market data lake is built by `optlab lake update` (Admin › Pipelines).
          </Empty>
        )}
        {rows.length > 0 && (
          <DataTable
            caption="Coverage of the lake"
            head={["Dataset", "Origin", "Source", "Rows", "From", "To", "Loaded"]}
            num={[3]}
            rows={rows.map((r, i) => ({
              key: `${r.dataset}-${r.origin}-${r.source}-${i}`,
              cells: [
                r.dataset,
                <OriginBadge key="o" origin={r.origin} />,
                <span key="s" className={c.faint}>
                  {r.source ?? "—"}
                </span>,
                formatNumber(r.n),
                formatDate(r.first),
                formatDate(r.last),
                <span key="l" className={c.faint}>
                  {formatDate(r.ingested)}
                </span>,
              ],
            }))}
          />
        )}
      </Panel>
    </SpacePage>
  );
}
