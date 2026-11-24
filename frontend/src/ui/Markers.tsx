// Where a number comes from, and where an entity leads. Every market object says whether it was observed, derived,
// reconstructed or made up (the lake's `origin`); every entity (trade, quote, run, snapshot) links to its page and
// offers the other places it can be opened (the bridges of HANDOFF_REFACTOR_2_UX.md §3).
import { ActionIcon, Menu } from "@mantine/core";
import { IconDots } from "@tabler/icons-react";
import { Link } from "react-router";
import { formatDate } from "../lib/format";
import classes from "./Markers.module.css";

export type OriginKind = "observed" | "derived" | "reconstructed" | "synthetic" | "typed";

const ORIGIN_LABEL: Record<OriginKind, string> = {
  observed: "observed",
  derived: "derived",
  reconstructed: "reconstructed",
  synthetic: "synthetic",
  typed: "typed by hand",
};

/** The kind of an origin string of the API ("lake:reconstructed", "simulated", "flat", "observed"...). */
export function originKind(origin: string | null | undefined): OriginKind {
  const o = (origin ?? "").toLowerCase();
  if (o.includes("reconstruct") || o.includes("extrapolat")) return "reconstructed";
  if (o.includes("observ") || o.includes("quote")) return "observed";
  if (o.includes("deriv") || o.includes("fit") || o.includes("calibrat")) return "derived";
  if (o.includes("typed") || o.includes("flat") || o.includes("manual") || o.includes("hand")) return "typed";
  return "synthetic";
}

export function OriginBadge({ origin, label }: { origin: string | null | undefined; label?: string }) {
  const kind = originKind(origin);
  return (
    <span className={classes.badge} title={origin ?? undefined}>
      <span className={`${classes.dot} ${classes[kind]}`} aria-hidden="true" />
      {label ? `${label}: ` : ""}
      {ORIGIN_LABEL[kind]}
    </span>
  );
}

export type MarketRef = {
  snapshot_id?: string | null;
  as_of?: string | null;
  underlying?: string | null;
  /** the surface's origin, or the snapshot's source */
  origin?: string | null;
};

/** The market a number was priced on: underlying, date, origin of its surface. A button when it opens the drawer. */
export function MarketBadge({ market, stale, onOpen }: { market: MarketRef; stale?: boolean; onOpen?: () => void }) {
  const underlying = market.underlying ?? market.snapshot_id?.split("-")[0] ?? "market";
  const kind = originKind(market.origin);
  const content = (
    <>
      <span className={`${classes.dot} ${classes[kind]}`} aria-hidden="true" />
      <span className={classes.mono}>
        {underlying} · {formatDate(market.as_of)}
      </span>
      <span className={classes.muted}>{ORIGIN_LABEL[kind]}</span>
      {stale && <span className={classes.stale}>old data</span>}
    </>
  );
  if (onOpen) {
    return (
      <button type="button" className={classes.market} onClick={onOpen} title={market.snapshot_id ?? undefined}>
        {content}
      </button>
    );
  }
  return (
    <span className={classes.market} title={market.snapshot_id ?? undefined}>
      {content}
    </span>
  );
}

export type Place = { label: string; to: string };

/** An entity's id as a link to its page, with an "Open in…" menu when it has other places. */
export function EntityLink({ label, to, places = [] }: { label: string; to: string; places?: Place[] }) {
  return (
    <span className={classes.entity}>
      <Link className={classes.entityLink} to={to}>
        {label}
      </Link>
      {places.length > 0 && (
        <Menu position="bottom-start" withinPortal>
          <Menu.Target>
            <ActionIcon variant="subtle" color="gray" size="xs" aria-label={`Open ${label} in…`}>
              <IconDots size={14} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Open in…</Menu.Label>
            {places.map((p) => (
              <Menu.Item key={p.to} component={Link} to={p.to}>
                {p.label}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>
      )}
    </span>
  );
}
