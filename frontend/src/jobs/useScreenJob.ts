// A screen that starts a job and shows its result: the job's id lives in the URL (so a link or a reload shows the
// same result), its progress in the tray and on the screen, its result once done.
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import type { Schemas } from "../api/client";
import { useJobs, useTracked } from "./jobs";
import { type JobResults, useJobResult } from "./results";

export function useScreenJob<K extends keyof JobResults>(kind: K, label: string, param = "job") {
  const [params, setParams] = useSearchParams();
  const fromUrl = params.get(param);
  // the job just started, held here: the router applies a search change in a transition, which a busy page (a job's
  // events arriving) may hold back, so the screen does not wait for the URL to show its own job
  const [started, setStarted] = useState<string | null>(null);
  useEffect(() => {
    if (fromUrl) setStarted(null);
  }, [fromUrl]);
  const jobId = started ?? fromUrl;
  const { track, cancel } = useJobs();
  const tracked = useTracked(jobId);
  const running = tracked?.state === "queued" || tracked?.state === "running";
  const { job, envelope } = useJobResult(kind, jobId, Boolean(tracked));
  // a link to a job still running (from another tab, a notification, a test): followed here as if started here
  const state = job.data?.state;
  useEffect(() => {
    if (jobId && !tracked && (state === "queued" || state === "running")) {
      track(jobId, { kind, label, link: `${window.location.pathname}${window.location.search}` });
    }
  }, [jobId, tracked, state, track, kind, label]);
  const start = useMutation({
    mutationFn: (submit: () => Promise<Schemas["JobAccepted"]>) => submit(),
    onSuccess: (accepted) => {
      const next = new URLSearchParams(params);
      next.set(param, accepted.job_id);
      track(accepted.job_id, { kind, label, link: `${window.location.pathname}?${next.toString()}` });
      setStarted(accepted.job_id);
      setParams(next, { replace: true });
    },
  });
  const failed = job.data?.state === "error" ? `${job.data.error?.type}: ${job.data.error?.message}` : null;
  return {
    jobId,
    tracked,
    running,
    envelope,
    failed,
    start,
    cancel: () => (jobId ? cancel(jobId) : Promise.resolve()),
  };
}
