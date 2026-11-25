// The system (Admin › System): the desk's version, database and market in use, the workers and the engine each sees,
// the backups, the settings it runs with, and whether other machines could reach it (read-only: the LAN is Luca's
// decision, ADR-0002).
import { Badge, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api, ok } from "../../api/client";
import { useSystem } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { formatDate, formatNumber } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { OriginBadge, PIPELINES } from "../../ui/Markers";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";
import classes from "./Admin.module.css";

export default function System() {
  const system = useSystem();
  const network = useQuery({ queryKey: ["admin", "network"], queryFn: () => ok(api.GET("/api/v1/admin/network")) });
  const s = system.data;
  const n = network.data;
  const e = s?.engine;
  return (
    <SpacePage>
      {system.isPending && <Loading />}
      {system.error && <Failure error={system.error} />}
      {s && (
        <>
          <KpiRow label="The desk">
            <Kpi label="Version" sub={`up since ${new Date(s.started).toLocaleString("en-GB")}`}>
              <span className={c.mono}>{s.version}</span>
            </Kpi>
            <Kpi label="Database" sub={s.store}>
              <span className={c.mono}>{s.database_revision ?? "—"}</span>
            </Kpi>
            <Kpi label="Market in use" sub={s.snapshot_id ?? "none"}>
              <span className={c.mono}>{formatDate(s.valuation)}</span>
            </Kpi>
            <Kpi
              label="Engine"
              sub={e ? `${e.compiled ? "compiled" : "NumPy only"}${e.cuda_build ? ", CUDA" : ""}` : ""}
            >
              <span className={c.mono}>{e?.resolved ?? "—"}</span>
            </Kpi>
          </KpiRow>
          <div className={c.grid}>
            <Panel title="Workers" hint="alive first, then the 12 latest the queue has seen" flush>
              <DataTable
                caption="The desk's workers"
                head={["Worker", "Role", "Host", "Engine", "Concurrency", "Started", "Heartbeat", "GPU"]}
                num={[4]}
                rows={[...(s.workers ?? [])]
                  .sort((a, b) => Number(b.alive) - Number(a.alive) || b.started_at.localeCompare(a.started_at))
                  .slice(0, 12)
                  .map((w) => {
                    const gpu = w.info?.gpu?.[0];
                    return {
                      key: w.worker_id,
                      cells: [
                        <span key="id" className={c.mono}>
                          {w.worker_id}
                        </span>,
                        <span key="r">
                          {w.role} {!w.alive && <span className={c.faint}>(stopped)</span>}
                        </span>,
                        w.host ?? "—",
                        w.engine ?? "—",
                        w.concurrency ?? "—",
                        new Date(w.started_at).toLocaleString("en-GB"),
                        w.heartbeat_at ? new Date(w.heartbeat_at).toLocaleTimeString("en-GB") : "—",
                        gpu
                          ? `${gpu.name ?? "GPU"}, ${formatNumber(gpu.memory_used_mb)} / ${formatNumber(gpu.memory_total_mb)} MB, ${gpu.temperature_c ?? "?"} °C`
                          : "—",
                      ],
                    };
                  })}
              />
            </Panel>
            <Panel title="Sharing" hint="read-only; opening the desk to the LAN is Luca's decision">
              {network.isPending && <Loading lines={1} />}
              {network.error && <Failure error={network.error} />}
              {n && (
                <>
                  <Text size="sm">
                    {n.lan
                      ? "The desk listens on the LAN (OPTLAB_LAN is on)."
                      : "The desk listens on this machine only (127.0.0.1)."}
                  </Text>
                  {(n.blockers ?? []).length > 0 ? (
                    <>
                      <Text size="sm" mt="sm" c="dimmed">
                        What would stop another machine from reaching it:
                      </Text>
                      <ul className={c.list}>
                        {(n.blockers ?? []).map((b) => (
                          <li key={b} className={c.muted}>
                            {b}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <Text size="sm" mt="sm" c="dimmed">
                      Nothing found that would block it.
                    </Text>
                  )}
                </>
              )}
            </Panel>
          </div>
          <div className={c.grid}>
            <Panel title="Backups of the database" hint="checked copies, the newest first" flush>
              <DataTable
                caption="Backups of the database"
                head={["When", "File", "Size, MB"]}
                num={[2]}
                rows={(s.backups ?? []).map((b) => ({
                  key: b.file,
                  cells: [
                    new Date(b.at).toLocaleString("en-GB"),
                    <span key="f" className={c.mono}>
                      {b.file}
                    </span>,
                    formatNumber(b.bytes / 1e6, { digits: 1 }),
                  ],
                }))}
              />
              {(s.backups ?? []).length === 0 && <p className={`${c.faint} ${c.pad}`}>No backup yet.</p>}
            </Panel>
            <Panel title="Copies of the lake" flush>
              <DataTable
                caption="Copies of the data lake"
                head={["When", "Folder"]}
                rows={(s.lake_backups ?? []).map((b) => ({
                  key: b.folder,
                  cells: [
                    new Date(b.at).toLocaleString("en-GB"),
                    <span key="f" className={c.mono}>
                      {b.folder}
                    </span>,
                  ],
                }))}
              />
              {(s.lake_backups ?? []).length === 0 && <p className={`${c.faint} ${c.pad}`}>No copy yet.</p>}
            </Panel>
          </div>
          <div className={c.grid}>
            <Panel title="Settings" hint="environment, then the config file, then the command line" flush>
              <DataTable
                caption="The settings the desk runs with"
                head={["Setting", "Value"]}
                rows={Object.entries(s.settings ?? {}).map(([k, v]) => ({
                  key: k,
                  cells: [
                    <span key="k" className={c.mono}>
                      {k}
                    </span>,
                    <span key="v" className={classes.detail}>
                      {typeof v === "string" ? v : JSON.stringify(v)}
                    </span>,
                  ],
                }))}
              />
            </Panel>
            <Panel title="The market in use" hint="what it was made of">
              <Text size="sm" mb="xs" className={c.mono}>
                {s.snapshot_id ?? "none"}
              </Text>
              {s.origin &&
                Object.entries(s.origin)
                  .filter(([, v]) => typeof v === "string")
                  .map(([part, v]) => <OriginBadge key={part} label={part} origin={String(v)} to={PIPELINES} />)}
              <Text size="sm" mt="md" c="dimmed">
                Jobs:{" "}
                {Object.entries(s.job_counts ?? {}).map(([k, v]) => (
                  <Badge key={k} size="xs" variant="outline" mr={4}>
                    {v} {k}
                  </Badge>
                ))}
              </Text>
            </Panel>
          </div>
        </>
      )}
    </SpacePage>
  );
}
