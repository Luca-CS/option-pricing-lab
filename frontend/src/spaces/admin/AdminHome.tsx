// The admin's home: is the desk healthy, who computes, what failed, when was the last backup.
import { Badge } from "@mantine/core";
import { Link } from "react-router";
import { useAdminJobs, useSystem } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { kindLabel } from "../../jobs/jobs";
import { formatDate } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

export function AdminHome() {
  const system = useSystem();
  const jobs = useAdminJobs();
  const s = system.data;
  const alive = s?.workers?.filter((w) => w.alive) ?? [];
  const failed = (jobs.data?.items ?? []).filter((j) => j.state === "error");
  const lastBackup = s?.backups?.[0];
  return (
    <SpacePage>
      {system.error && <Failure error={system.error} />}
      <KpiRow label="Health">
        <Kpi label="Desk" sub={s ? `version ${s.version}` : ""}>
          <span className={c.mono}>{s ? (s.ok ? "up" : "down") : "…"}</span>
        </Kpi>
        <Kpi
          label="Workers alive"
          sub={alive.map((w) => `${w.role} on ${w.host ?? "?"}`).join(", ") || "none: jobs wait"}
        >
          <span className={c.mono}>{s ? alive.length : "…"}</span>
        </Kpi>
        <Kpi
          label="Jobs"
          sub={
            s
              ? Object.entries(s.job_counts ?? {})
                  .map(([k, v]) => `${v} ${k}`)
                  .join(", ")
              : ""
          }
        >
          <span className={c.mono}>{failed.length} failed</span>
        </Kpi>
        <Kpi label="Last backup" sub={lastBackup?.file ?? "none yet"}>
          <span className={c.mono}>{lastBackup ? formatDate(lastBackup.at) : "—"}</span>
        </Kpi>
      </KpiRow>
      <div className={c.grid}>
        <Panel title="Workers" hint="the processes that run the jobs; the GPU as each sees it" flush>
          {system.isPending && <Loading />}
          {s && (
            <DataTable
              caption="Workers"
              head={["Worker", "Role", "Engine", "GPU", "Heartbeat"]}
              rows={[...(s.workers ?? [])]
                .sort((a, b) => Number(b.alive) - Number(a.alive))
                .slice(0, 6)
                .map((w) => {
                  const gpu = w.info?.gpu?.[0];
                  return {
                    key: w.worker_id,
                    cells: [
                      <span key="id" className={c.mono}>
                        {w.worker_id}
                      </span>,
                      <span key="r" className={w.alive ? undefined : c.faint}>
                        {w.role}
                        {w.alive ? "" : ", stopped"}
                      </span>,
                      w.engine ?? "—",
                      gpu ? `${gpu.name ?? "GPU"} · ${gpu.temperature_c ?? "?"} °C` : "—",
                      <span key="hb" className={c.faint}>
                        {w.heartbeat_at ? new Date(w.heartbeat_at).toLocaleTimeString("en-GB") : "—"}
                      </span>,
                    ],
                  };
                })}
            />
          )}
        </Panel>
        <Panel title="Recent jobs" hint="everyone's, newest first" flush>
          {jobs.isPending && <Loading />}
          {jobs.data && (
            <DataTable
              caption="Recent jobs"
              head={["Job", "Kind", "State", "Submitted"]}
              rows={jobs.data.items.slice(0, 12).map((j) => ({
                key: j.id,
                cells: [
                  <Link key="id" to={`/jobs/${j.id}`} className={c.mono}>
                    {j.id}
                  </Link>,
                  kindLabel(j.kind),
                  <Badge
                    key="s"
                    variant="light"
                    color={j.state === "error" ? "red" : j.state === "done" ? "accent" : "gray"}
                  >
                    {j.state}
                  </Badge>,
                  <span key="t" className={c.faint}>
                    {formatDate(j.submitted_at)}
                  </span>,
                ],
              }))}
            />
          )}
        </Panel>
      </div>
    </SpacePage>
  );
}
