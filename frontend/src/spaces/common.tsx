// What the screens of every space share: the placeholder of a screen still to come, small tables, and the link of a
// trade or a quote with the places it opens in (the bridges of docs/ux.md §3).
import { Alert, Anchor, Badge, Checkbox, NumberInput, SegmentedControl, Stack, Text, Tooltip } from "@mantine/core";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { SpacePage } from "../app/Shell";
import { ENTITY_PAGES, spaceOf } from "../app/spaces";
import type { DeskSide } from "../lib/side";
import { EntityLink } from "../ui/Markers";
import { Empty } from "../ui/States";
import classes from "./common.module.css";

/** A screen whose step has not landed yet: it says which, and where to go meanwhile. */
export function Soon() {
  const location = useLocation();
  const space = spaceOf(location.pathname);
  const screen =
    space?.screens.find((s) => s.path === location.pathname) ??
    ENTITY_PAGES.find((p) => new RegExp(`^${p.path.replace(/:[a-z]+/g, "[^/]+")}$`).test(location.pathname));
  return (
    <SpacePage>
      <Empty title={`${screen?.label ?? "This screen"} comes with step ${screen?.step ?? "a later step"}`}>
        The desk's API already serves it; its screen is being built.{" "}
        {space && (
          <Anchor component={Link} to={`/${space.id}`}>
            Back to {space.label}
          </Anchor>
        )}
      </Empty>
    </SpacePage>
  );
}

export function TradeLink({ id }: { id: string }) {
  const e = encodeURIComponent(id);
  return (
    <EntityLink
      label={id}
      to={`/trading/trades/${e}`}
      places={[
        { label: "Its Greeks", to: `/trading/risk?trade=${e}` },
        { label: "Its P&L explained", to: `/trading/pnl?trade=${e}` },
        { label: "Its lifecycle", to: `/trading/lifecycle/${e}` },
        { label: "Its replay", to: `/quant/replay?trade=${e}` },
      ]}
    />
  );
}

export function QuoteLink({ id }: { id: string }) {
  const e = encodeURIComponent(id);
  return (
    <EntityLink
      label={id}
      to={`/structuring/quotes/${e}`}
      places={[
        // the quote's page opens its booking dialog (to a role that may book)
        { label: "Book it", to: `/structuring/quotes/${e}?book=1` },
        { label: "Its model risk", to: `/quant/calibration?quote=${e}` },
        { label: "Its backtest", to: `/quant/replay?quote=${e}` },
      ]}
    />
  );
}

/** A plain data table: header cells, then rows; numbers right-aligned by the `num` columns. */
export function DataTable({
  head,
  rows,
  num = [],
  caption,
}: {
  head: string[];
  rows: { key: string; cells: ReactNode[] }[];
  num?: number[];
  caption?: string;
}) {
  return (
    <table className={classes.table}>
      {caption && <caption className="visually-hidden">{caption}</caption>}
      <thead>
        <tr>
          {head.map((h, i) => (
            <th key={h} scope="col" className={num.includes(i) ? classes.num : undefined}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            {r.cells.map((c, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: a row's cells are its columns, in order
              <td key={i} className={num.includes(i) ? classes.num : undefined}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export { classes as commonClasses };

/** The desk's side and a positive size: what a booking or an edit asks, instead of a signed quantity. */
export function SideSize({
  side,
  onSide,
  size,
  onSize,
  unit = "notionals",
}: {
  side: DeskSide;
  onSide: (side: DeskSide) => void;
  size: number | string;
  onSize: (size: number | string) => void;
  unit?: string;
}) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500} component="span" id="desk-side-label">
        The desk
      </Text>
      <SegmentedControl
        aria-labelledby="desk-side-label"
        value={side}
        onChange={(v) => onSide(v as DeskSide)}
        data={[
          { value: "sold", label: "Sold" },
          { value: "bought", label: "Bought" },
        ]}
      />
      <NumberInput
        label="Size"
        description={`in ${unit}; the side gives the sign (sold: the book holds the issuer's side)`}
        value={size}
        onChange={onSize}
        min={0}
        step={0.5}
        decimalScale={4}
        allowNegative={false}
      />
    </Stack>
  );
}

/** "desk short" or "desk long", with the server's warning when the sign contradicts the side the sheet was priced for. */
export function DeskSideBadge({ trade }: { trade: { desk_side: string; side_check?: { message: string } | null } }) {
  const badge = (
    <Badge
      component="span"
      variant="light"
      color={trade.side_check ? "amber" : trade.desk_side === "short" ? "accent" : "gray"}
    >
      desk {trade.desk_side}
      {trade.side_check ? " ⚠" : ""}
    </Badge>
  );
  return trade.side_check ? <Tooltip label={trade.side_check.message}>{badge}</Tooltip> : badge;
}

/** Whether a run takes the book's what-ifs (simulated trades): the server never puts them in the EOD. */
export function WhatIfSwitch({ checked, onChange }: { checked: boolean; onChange: (on: boolean) => void }) {
  return (
    <Checkbox
      size="xs"
      label="Include the what-ifs"
      checked={checked}
      onChange={(e) => onChange(e.currentTarget.checked)}
    />
  );
}

/** The server's side check as an alert on a trade's page. */
export function SideCheckAlert({ check }: { check?: { message: string } | null }) {
  if (!check) return null;
  return (
    <Alert color="amber" title="The side contradicts the price">
      This trade was {check.message}. Buying back a sold note is legitimate; otherwise edit its side.
    </Alert>
  );
}
