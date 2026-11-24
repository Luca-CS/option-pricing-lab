// The states of a screen that is not showing data yet: loading, empty (saying what to do), failed (saying what the
// server said, field by field).
import { Alert, Skeleton, Stack } from "@mantine/core";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import classes from "./States.module.css";

export function Loading({ lines = 3, label = "Loading" }: { lines?: number; label?: string }) {
  return (
    <Stack gap={8} aria-busy="true" aria-label={label}>
      {Array.from({ length: lines }, (_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: placeholders have no identity
        <Skeleton key={i} height={14} radius="sm" width={i === lines - 1 ? "60%" : "100%"} />
      ))}
    </Stack>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={classes.empty}>
      <strong className={classes.title}>{title}</strong>
      {children && <p className={classes.text}>{children}</p>}
      {action}
    </div>
  );
}

export function Failure({ error, title = "This did not work" }: { error: unknown; title?: string }) {
  const api = error instanceof ApiError ? error : null;
  const fields = api ? Object.entries(api.fields()) : [];
  const message = api ? api.message : error instanceof Error ? error.message : "The desk cannot be reached.";
  return (
    <Alert color="red" variant="light" title={title} role="alert">
      <p className={classes.text}>{message}</p>
      {fields.length > 0 && (
        <ul className={classes.fields}>
          {fields.map(([field, text]) => (
            <li key={field}>
              <code>{field}</code>: {text}
            </li>
          ))}
        </ul>
      )}
    </Alert>
  );
}
