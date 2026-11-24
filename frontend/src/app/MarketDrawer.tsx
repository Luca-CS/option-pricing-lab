// The market drawer, open from anywhere (HANDOFF_REFACTOR_2_UX.md §3): the market in use, where each part of it
// comes from, its key levels, and the stored snapshots a trader may switch the desk to.
import { Anchor, Button, Drawer, Group, Stack, Table, Text } from "@mantine/core";
import { modals } from "@mantine/modals";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, ok } from "../api/client";
import { useMarketContext, useSnapshots } from "../api/queries";
import { can, useMe } from "../auth/session";
import { formatDate, formatNumber, formatPercent } from "../lib/format";
import { OriginBadge } from "../ui/Markers";
import { Failure, Loading } from "../ui/States";
import { Num } from "../ui/Value";
import classes from "./MarketDrawer.module.css";

const PARTS = ["surface", "carry", "discount", "heston", "leverage"] as const;

export function MarketDrawer({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const context = useMarketContext();
  const snapshots = useSnapshots();
  const me = useMe().data;
  const queries = useQueryClient();
  const use = useMutation({
    mutationFn: (snapshot_id: string) => ok(api.PUT("/api/v1/market/in-use", { body: { snapshot_id } })),
    onSuccess: () => queries.invalidateQueries({ queryKey: ["market"] }),
  });
  const c = context.data;
  const tenors = c ? Object.keys(c.atm_vols).sort((a, b) => Number(a) - Number(b)) : [];

  return (
    <Drawer opened={opened} onClose={onClose} position="right" size={460} title="Market in use">
      {context.isPending && <Loading />}
      {context.error && <Failure error={context.error} />}
      {c && (
        <Stack gap="md">
          <div>
            <Text className={classes.mono} size="sm">
              {c.snapshot_id}
            </Text>
            <Text size="sm" c="dimmed">
              {c.underlying} · valued {formatDate(c.valuation)} · calibrated up to{" "}
              {formatNumber(c.t_max, { digits: 1 })} years · model spot <Num value={c.spot} digits={0} />
            </Text>
          </div>
          <Group gap={6}>
            {PARTS.map((p) => (c.origin[p] ? <OriginBadge key={p} label={p} origin={c.origin[p]} /> : null))}
          </Group>
          <Table className={classes.table} withRowBorders={false} verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Maturity</Table.Th>
                <Table.Th>ATM vol</Table.Th>
                <Table.Th>Zero rate</Table.Th>
                <Table.Th>Carry</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {tenors.map((t) => (
                <Table.Tr key={t}>
                  <Table.Td className={classes.mono}>{t} y</Table.Td>
                  <Table.Td className={classes.mono}>{formatPercent(c.atm_vols[t])}</Table.Td>
                  <Table.Td className={classes.mono}>{formatPercent(c.zero_rates[t])}</Table.Td>
                  <Table.Td className={classes.mono}>{formatPercent(c.carry[t])}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {c.heston && (
            <Text size="sm" c="dimmed">
              Heston{" "}
              <span className={classes.mono}>
                {Object.entries(c.heston)
                  .map(([k, v]) => `${k} ${formatNumber(v, { digits: 3 })}`)
                  .join(" · ")}
              </span>
              {c.slv_mix !== null && c.slv_mix !== undefined && (
                <> · SLV mix {formatNumber(c.slv_mix, { digits: 2 })}</>
              )}
            </Text>
          )}
          <Anchor component={Link} to="/quant/market" onClick={onClose} size="sm">
            Open in Quant › Market explorer
          </Anchor>

          <Text fw={600} size="sm" mt="sm">
            Stored snapshots
          </Text>
          {snapshots.data?.items.map((s) => (
            <div key={s.snapshot_id} className={classes.snapshot}>
              <div>
                <div className={classes.mono}>{s.snapshot_id}</div>
                <Text size="xs" c="dimmed">
                  {s.underlying} · {formatDate(s.valuation)} · {s.source ?? "unknown source"}
                </Text>
              </div>
              {s.active ? (
                <Text size="xs" c="dimmed">
                  in use
                </Text>
              ) : (
                can(me, "use_market") && (
                  <Button
                    size="xs"
                    variant="default"
                    loading={use.isPending && use.variables === s.snapshot_id}
                    onClick={() =>
                      modals.openConfirmModal({
                        title: "Switch the desk's market?",
                        children: (
                          <Text size="sm">
                            Every new quote, valuation and risk run will price on {s.snapshot_id}. Jobs already
                            submitted keep their market. The switch is in the audit log.
                          </Text>
                        ),
                        labels: { confirm: "Switch", cancel: "Keep the current one" },
                        onConfirm: () => use.mutate(s.snapshot_id),
                      })
                    }
                  >
                    Use this market
                  </Button>
                )
              )}
            </div>
          ))}
          {use.error && <Failure error={use.error} />}
        </Stack>
      )}
    </Drawer>
  );
}
