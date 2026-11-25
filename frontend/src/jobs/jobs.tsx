// Jobs as the person sees them (HANDOFF_REFACTOR_2_UX.md §5): a submission returns at once with its job id, the job
// is followed over Server-Sent Events (`GET /jobs/{id}/events`, api.md §7), it shows in the tray while it runs, and a
// notification says when it ends, with a link to its result. Never a frozen page. The jobs a person had running
// before a reload are picked up again from `GET /jobs`.
import { Anchor } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { api, ok, type Schemas } from "../api/client";
import { ME } from "../auth/session";

export type Job = Schemas["Job"];
export type JobState = Job["state"];

export type Tracked = {
  id: string;
  kind: string;
  label: string;
  /** where its result is shown (default: the job's page) */
  link: string;
  state: JobState;
  progress?: number | null;
  step?: string | null;
  eta?: number | null;
  error?: string | null;
};

const KIND_LABEL: Record<string, string> = {
  quote: "Quote",
  script: "Script price",
  value: "Trade valuation",
  book: "Book valuation",
  risk: "Greeks",
  ladder: "Ladder",
  scenarios: "Scenarios",
  var: "VaR",
  eod: "EOD runs",
  fit: "Vol lab fit",
  reproduce: "Vol lab reproduction",
  replay: "Replay",
  market: "Market from the lake",
  lake_backup: "Lake backup",
  backup: "Backup",
  housekeeping: "Housekeeping",
  lake: "Lake update",
  eod_today: "EOD of the day",
};

/** The kinds of job the desk runs, as the screens name them. */
export const JOB_KINDS = Object.keys(KIND_LABEL);

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.replaceAll("_", " ");
}

const ENDS = ["done", "error", "cancelled"] as const;

// what a finished job of a kind adds to: those lists are read again (a stored run, a saved quote)
const RISK_LISTS = [
  ["risk", "runs"],
  ["risk", "pnl"],
  ["risk", "backtest"],
];
const REFRESHES: Record<string, string[][]> = {
  quote: [["structuring", "quotes"]],
  risk: RISK_LISTS,
  ladder: RISK_LISTS,
  scenarios: RISK_LISTS,
  var: RISK_LISTS,
  eod: RISK_LISTS,
};

type Jobs = {
  jobs: Tracked[];
  track: (id: string, info: { kind: string; label?: string; link?: string }) => void;
  cancel: (id: string) => Promise<void>;
  dismiss: (id: string) => void;
};

const JobsContext = createContext<Jobs | null>(null);

export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<Record<string, Tracked>>({});
  const sources = useRef(new Map<string, EventSource>());
  const queries = useQueryClient();

  const patch = useCallback((id: string, change: Partial<Tracked>) => {
    setJobs((prev) => {
      const current = prev[id];
      return current ? { ...prev, [id]: { ...current, ...change } } : prev;
    });
  }, []);

  const latest = useRef(jobs);
  latest.current = jobs;

  const ended = useCallback(
    (id: string, job: Job) => {
      queries.setQueryData(["job", id], job);
      if (job.state === "done") {
        for (const queryKey of REFRESHES[job.kind] ?? []) void queries.invalidateQueries({ queryKey });
      }
      const current = latest.current[id];
      if (!current) return;
      const error = job.error ? `${job.error.type}: ${job.error.message}` : null;
      patch(id, { state: job.state, progress: job.progress, step: job.step, error });
      notifications.show({
        id: `job-${id}`,
        color: job.state === "done" ? "accent" : job.state === "error" ? "red" : "gray",
        title: `${current.label} ${job.state === "done" ? "is done" : job.state === "error" ? "failed" : "was cancelled"}`,
        message: (
          <Anchor component={Link} to={current.link} size="sm">
            {job.state === "error" ? (error ?? "See what happened") : "Open the result"}
          </Anchor>
        ),
        autoClose: job.state === "error" ? false : 8000,
      });
    },
    [patch, queries],
  );

  const follow = useCallback(
    (id: string) => {
      if (sources.current.has(id) || typeof EventSource === "undefined") return;
      const es = new EventSource(`/api/v1/jobs/${encodeURIComponent(id)}/events`);
      sources.current.set(id, es);
      const close = () => {
        es.close();
        sources.current.delete(id);
      };
      const moving = (ev: MessageEvent<string>) => {
        const data = JSON.parse(ev.data) as Partial<Job>;
        patch(id, { state: data.state ?? "running", progress: data.progress, step: data.step, eta: data.eta });
      };
      es.addEventListener("state", moving);
      es.addEventListener("progress", moving);
      for (const end of ENDS) {
        es.addEventListener(end, (ev: MessageEvent<string>) => {
          close();
          ended(id, JSON.parse(ev.data) as Job);
        });
      }
      es.addEventListener("unauthorized", () => {
        close();
        queries.setQueryData(ME, null);
      });
      es.onerror = () => {
        // the stream ends after the last event; if it broke before, ask once where the job stands
        if (es.readyState === EventSource.CLOSED && sources.current.has(id)) {
          close();
          ok(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id } } }))
            .then((job) => (ENDS.includes(job.state as (typeof ENDS)[number]) ? ended(id, job) : follow(id)))
            .catch(() => patch(id, { state: "error", error: "lost track of this job" }));
        }
      };
    },
    [ended, patch, queries],
  );

  const track = useCallback<Jobs["track"]>(
    (id, info) => {
      setJobs((prev) =>
        prev[id]
          ? prev
          : {
              ...prev,
              [id]: {
                id,
                kind: info.kind,
                label: info.label ?? kindLabel(info.kind),
                link: info.link ?? `/jobs/${id}`,
                state: "queued",
              },
            },
      );
      follow(id);
    },
    [follow],
  );

  const cancel = useCallback(async (id: string) => {
    await ok(api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: id } } }));
  }, []);

  const dismiss = useCallback((id: string) => {
    setJobs((prev) => {
      const { [id]: _, ...rest } = prev;
      return rest;
    });
  }, []);

  // the jobs still running from before a reload
  useEffect(() => {
    ok(api.GET("/api/v1/jobs"))
      .then((list) => {
        for (const j of list.jobs) track(j.id, { kind: j.kind });
      })
      .catch(() => undefined);
    const all = sources.current;
    return () => {
      for (const es of all.values()) es.close();
      all.clear();
    };
  }, [track]);

  const value = useMemo<Jobs>(
    () => ({ jobs: Object.values(jobs).reverse(), track, cancel, dismiss }),
    [jobs, track, cancel, dismiss],
  );
  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export function useJobs(): Jobs {
  const value = useContext(JobsContext);
  if (!value) throw new Error("useJobs outside JobsProvider");
  return value;
}

/** One tracked job by id, or undefined. */
export function useTracked(id: string | null | undefined): Tracked | undefined {
  const { jobs } = useJobs();
  return id ? jobs.find((j) => j.id === id) : undefined;
}
