// Every job (Admin › Jobs): who submitted it or which schedule, its state and times, the newest first, page by page.
// A job still waiting or running can be cancelled; each opens on its own page.
import { Anchor, Badge, Button, Group, Select } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { JOB_KINDS, kindLabel } from "../../jobs/jobs";
import { formatSeconds } from "../../lib/format";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

type Row = Schemas["JobRow"];

const STATE_COLOR: Record<string, string> = {
  queued: "gray",
  running: "accent",
  done: "teal",
  error: "red",
  cancelled: "gray",
};

function took(j: Row): string {
  if (!j.started_at) return "—";
  const end = j.finished_at ? new Date(j.finished_at) : new Date();
  return formatSeconds((end.getTime() - new Date(j.started_at).getTime()) / 1000);
}

export default function Jobs() {
  const queries = useQueryClient();
  // a job's page links here with ?kind= (a failed job among the others of its kind)
  const [params] = useSearchParams();
  const [kind, setKind] = useState<string | null>(params.get("kind"));
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const jobs = useQuery({
    queryKey: ["admin", "jobs", kind, cursor],
    queryFn: () =>
      ok(api.GET("/api/v1/admin/jobs", { params: { query: { limit: 50, kind: kind ?? undefined, cursor } } })),
    refetchInterval: 5_000,
  });
  const cancel = useMutation({
    mutationFn: (id: string) => ok(api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: id } } })),
    onSettled: () => void queries.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
  const items = jobs.data?.items ?? [];
  return (
    <SpacePage>
      <Group gap="sm" align="flex-end">
        <Select
          label="Kind"
          size="sm"
          w={220}
          clearable
          placeholder="every kind"
          data={JOB_KINDS.map((k) => ({ value: k, label: kindLabel(k) }))}
          value={kind}
          onChange={(v) => {
            setKind(v);
            setCursors([]);
          }}
        />
      </Group>
      {cancel.error && <Failure error={cancel.error} title="Not cancelled" />}
      <Panel title="Jobs" hint="the newest first; refreshed every 5 seconds" flush>
        {jobs.isPending && <Loading />}
        {jobs.error && <Failure error={jobs.error} />}
        <DataTable
          caption="Every job of the desk"
          head={["Job", "Kind", "State", "By", "Submitted", "Took", "Engine", ""]}
          num={[5]}
          rows={items.map((j) => ({
            key: j.id,
            cells: [
              <Anchor key="id" component={Link} to={`/jobs/${j.id}`} className={c.mono}>
                {j.id.slice(0, 10)}
              </Anchor>,
              kindLabel(j.kind),
              <Badge key="s" size="xs" variant="light" color={STATE_COLOR[j.state] ?? "gray"}>
                {j.state}
                {j.state === "running" && j.progress !== null && j.progress !== undefined
                  ? ` ${Math.round(j.progress * 100)} %`
                  : ""}
              </Badge>,
              <span key="b" className={c.mono}>
                {j.schedule ? `schedule ${j.schedule}` : (j.user_id ?? "—")}
              </span>,
              new Date(j.submitted_at).toLocaleString("en-GB"),
              took(j),
              j.engine ?? "—",
              j.state === "queued" || j.state === "running" ? (
                <Button
                  key="x"
                  size="compact-xs"
                  variant="subtle"
                  color="red"
                  loading={cancel.isPending && cancel.variables === j.id}
                  onClick={() => cancel.mutate(j.id)}
                  aria-label={`Cancel job ${j.id}`}
                >
                  Cancel
                </Button>
              ) : j.error ? (
                <span key="e" className={c.faint} title={JSON.stringify(j.error)}>
                  {String((j.error as { type?: string }).type ?? "error")}
                </span>
              ) : (
                ""
              ),
            ],
          }))}
        />
      </Panel>
      <Group gap="sm">
        <Button
          size="xs"
          variant="default"
          disabled={cursors.length === 0}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          Newer
        </Button>
        <Button
          size="xs"
          variant="default"
          disabled={!jobs.data?.next}
          onClick={() => jobs.data?.next && setCursors([...cursors, jobs.data.next])}
        >
          Older
        </Button>
      </Group>
    </SpacePage>
  );
}
