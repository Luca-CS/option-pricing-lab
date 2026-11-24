// The frame of every screen (HANDOFF_REFACTOR_2_UX.md §5): a rail of the spaces, a context bar that always says on
// what market, with which engine and for whom the numbers are, the jobs in flight, and the page. Ctrl+K searches,
// `g` + a letter goes to a space, `?` lists the keys.
import { Notifications } from "@mantine/notifications";
import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import { useMe } from "../auth/session";
import { JobsTray } from "../jobs/JobsTray";
import { JobsProvider } from "../jobs/jobs";
import { ContextBar } from "./ContextBar";
import { MarketDrawer } from "./MarketDrawer";
import { Palette } from "./Palette";
import classes from "./Shell.module.css";
import { ShortcutsHelp, useShortcuts } from "./shortcuts";
import { homeOf, type Space, spaceOf, spacesFor } from "./spaces";

function RailLink({ space, other }: { space: Space; other?: boolean }) {
  const Icon = space.icon;
  return (
    <NavLink
      to={`/${space.id}`}
      className={({ isActive }) =>
        [classes.space, other ? classes.other : "", isActive ? classes.active : ""].join(" ")
      }
      title={`${space.label} (g ${space.key})`}
    >
      <Icon size={19} stroke={1.7} aria-hidden="true" />
      <span>{space.label}</span>
    </NavLink>
  );
}

export function Shell() {
  const me = useMe().data;
  const { own, others } = spacesFor(me);
  const [market, setMarket] = useState(false);
  const [help, setHelp] = useState(false);
  useShortcuts({ onHelp: () => setHelp(true) });

  return (
    <JobsProvider>
      <Notifications position="bottom-right" limit={4} />
      <div className={classes.shell}>
        <nav className={classes.rail} aria-label="Spaces">
          <Link to={homeOf(me)} className={classes.mark} aria-label="Home">
            o
          </Link>
          {own.map((s) => (
            <RailLink key={s.id} space={s} />
          ))}
          {others.length > 0 && own.length > 0 && <span className={classes.divider} aria-hidden="true" />}
          {others.map((s) => (
            <RailLink key={s.id} space={s} other />
          ))}
        </nav>
        <div className={classes.main}>
          <ContextBar onMarket={() => setMarket(true)} jobs={<JobsTray />} />
          <Outlet />
        </div>
      </div>
      <MarketDrawer opened={market} onClose={() => setMarket(false)} />
      <Palette />
      <ShortcutsHelp opened={help} onClose={() => setHelp(false)} />
    </JobsProvider>
  );
}

/** A space's page: its title, the tabs of its screens, then the screen. */
export function SpacePage({ title, children }: { title?: string; children: React.ReactNode }) {
  const location = useLocation();
  const space = spaceOf(location.pathname);
  return (
    <main className={classes.content}>
      <div className={classes.head}>
        <h1 className={classes.title}>{title ?? space?.label}</h1>
        {space && (
          <nav className={classes.tabs} aria-label={`${space.label} screens`}>
            {space.screens.map((s) => (
              <NavLink key={s.path} to={s.path} end className={classes.tab}>
                {s.label}
              </NavLink>
            ))}
          </nav>
        )}
      </div>
      {children}
    </main>
  );
}

export { classes as shellClasses };
