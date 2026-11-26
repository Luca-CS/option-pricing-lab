// A finished job's result, typed by its kind. The document lists the schema of each kind's result under
// `x-job-results` (optlab.server.payloads.JOB_RESULTS); this map mirrors it with the generated types, so a screen
// reads a result as the server describes it.
import { useQuery } from "@tanstack/react-query";
import { api, ok, type Schemas } from "../api/client";

export type JobResults = {
  quote: Schemas["QuoteResult"];
  value: Schemas["TradeValuation"];
  book: Schemas["BookValuation"];
  risk: Schemas["RiskRun"];
  ladder: Schemas["LadderRun"];
  scenarios: Schemas["ScenarioRun"];
  var: Schemas["VarRun"];
  eod: Schemas["EodResult"];
  pnl_detail: Schemas["PnlDetail"];
  script: Schemas["ScriptPrice"];
  replay: Schemas["Replay"];
  fit: Schemas["VolFit"];
  reproduce: Schemas["VolFitReproduction"];
};

export type Envelope<K extends keyof JobResults> = {
  market: Schemas["MarketRef"] | null | undefined;
  method: Schemas["Method"] | null | undefined;
  result: JobResults[K];
};

/** The job, and its envelope once it is done (null before, or if it is of another kind). A job `followed` by its
 * events (the jobs tray) gets its end from them; another is asked again every two seconds until it ends. */
export function useJobResult<K extends keyof JobResults>(kind: K, jobId: string | null | undefined, followed: boolean) {
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => ok(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId ?? "" } } })),
    enabled: Boolean(jobId),
    refetchInterval: (q) =>
      q.state.data && ["queued", "running"].includes(q.state.data.state) && !followed ? 2000 : false,
  });
  const j = job.data;
  const envelope = j && j.state === "done" && j.kind === kind && j.result ? (j.result as unknown as Envelope<K>) : null;
  return { job, envelope };
}
