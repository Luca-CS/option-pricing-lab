// The moving parts: a job's progress where its button was (step, time left, cancel; never a frozen page), and a
// timeline of a trade's events.
import { UnstyledButton } from "@mantine/core";
import type { CSSProperties, ReactNode } from "react";
import { formatDate, formatSeconds } from "../lib/format";
import classes from "./Flow.module.css";

export type JobView = {
  state: "queued" | "running" | "done" | "error" | "cancelled";
  progress?: number | null;
  step?: string | null;
  eta?: number | null;
  error?: string | null;
};

export function JobProgress({ job, label, onCancel }: { job: JobView; label?: string; onCancel?: () => void }) {
  const done = Math.max(0, Math.min(1, job.progress ?? 0));
  const known = job.state === "running" && job.progress !== null && job.progress !== undefined && done > 0;
  const words =
    job.state === "queued"
      ? "queued"
      : job.state === "running"
        ? `${known ? `${Math.round(done * 100)} % · ` : ""}${job.step ?? label ?? "running"}${
            job.eta !== null && job.eta !== undefined ? ` · ${formatSeconds(job.eta)} left` : ""
          }`
        : job.state === "error"
          ? (job.error ?? "failed")
          : job.state;
  return (
    <span
      className={[classes.job, job.state === "error" ? classes.error : ""].join(" ")}
      role="status"
      aria-live="polite"
    >
      <span
        className={classes.bar}
        role="progressbar"
        aria-label={label ?? "progress"}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={known ? Math.round(done * 100) : undefined}
      >
        <span
          className={[classes.fill, known ? "" : classes.indeterminate].join(" ")}
          style={known ? ({ "--done": `${done * 100}%` } as CSSProperties) : undefined}
        />
      </span>
      <span className={classes.text}>{words}</span>
      {onCancel && (job.state === "queued" || job.state === "running") && (
        <UnstyledButton className={classes.muted} onClick={onCancel} td="underline">
          Cancel
        </UnstyledButton>
      )}
    </span>
  );
}

export type TimelineEvent = {
  /** unique in the list */
  id: string;
  date: string;
  kind: "upcoming" | "paid" | "done" | "called" | "missed" | "knocked_in";
  label: ReactNode;
  detail?: ReactNode;
};

const KIND_WORD: Record<TimelineEvent["kind"], string> = {
  upcoming: "to come",
  paid: "paid",
  done: "done",
  called: "called",
  missed: "missed",
  knocked_in: "knocked in",
};

export function Timeline({ events, label }: { events: TimelineEvent[]; label?: string }) {
  return (
    <ol className={classes.timeline} aria-label={label}>
      {events.map((e) => (
        <li key={e.id} className={classes.event}>
          <span className={classes.date}>{formatDate(e.date)}</span>
          <span className={`${classes.mark} ${classes[e.kind]}`} title={KIND_WORD[e.kind]} aria-hidden="true" />
          <span>
            <span className="visually-hidden">{KIND_WORD[e.kind]}: </span>
            {e.label}
            {e.detail && <span className={classes.detail}>{e.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
