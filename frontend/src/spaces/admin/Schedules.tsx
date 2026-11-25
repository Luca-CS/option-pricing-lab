// Schedules (Admin › Schedules): the jobs the desk runs by itself (backup, housekeeping, the lake's copy and update,
// the EOD of the day): their days and time, on or off, when they ran last and how it went. An admin changes them or
// runs one now.
import { Anchor, Badge, Button, Chip, Group, Switch, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { kindLabel, useJobs } from "../../jobs/jobs";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { commonClasses as c } from "../common";
import classes from "./Admin.module.css";

type Pipeline = Schemas["Pipeline"];

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function Schedule({ p }: { p: Pipeline }) {
  const queries = useQueryClient();
  const { track } = useJobs();
  const [days, setDays] = useState(p.days.split(","));
  const [at, setAt] = useState(p.at);
  const save = useMutation({
    mutationFn: (body: Schemas["PipelineUpdate"]) =>
      ok(api.PATCH("/api/v1/admin/pipelines/{name}", { params: { path: { name: p.name } }, body })),
    onSuccess: () => void queries.invalidateQueries({ queryKey: ["admin", "pipelines"] }),
  });
  const run = useMutation({
    mutationFn: () => ok(api.POST("/api/v1/admin/pipelines/{name}/run", { params: { path: { name: p.name } } })),
    onSuccess: (a) => {
      track(a.job_id, { kind: p.kind, label: `${p.name}, run now` });
      notifications.show({ color: "accent", title: `${p.name} started`, message: "It shows in the jobs tray." });
    },
  });
  const ordered = DAYS.filter((d) => days.includes(d));
  const changed = ordered.join(",") !== p.days || at !== p.at;
  const atValid = /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(at);
  const last = p.last_job;
  return (
    <Panel
      title={p.name}
      hint={`${kindLabel(p.kind)} · ${p.timezone}`}
      actions={
        <Group gap="xs">
          <Switch
            size="sm"
            label="On"
            checked={p.enabled}
            disabled={save.isPending}
            onChange={(e) => save.mutate({ enabled: e.currentTarget.checked })}
          />
          <Button size="xs" variant="default" loading={run.isPending} onClick={() => run.mutate()}>
            Run now
          </Button>
        </Group>
      }
    >
      <div className={classes.schedule}>
        <Chip.Group multiple value={days} onChange={setDays}>
          <Group gap={4} role="group" aria-label={`Days of ${p.name}`}>
            {DAYS.map((d) => (
              <Chip key={d} value={d} size="xs" variant="outline">
                {d}
              </Chip>
            ))}
          </Group>
        </Chip.Group>
        <TextInput
          size="xs"
          w={90}
          value={at}
          onChange={(e) => setAt(e.currentTarget.value)}
          error={atValid ? undefined : "HH:MM"}
          aria-label={`Time of ${p.name}`}
        />
        <Button
          size="xs"
          disabled={!changed || !atValid || ordered.length === 0}
          loading={save.isPending}
          onClick={() => save.mutate({ days: ordered.join(","), at })}
        >
          Save
        </Button>
      </div>
      <dl className={c.facts}>
        <dt>Next</dt>
        <dd>{p.enabled ? when(p.next_run_at) : "off"}</dd>
        <dt>Last</dt>
        <dd>
          {when(p.last_run_at)}{" "}
          {last && (
            <>
              <Badge
                size="xs"
                variant="light"
                color={last.state === "error" ? "red" : last.state === "done" ? "teal" : "gray"}
              >
                {last.state}
              </Badge>{" "}
              <Anchor component={Link} to={`/jobs/${last.id}`} size="xs" underline="always">
                the job
              </Anchor>
            </>
          )}
        </dd>
        {Object.keys(p.params ?? {}).length > 0 && (
          <>
            <dt>Parameters</dt>
            <dd>{JSON.stringify(p.params)}</dd>
          </>
        )}
        <dt>Changed</dt>
        <dd>
          {when(p.updated_at)}
          {p.updated_by ? ` by ${p.updated_by}` : ""}
        </dd>
      </dl>
      {(save.error ?? run.error) && <Failure error={save.error ?? run.error} title="Not done" />}
    </Panel>
  );
}

export default function Schedules() {
  const pipelines = useQuery({
    queryKey: ["admin", "pipelines"],
    queryFn: () => ok(api.GET("/api/v1/admin/pipelines")),
    refetchInterval: 30_000,
  });
  return (
    <SpacePage>
      {pipelines.isPending && <Loading />}
      {pipelines.error && <Failure error={pipelines.error} />}
      <div className={c.grid}>
        {(pipelines.data?.pipelines ?? []).map((p) => (
          <Schedule key={p.name} p={p} />
        ))}
      </div>
    </SpacePage>
  );
}
