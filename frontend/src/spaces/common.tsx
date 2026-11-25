// What the screens of every space share: the placeholder of a screen still to come, small tables, and the link of a
// trade or a quote with the places it opens in (the bridges of HANDOFF_REFACTOR_2_UX.md §3).
import { Anchor } from "@mantine/core";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { SpacePage } from "../app/Shell";
import { ENTITY_PAGES, spaceOf } from "../app/spaces";
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
