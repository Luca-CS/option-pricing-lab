// A job's page (the bridge "job → its result", HANDOFF_REFACTOR_2_UX.md §3): what it is, where it stands, on what
// market and by what method it computed, the error if it failed, and the screen that shows its result.
import { Anchor, Badge, Button, Code, Group } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useParams } from "react-router";
import { api, ok } from "../api/client";
import { SpacePage } from "../app/Shell";
import { useMe } from "../auth/session";
import { kindLabel, useJobs, useTracked } from "../jobs/jobs";
import { formatDate, formatSeconds } from "../lib/format";
import { JobProgress } from "../ui/Flow";
import { MarketBadge } from "../ui/Markers";
import { Panel } from "../ui/Panel";
import { Failure, Loading } from "../ui/States";
import classes from "./JobPage.module.css";

type Envelope = {
  market?: { snapshot_id?: string | null; as_of?: string | null; origin?: unknown } | null;
  method?: Record<string, unknown> | null;
  result?: unknown;
};

/** The screen that shows a finished job's result, from its kind and its result's ids. */
export function resultScreen(kind: string, result: unknown, job = ""): { label: string; to: string } | null {
  const r = (result ?? {}) as Record<string, unknown>;
  const id = (k: string) => (typeof r[k] === "string" ? encodeURIComponent(r[k] as string) : null);
  switch (kind) {
    case "quote":
      return id("quote_id") ? { label: "Open the quote", to: `/structuring/quotes/${id("quote_id")}` } : null;
    case "book":
      return { label: "Open the book", to: "/trading/book" };
    case "value":
      return id("trade_id") ? { label: "Open the trade", to: `/trading/trades/${id("trade_id")}` } : null;
    case "risk":
    case "ladder":
    case "scenarios":
      return id("run_id") ? { label: "Open the book risk", to: `/trading/risk?run=${id("run_id")}` } : null;
    case "var":
      return id("run_id") ? { label: "Open the VaR", to: `/risk/var/${id("run_id")}` } : null;
    case "eod":
      return { label: "Open the P&L", to: "/trading/pnl" };
    case "replay":
      return { label: "Open the replay", to: `/quant/replay?job=${encodeURIComponent(job)}` };
    case "script":
      return { label: "Open the script lab", to: `/quant/script?job=${encodeURIComponent(job)}` };
    case "fit":
      return { label: "Open the fit", to: `/quant/vollab?job=${encodeURIComponent(job)}` };
    case "reproduce":
      return { label: "Open the vol lab", to: `/quant/vollab?again=${encodeURIComponent(job)}` };
    default:
      return null;
  }
}

export function JobPage() {
  const { id = "" } = useParams();
  const { track, cancel } = useJobs();
  const tracked = useTracked(id);
  const job = useQuery({
    queryKey: ["job", id],
    queryFn: () => ok(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id } } })),
  });
  const j = job.data;
  const admin = useMe().data?.roles.includes("admin") ?? false;
  const running = j?.state === "queued" || j?.state === "running";
  useEffect(() => {
    if (j && running) track(j.id, { kind: j.kind });
  }, [j, running, track]);
  useEffect(() => {
    if (tracked && tracked.state !== "queued" && tracked.state !== "running") void job.refetch();
  }, [tracked?.state, tracked, job.refetch]);

  const env = (j?.result ?? null) as Envelope | null;
  const screen = j?.state === "done" ? resultScreen(j.kind, env?.result, j.id) : null;
  const origin = env?.market?.origin as { surface?: string } | string | null | undefined;
  return (
    <SpacePage title={j ? `${kindLabel(j.kind)} · job ${j.id}` : "Job"}>
      {job.isPending && <Loading />}
      {job.error && <Failure error={job.error} title="This job cannot be read" />}
      {j && (
        <>
          <Panel title="Where it stands">
            <Group gap="md" align="center">
              <Badge variant="light" color={j.state === "done" ? "accent" : j.state === "error" ? "red" : "gray"}>
                {j.state}
              </Badge>
              {running && (
                <JobProgress
                  job={tracked ?? { state: j.state, progress: j.progress, step: j.step, eta: j.eta }}
                  onCancel={() => void cancel(j.id)}
                />
              )}
              {screen && (
                <Button component={Link} to={screen.to} size="xs">
                  {screen.label}
                </Button>
              )}
            </Group>
            <dl className={classes.facts}>
              <dt>Submitted</dt>
              <dd>
                {formatDate(j.submitted_at)} {new Date(j.submitted_at).toLocaleTimeString("en-GB")}
              </dd>
              <dt>Took</dt>
              <dd>{formatSeconds(j.elapsed)}</dd>
              <dt>Engine</dt>
              <dd>{j.engine ?? "—"}</dd>
              <dt>Step</dt>
              <dd>{j.step ?? "—"}</dd>
            </dl>
          </Panel>
          {j.error && <Failure error={new Error(`${j.error.type}: ${j.error.message}`)} title="It failed" />}
          {env && (
            <Panel title="How it computed">
              {env.market?.snapshot_id && (
                <MarketBadge
                  market={{
                    snapshot_id: env.market.snapshot_id,
                    as_of: env.market.as_of ?? null,
                    origin: typeof origin === "string" ? origin : (origin?.surface ?? null),
                  }}
                />
              )}
              {env.method && (
                <p className={classes.method}>
                  {Object.entries(env.method)
                    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                    .join(" · ")}
                </p>
              )}
              <details>
                <summary>The whole result, as the API sent it</summary>
                <Code block className={classes.raw}>
                  {JSON.stringify(env.result, null, 1)}
                </Code>
              </details>
            </Panel>
          )}
          {/* bridge 8: a failed job, or one without a screen, opens among the desk's jobs of its kind (Admin) */}
          {admin && !screen && (j.state === "done" || j.state === "error") && (
            <Anchor component={Link} to={`/admin/jobs?kind=${encodeURIComponent(j.kind)}`} size="sm" underline="always">
              The desk's {kindLabel(j.kind).toLowerCase()} jobs (Admin)
            </Anchor>
          )}
        </>
      )}
    </SpacePage>
  );
}
