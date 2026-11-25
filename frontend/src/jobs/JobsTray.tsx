import { ActionIcon, Indicator, Popover, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconActivity, IconX } from "@tabler/icons-react";
import { Link } from "react-router";
import { JobProgress } from "../ui/Flow";
import classes from "./JobsTray.module.css";
import { useJobs } from "./jobs";

/** The jobs of this session: running ones with their progress and a cancel, ended ones with a link to the result. */
export function JobsTray() {
  const { jobs, cancel, dismiss } = useJobs();
  const running = jobs.filter((j) => j.state === "queued" || j.state === "running");
  return (
    <Popover position="bottom-end" width={380} withinPortal shadow="md">
      <Popover.Target>
        <ActionIcon
          variant="default"
          size="lg"
          aria-label={running.length ? `Jobs, ${running.length} running` : "Jobs"}
        >
          <Indicator label={running.length} size={15} disabled={running.length === 0} color="accent" offset={-2}>
            <IconActivity size={17} />
          </Indicator>
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown>
        <Text fw={600} size="sm" mb={8}>
          Jobs of this session
        </Text>
        {jobs.length === 0 ? (
          <Text size="sm" c="dimmed">
            Nothing computed yet. A quote, a valuation or a risk run shows here while it runs and stays until you
            dismiss it.
          </Text>
        ) : (
          <Stack gap={10}>
            {jobs.map((j) => (
              <div key={j.id} className={classes.job}>
                <div className={classes.top}>
                  <Link to={j.link} className={classes.name}>
                    {j.label}
                  </Link>
                  <span className={classes.id}>{j.id}</span>
                  {j.state !== "queued" && j.state !== "running" && (
                    <UnstyledButton
                      onClick={() => dismiss(j.id)}
                      aria-label={`Dismiss ${j.label}`}
                      className={classes.x}
                    >
                      <IconX size={13} />
                    </UnstyledButton>
                  )}
                </div>
                <JobProgress job={j} label={j.label} onCancel={() => void cancel(j.id)} />
              </div>
            ))}
          </Stack>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}
