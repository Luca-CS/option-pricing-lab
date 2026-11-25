// The bar above a risk run: which run is shown (its id, its day, how long it took), and, for whoever may start one,
// the precision and the button, replaced by the job's progress while it runs.
import { Button, Group, SegmentedControl, Text } from "@mantine/core";
import { type ReactNode, useState } from "react";
import type { Schemas } from "../api/client";
import { can, useMe } from "../auth/session";
import type { useScreenJob } from "../jobs/useScreenJob";
import { formatDate, formatSeconds } from "../lib/format";
import { JobProgress } from "../ui/Flow";
import { commonClasses as c } from "./common";

export type Precision = "quick" | "standard" | "precise";

const PRECISION = [
  { value: "quick", label: "Quick" },
  { value: "standard", label: "Standard" },
  { value: "precise", label: "Precise" },
];

export function RunBar({
  operation,
  label,
  run,
  job,
  submit,
  children,
}: {
  /** the API operation that starts it (the user's permissions name it) */
  operation: string;
  label: string;
  run: { run_id: string; seconds?: number | null; meta?: Record<string, unknown> } | null;
  job: ReturnType<typeof useScreenJob>;
  submit: (precision: Precision) => Promise<Schemas["JobAccepted"]>;
  /** more settings, beside the precision */
  children?: ReactNode;
}) {
  const me = useMe().data;
  const [precision, setPrecision] = useState<Precision>("quick");
  const meta = (run?.meta ?? {}) as { valuation?: string };
  return (
    <Group gap="sm" wrap="wrap">
      <Text size="sm" c="dimmed">
        {run
          ? `${run.run_id} · ${meta.valuation ? formatDate(meta.valuation) : ""} · ${formatSeconds(run.seconds)}`
          : "No run yet"}
      </Text>
      <span className={c.spacer} />
      {can(me, operation) &&
        (job.running && job.tracked ? (
          <JobProgress job={job.tracked} label={label} onCancel={() => void job.cancel()} />
        ) : (
          <>
            {children}
            <SegmentedControl
              size="xs"
              value={precision}
              onChange={(v) => setPrecision(v as Precision)}
              data={PRECISION}
              aria-label="Precision"
            />
            <Button size="xs" loading={job.start.isPending} onClick={() => job.start.mutate(() => submit(precision))}>
              {label}
            </Button>
          </>
        ))}
    </Group>
  );
}
