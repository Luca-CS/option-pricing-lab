// The audit log (Admin › Audit): who did what to what, the latest first: sign-ins, accounts, trades, settings, the
// scenario library, the market in use. A change keeps the hashes of the record before and after it.
import { Button, Group, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, ok } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { Panel } from "../../ui/Panel";
import { Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";
import classes from "./Admin.module.css";

export default function Audit() {
  const [pages, setPages] = useState<number[]>([]);
  const [filter, setFilter] = useState("");
  const before = pages.at(-1);
  const audit = useQuery({
    queryKey: ["admin", "audit", before],
    queryFn: () => ok(api.GET("/api/v1/admin/audit", { params: { query: { limit: 100, before } } })),
  });
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => ok(api.GET("/api/v1/admin/users")) });
  const nameOf = new Map((users.data?.users ?? []).map((u) => [u.user_id, u.name]));
  const f = filter.trim().toLowerCase();
  const items = (audit.data?.items ?? []).filter(
    (e) => !f || [e.action, e.target ?? "", nameOf.get(e.user_id ?? "") ?? ""].some((x) => x.toLowerCase().includes(f)),
  );
  return (
    <SpacePage>
      <Group gap="sm" align="flex-end">
        <TextInput
          label="Filter this page"
          description="by action, target or user"
          size="sm"
          w={260}
          value={filter}
          onChange={(e) => setFilter(e.currentTarget.value)}
        />
      </Group>
      <Panel title="The audit log" hint="the latest first, 100 a page" flush>
        {audit.isPending && <Loading />}
        {audit.error && <Failure error={audit.error} />}
        <DataTable
          caption="The audit log"
          head={["When", "Who", "Action", "Target", "Detail", "Before → after"]}
          rows={items.map((e) => ({
            key: String(e.audit_id),
            cells: [
              new Date(e.at).toLocaleString("en-GB"),
              <span key="u" className={c.mono}>
                {nameOf.get(e.user_id ?? "") ?? e.user_id ?? "—"}
              </span>,
              e.action.replaceAll("_", " "),
              <span key="t" className={c.mono}>
                {e.target ?? "—"}
              </span>,
              <span key="d" className={classes.detail}>
                {e.detail ? JSON.stringify(e.detail) : ""}
              </span>,
              <span key="h" className={c.mono}>
                {e.before_hash || e.after_hash
                  ? `${(e.before_hash ?? "∅").slice(0, 8)} → ${(e.after_hash ?? "∅").slice(0, 8)}`
                  : ""}
              </span>,
            ],
          }))}
        />
      </Panel>
      <Group gap="sm">
        <Button size="xs" variant="default" disabled={pages.length === 0} onClick={() => setPages(pages.slice(0, -1))}>
          Newer
        </Button>
        <Button
          size="xs"
          variant="default"
          disabled={!audit.data?.next}
          onClick={() => audit.data?.next && setPages([...pages, audit.data.next])}
        >
          Older
        </Button>
      </Group>
    </SpacePage>
  );
}
