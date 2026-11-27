// The pricing policy (Trading › Pricing policy): the desk's cost table by group of underlyings and size band, as the
// quotes read it, and the hedging instruments' half-spreads and swap spreads beside it. A trader edits it and saves a
// new version; a value still at its starting value is in italics (an assumption), so what the desk has decided shows
// apart from what it was given.
import { Badge, Button, Group, Tabs, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { formatDate, formatNumber } from "../../lib/format";
import { display, parse, suffix } from "../../lib/sheet";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { commonClasses as c } from "../common";
import classes from "./Costs.module.css";

type Groups = Record<string, Schemas["CostGroup"]>;
type HedgeRows = Schemas["HedgeRows"];

function HedgeTable({
  rows,
  fields,
  starting,
  editable,
  onChange,
}: {
  rows: HedgeRows;
  fields: Schemas["CostField"][];
  starting: string[];
  editable: boolean;
  onChange: (key: string, value: number, currency?: string) => void;
}) {
  const plain = fields.filter((f) => f.name !== "swap_spread_bp");
  const spread = fields.find((f) => f.name === "swap_spread_bp");
  const cell = (key: string, value: number, label: string, currency?: string) => {
    const atStart = starting.includes(currency ? `${key}.${currency}` : key);
    return editable ? (
      <TextInput
        size="xs"
        aria-label={label}
        defaultValue={String(value)}
        onBlur={(e) => {
          const x = Number(e.currentTarget.value);
          if (Number.isFinite(x)) onChange(key, x, currency);
        }}
        classNames={{ input: atStart ? classes.starting : classes.decided }}
      />
    ) : (
      <span className={atStart ? classes.starting : undefined}>{formatNumber(value, { digits: 2 })}</span>
    );
  };
  return (
    <Panel
      title="Hedging instruments"
      hint="half-spreads per unit, and the swap curve's proxy; italics: an assumption"
      flush
    >
      <div className={classes.frame}>
        <table className={classes.table}>
          <caption className="visually-hidden">The hedging instruments' costs</caption>
          <thead>
            <tr>
              <th scope="col">Line</th>
              <th scope="col" className={classes.num}>
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            {plain.map((f) => (
              <tr key={f.name}>
                <th scope="row">
                  {f.label} <span className={c.faint}>{f.unit}</span>
                </th>
                <td className={classes.num}>
                  {cell(f.name, Number((rows as Record<string, unknown>)[f.name]), f.label)}
                </td>
              </tr>
            ))}
            {spread &&
              Object.entries(rows.swap_spread_bp).map(([ccy, v]) => (
                <tr key={ccy}>
                  <th scope="row">
                    {spread.label}, {ccy} <span className={c.faint}>{spread.unit}</span>
                  </th>
                  <td className={classes.num}>{cell("swap_spread_bp", v, `${spread.label}, ${ccy}`, ccy)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function Costs() {
  const me = useMe().data;
  const editable = can(me, "put_cost_defaults");
  const queries = useQueryClient();
  const table = useQuery({
    queryKey: ["trading", "costs"],
    queryFn: () => ok(api.GET("/api/v1/trading/cost-defaults")),
  });
  const [groups, setGroups] = useState<Groups | null>(null);
  const [hedges, setHedges] = useState<HedgeRows | null>(null);
  useEffect(() => {
    if (table.data) {
      setGroups(structuredClone(table.data.groups));
      setHedges(structuredClone(table.data.hedges));
    }
  }, [table.data]);
  const save = useMutation({
    mutationFn: () =>
      ok(
        api.PUT("/api/v1/trading/cost-defaults", {
          body: { groups: groups ?? {}, ...(hedges ? { hedges: hedges as Record<string, unknown> } : {}) },
        }),
      ),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["trading", "costs"] });
      notifications.show({ color: "accent", title: "Cost table saved", message: "New quotes read this version." });
    },
  });
  const fields = table.data?.fields ?? [];
  const starting = table.data?.starting ?? {};
  const changed = Boolean(
    table.data &&
      ((groups && JSON.stringify(groups) !== JSON.stringify(table.data.groups)) ||
        (hedges && JSON.stringify(hedges) !== JSON.stringify(table.data.hedges))),
  );
  const setHedge = (key: string, value: number, currency?: string) =>
    setHedges((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev) as HedgeRows & Record<string, unknown>;
      if (currency) next.swap_spread_bp = { ...next.swap_spread_bp, [currency]: value };
      else next[key] = value;
      return next;
    });
  const setCell = (g: string, i: number, field: string, value: unknown) =>
    setGroups((prev) => {
      if (!prev?.[g]) return prev;
      const next = structuredClone(prev);
      const tier = next[g]?.tiers[i] as Record<string, unknown> | undefined;
      if (tier) tier[field] = value;
      return next;
    });

  return (
    <SpacePage>
      <Group gap="sm">
        <Text size="sm" c="dimmed">
          {table.data?.version
            ? `Version ${table.data.version}, ${formatDate(table.data.recorded_at)}`
            : "The starting table, not saved yet"}
          {" · "}
          {table.data?.versions?.length ?? 0} versions kept
        </Text>
        <span className={c.spacer} />
        {editable && (
          <>
            <Button
              size="xs"
              variant="default"
              disabled={!changed}
              onClick={() => {
                if (table.data) {
                  setGroups(structuredClone(table.data.groups));
                  setHedges(structuredClone(table.data.hedges));
                }
              }}
            >
              Discard
            </Button>
            <Button size="xs" disabled={!changed} loading={save.isPending} onClick={() => save.mutate()}>
              Save a new version
            </Button>
          </>
        )}
      </Group>
      {table.isPending && <Loading />}
      {table.error && <Failure error={table.error} />}
      {save.error && <Failure error={save.error} title="Not saved" />}
      {groups && (
        <Tabs defaultValue={Object.keys(groups)[0]}>
          <Tabs.List>
            {Object.entries(groups).map(([k, g]) => (
              <Tabs.Tab key={k} value={k}>
                {g.label}{" "}
                <Badge size="xs" variant="outline" ml={4}>
                  {g.currency}
                </Badge>
              </Tabs.Tab>
            ))}
          </Tabs.List>
          {Object.entries(groups).map(([k, g]) => (
            <Tabs.Panel key={k} value={k} pt="md">
              <Panel title={g.label} hint="by size band, up to its notional; italics: still the starting value" flush>
                <div className={classes.frame}>
                  <table className={classes.table}>
                    <caption className="visually-hidden">{`Cost table of ${g.label}`}</caption>
                    <thead>
                      <tr>
                        <th scope="col">Line</th>
                        {g.tiers.map((t, i) => (
                          <th scope="col" key={String(t.max_notional ?? i)} className={classes.num}>
                            up to {formatNumber(t.max_notional)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {fields.map((f) => (
                        <tr key={f.name}>
                          <th scope="row">
                            {f.label} <span className={c.faint}>{suffix(f)}</span>
                          </th>
                          {g.tiers.map((t, i) => {
                            const value = (t as Record<string, unknown>)[f.name];
                            const start = (starting[k]?.tiers[i] as Record<string, unknown> | undefined)?.[f.name];
                            const atStart = JSON.stringify(value) === JSON.stringify(start);
                            return (
                              <td key={String(t.max_notional ?? i)} className={classes.num}>
                                {editable ? (
                                  <TextInput
                                    size="xs"
                                    aria-label={`${f.label}, up to ${formatNumber(t.max_notional)}`}
                                    defaultValue={display(f, value)}
                                    onBlur={(e) => setCell(k, i, f.name, parse(f, e.currentTarget.value))}
                                    classNames={{ input: atStart ? classes.starting : classes.decided }}
                                  />
                                ) : (
                                  <span className={atStart ? classes.starting : undefined}>{display(f, value)}</span>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </Tabs.Panel>
          ))}
        </Tabs>
      )}
      {hedges && table.data && (
        <HedgeTable
          rows={hedges}
          fields={table.data.hedge_fields ?? []}
          starting={table.data.hedges_starting ?? []}
          editable={editable}
          onChange={setHedge}
        />
      )}
    </SpacePage>
  );
}
