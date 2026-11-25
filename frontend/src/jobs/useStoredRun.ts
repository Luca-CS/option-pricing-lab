// The run a risk screen shows: the result of the job it just ran, else the stored run the URL names, else the last
// one stored of that kind. A stored run carries its job result's fields (the store keeps the library's shapes,
// api.md §5), plus its kind and when it was made.
import { useQuery } from "@tanstack/react-query";
import { api, ok } from "../api/client";
import { useRiskRuns } from "../api/queries";
import type { JobResults } from "./results";

export function useStoredRun<K extends "risk" | "ladder" | "scenarios" | "var">(
  kind: K,
  fresh: JobResults[K] | undefined,
  param: string | null,
) {
  const runs = useRiskRuns(kind);
  const id = param ?? runs.data?.items[0]?.run_id ?? null;
  const stored = useQuery({
    queryKey: ["risk", "run", id],
    queryFn: () => ok(api.GET("/api/v1/risk/runs/{run_id}", { params: { path: { run_id: id ?? "" } } })),
    enabled: Boolean(id) && !fresh,
  });
  const run = fresh ?? (stored.data as unknown as JobResults[K] | undefined) ?? null;
  return {
    run,
    runs: runs.data?.items ?? [],
    pending: stored.isPending && Boolean(id) && !fresh,
    none: runs.data && !id,
    error: stored.error ?? runs.error,
  };
}
