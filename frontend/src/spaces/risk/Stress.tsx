// Stress tests (Risk › Stress): the signed scenario library and the book under it. A risk manager edits the library
// and signs a new version (the server signs it with their name and keeps every version); anyone who may run it sees
// the book under each scenario, each trade under each, and the spot × vol grid.
import { Badge, Button, Checkbox, Drawer, Group, Modal, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { ApiError, api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { useScreenJob } from "../../jobs/useScreenJob";
import { useStoredRun } from "../../jobs/useStoredRun";
import { formatDate } from "../../lib/format";
import { describeShock } from "../../lib/shocks";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable, WhatIfSwitch } from "../common";
import { RunBar } from "../runs";
import { ScenariosView } from "../scenarios";
import classes from "./Risk.module.css";

type Scenario = Schemas["ScenarioDef"];

function Shock({ shock }: { shock: Record<string, unknown> }) {
  const lines = describeShock(shock);
  if (lines.length === 0) return <span className={c.faint}>no move</span>;
  return (
    <ul className={classes.moves}>
      {lines.map((l) => (
        <li key={`${l.what}-${l.move}`}>
          <span className={c.faint}>{l.what}</span> {l.move}
        </li>
      ))}
    </ul>
  );
}

/** One scenario's fields; the shock is JSON as the risk engine reads it (the server checks every field on save). */
function Editor({
  scenario,
  names,
  onDone,
  onClose,
}: {
  scenario: Scenario;
  names: string[];
  onDone: (s: Scenario) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(scenario.name);
  const [description, setDescription] = useState(scenario.description ?? "");
  const [label, setLabel] = useState(scenario.label ?? "");
  const [shock, setShock] = useState(JSON.stringify(scenario.shock ?? {}, null, 2));
  let parsed: Record<string, unknown> | null = null;
  let shockError: string | null = null;
  try {
    const v: unknown = JSON.parse(shock);
    if (v && typeof v === "object" && !Array.isArray(v)) parsed = v as Record<string, unknown>;
    else shockError = 'A shock is an object of fields: { "spot": { "*": -0.2 } }';
  } catch (e) {
    shockError = e instanceof Error ? e.message : "Not JSON";
  }
  const nameError = !name.trim()
    ? "A scenario needs a name"
    : names.includes(name.trim())
      ? "Another scenario has this name"
      : null;
  return (
    <Stack gap="sm">
      <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} error={nameError} />
      <TextInput label="What it does" value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
      <TextInput
        label="Label"
        description="shown beside it, e.g. whether it is still a starting value"
        value={label}
        onChange={(e) => setLabel(e.currentTarget.value)}
      />
      <Textarea
        label="Shock"
        description="moves as fractions (spot −0.2 is −20 %), vols in absolute vol (0.02 is two points), curves in bp"
        value={shock}
        onChange={(e) => setShock(e.currentTarget.value)}
        error={shockError}
        autosize
        minRows={8}
        classNames={{ input: c.mono }}
        spellCheck={false}
      />
      {parsed && (
        <div>
          <Text size="xs" c="dimmed" mb={4}>
            Read as
          </Text>
          <Shock shock={parsed} />
        </div>
      )}
      <Group justify="flex-end" gap="sm">
        <Button variant="default" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="sm"
          disabled={Boolean(nameError || shockError)}
          onClick={() => parsed && onDone({ ...scenario, name: name.trim(), description, label, shock: parsed })}
        >
          Keep it
        </Button>
      </Group>
    </Stack>
  );
}

export default function Stress() {
  const me = useMe().data;
  const editable = can(me, "put_scenarios");
  const queries = useQueryClient();
  const [params] = useSearchParams();
  const library = useQuery({ queryKey: ["risk", "scenarios"], queryFn: () => ok(api.GET("/api/v1/risk/scenarios")) });
  const [draft, setDraft] = useState<Scenario[] | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [signing, setSigning] = useState(false);
  const [note, setNote] = useState("");
  const [grid, setGrid] = useState(true);
  const [simulated, setSimulated] = useState(false);
  const job = useScreenJob("scenarios", "Scenarios on the book", "job");
  const last = useStoredRun("scenarios", job.envelope?.result, params.get("run"));
  const save = useMutation({
    mutationFn: () => ok(api.PUT("/api/v1/risk/scenarios", { body: { scenarios: draft ?? [], note } })),
    onSuccess: (r) => {
      void queries.invalidateQueries({ queryKey: ["risk", "scenarios"] });
      setDraft(null);
      setSigning(false);
      setNote("");
      notifications.show({
        color: "accent",
        title: `Library version ${String((r as { version?: number }).version ?? "")} signed`,
        message: "The next scenario runs read it.",
      });
    },
  });
  const lib = library.data;
  const shown = draft ?? lib?.scenarios ?? [];
  const changed = Boolean(draft && lib && JSON.stringify(draft) !== JSON.stringify(lib.scenarios));
  const blank: Scenario = { name: "", description: "", label: "", shock: { spot: { "*": 0 } } };
  const edited = editing === null ? null : editing < shown.length ? shown[editing] : blank;
  const keep = (s: Scenario) =>
    setDraft((prev) => {
      const next = [...(prev ?? lib?.scenarios ?? [])];
      if (editing !== null && editing < next.length) next[editing] = s;
      else next.push(s);
      return next;
    });

  return (
    <SpacePage>
      {library.isPending && <Loading />}
      {library.error && <Failure error={library.error} />}
      {lib && (
        <Panel
          title="The scenario library"
          hint={
            lib.starting
              ? "the starting values, not signed yet"
              : `version ${lib.version}, signed by ${lib.signed_by} on ${formatDate(lib.created_at)}`
          }
          actions={
            editable &&
            (draft ? (
              <Group gap="xs">
                <Button size="xs" variant="default" onClick={() => setEditing(shown.length)}>
                  Add a scenario
                </Button>
                <Button size="xs" variant="default" onClick={() => setDraft(null)}>
                  Discard
                </Button>
                <Button size="xs" disabled={!changed || shown.length === 0} onClick={() => setSigning(true)}>
                  Sign a new version
                </Button>
              </Group>
            ) : (
              <Button size="xs" variant="default" onClick={() => setDraft(structuredClone(lib.scenarios))}>
                Edit the library
              </Button>
            ))
          }
          flush
        >
          <DataTable
            caption="The scenarios of the library"
            head={["Scenario", "What it does", "The shock", ...(draft ? [""] : [])]}
            rows={shown.map((s, i) => ({
              key: `${i}-${s.name}`,
              cells: [
                <span key="n">
                  {s.name}{" "}
                  {s.label && (
                    <Badge size="xs" variant="outline" color="gray" title={s.label}>
                      {s.label.startsWith("starting") ? "starting value" : s.label}
                    </Badge>
                  )}
                </span>,
                <span key="d" className={`${c.faint} ${c.wrap}`}>
                  {s.description}
                </span>,
                <Shock key="s" shock={s.shock ?? {}} />,
                ...(draft
                  ? [
                      <Group key="a" gap={4} wrap="nowrap">
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          onClick={() => setEditing(i)}
                          aria-label={`Edit ${s.name}`}
                        >
                          Edit
                        </Button>
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          aria-label={`Copy ${s.name}`}
                          onClick={() => setDraft([...shown, { ...structuredClone(s), name: `${s.name} (copy)` }])}
                        >
                          Copy
                        </Button>
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="red"
                          aria-label={`Remove ${s.name}`}
                          onClick={() => setDraft(shown.filter((_, k) => k !== i))}
                        >
                          Remove
                        </Button>
                      </Group>,
                    ]
                  : []),
              ],
            }))}
          />
          {lib.note && !lib.starting && !draft && <p className={`${c.faint} ${c.pad}`}>{lib.note}</p>}
        </Panel>
      )}
      <Drawer
        opened={edited !== null}
        onClose={() => setEditing(null)}
        title={edited?.name ? `Edit ${edited.name}` : "A new scenario"}
        position="right"
        size="lg"
      >
        {edited && (
          <Editor
            key={editing}
            scenario={edited}
            names={shown.filter((_, k) => k !== editing).map((s) => s.name)}
            onClose={() => setEditing(null)}
            onDone={(s) => {
              keep(s);
              setEditing(null);
            }}
          />
        )}
      </Drawer>
      <Modal opened={signing} onClose={() => setSigning(false)} title="Sign a new version of the library">
        <Stack gap="sm">
          <Text size="sm">
            The new version is signed with your name, {me?.name}, and every scenario run after it reads it. The versions
            before it are kept.
          </Text>
          <TextInput
            label="Note"
            description="what changed, and why"
            value={note}
            onChange={(e) => setNote(e.currentTarget.value)}
          />
          {save.error && (
            <Failure
              error={save.error}
              title={
                save.error instanceof ApiError && save.error.status === 422 ? "The library was refused" : "Not saved"
              }
            />
          )}
          <Group justify="flex-end" gap="sm">
            <Button variant="default" size="sm" onClick={() => setSigning(false)}>
              Cancel
            </Button>
            <Button size="sm" loading={save.isPending} disabled={!note.trim()} onClick={() => save.mutate()}>
              Sign
            </Button>
          </Group>
        </Stack>
      </Modal>
      <Panel title="The book under the library" hint="desk side; each scenario a full revaluation on the base's seed">
        <RunBar
          operation="run_scenarios"
          label="Run the library on the book"
          run={last.run}
          job={job}
          submit={(precision) =>
            ok(
              api.POST("/api/v1/risk/scenarios/runs", {
                body: { options: { precision, include_simulated: simulated }, grid },
              }),
            )
          }
        >
          <Checkbox
            size="xs"
            label="Spot × vol grid"
            checked={grid}
            onChange={(e) => setGrid(e.currentTarget.checked)}
          />
          <WhatIfSwitch checked={simulated} onChange={setSimulated} />
        </RunBar>
      </Panel>
      {job.failed && <Failure error={new Error(job.failed)} title="The run failed" />}
      {last.pending && <Loading />}
      {last.none && !last.run && <Empty title="No scenario run yet">Run the library on the book to see its P&L.</Empty>}
      {last.run && <ScenariosView run={last.run} />}
      {lib && (lib.history ?? []).length > 0 && (
        <Panel title="Versions" hint="every signed version is kept" flush>
          <DataTable
            caption="The signed versions of the library"
            head={["Version", "Signed by", "Note", "When"]}
            num={[0]}
            rows={[...(lib.history ?? [])].reverse().map((v) => ({
              key: String(v.version),
              cells: [
                v.version,
                String(v.meta?.signed_by ?? "—"),
                <span key="n" className={c.faint}>
                  {String(v.meta?.note ?? "")}
                </span>,
                formatDate(v.created_at),
              ],
            }))}
          />
        </Panel>
      )}
    </SpacePage>
  );
}
