// The desk's five spaces (HANDOFF_REFACTOR_2_UX.md §3), their screens and who they belong to. A space is shown to
// the roles it serves first; every signed-in user may read every space but Admin, and the server refuses what a role
// may not do anyway.
import {
  type Icon,
  IconChartLine,
  IconListDetails,
  IconMathFunction,
  IconSettings,
  IconShieldHalf,
} from "@tabler/icons-react";
import type { Me } from "../api/client";

export type SpaceId = "structuring" | "trading" | "risk" | "quant" | "admin";

export type Screen = { path: string; label: string; step?: string };

export type Space = {
  id: SpaceId;
  label: string;
  /** the role whose home this space is */
  role: string;
  icon: Icon;
  /** the second key of the `g` shortcut */
  key: string;
  screens: Screen[];
};

export const SPACES: Space[] = [
  {
    id: "structuring",
    label: "Structuring",
    role: "structurer",
    icon: IconListDetails,
    key: "s",
    screens: [
      { path: "/structuring", label: "Home" },
      { path: "/structuring/builder", label: "Builder" },
      { path: "/structuring/compare", label: "Compare" },
      { path: "/structuring/quotes", label: "Quotes" },
    ],
  },
  {
    id: "trading",
    label: "Trading",
    role: "trader",
    icon: IconChartLine,
    key: "t",
    screens: [
      { path: "/trading", label: "Home" },
      { path: "/trading/book", label: "Book" },
      { path: "/trading/risk", label: "Book risk" },
      { path: "/trading/pnl", label: "P&L explained" },
      { path: "/trading/lifecycle", label: "Lifecycle" },
      { path: "/trading/costs", label: "Pricing policy" },
    ],
  },
  {
    id: "risk",
    label: "Risk",
    role: "risk",
    icon: IconShieldHalf,
    key: "r",
    screens: [
      { path: "/risk", label: "Home" },
      { path: "/risk/var", label: "VaR" },
      { path: "/risk/stress", label: "Stress" },
      { path: "/risk/backtest", label: "Backtest" },
      { path: "/risk/runs", label: "EOD runs" },
    ],
  },
  {
    id: "quant",
    label: "Quant",
    role: "quant",
    icon: IconMathFunction,
    key: "q",
    screens: [
      { path: "/quant", label: "Home" },
      { path: "/quant/market", label: "Market explorer", step: "F7" },
      { path: "/quant/calibration", label: "Calibration", step: "F7" },
      { path: "/quant/vollab", label: "Vol lab", step: "F7" },
      { path: "/quant/replay", label: "Replay lab", step: "F7" },
      { path: "/quant/script", label: "Script lab", step: "F7" },
      { path: "/quant/engine", label: "Engine", step: "F7" },
    ],
  },
  {
    id: "admin",
    label: "Admin",
    role: "admin",
    icon: IconSettings,
    key: "a",
    screens: [
      { path: "/admin", label: "Home" },
      { path: "/admin/users", label: "Users", step: "F7" },
      { path: "/admin/jobs", label: "Jobs", step: "F7" },
      { path: "/admin/schedules", label: "Schedules", step: "F7" },
      { path: "/admin/audit", label: "Audit", step: "F7" },
      { path: "/admin/system", label: "System", step: "F7" },
    ],
  },
];

/** Pages of one entity, reached by links and the palette; each lands with the step that builds its space. */
export const ENTITY_PAGES: Screen[] = [
  { path: "/structuring/quotes/:id", label: "A quote" },
  { path: "/trading/trades/:id", label: "A trade" },
  { path: "/trading/lifecycle/:id", label: "A trade's lifecycle" },
  { path: "/risk/var/:id", label: "A VaR run" },
  { path: "/quant/market/:name", label: "A name's market", step: "F7" },
  { path: "/quant/replay/:id", label: "A replay", step: "F7" },
];

export function spaceOf(pathname: string): Space | undefined {
  const first = pathname.split("/")[1];
  return SPACES.find((s) => s.id === first);
}

/** The spaces this user sees, those of its roles first; Admin to admins only. */
export function spacesFor(me: Pick<Me, "roles"> | null | undefined): { own: Space[]; others: Space[] } {
  const roles = me?.roles ?? [];
  const admin = roles.includes("admin");
  const visible = SPACES.filter((s) => s.id !== "admin" || admin);
  if (admin) return { own: visible, others: [] };
  const own = visible.filter((s) => roles.includes(s.role));
  return { own, others: visible.filter((s) => !own.includes(s)) };
}

/** Where this user lands: the home of its first role's space (an admin's first role decides too), else Trading. */
export function homeOf(me: Pick<Me, "roles"> | null | undefined): string {
  for (const role of me?.roles ?? []) {
    const space = SPACES.find((s) => s.role === role);
    if (space) return `/${space.id}`;
  }
  return "/trading";
}
