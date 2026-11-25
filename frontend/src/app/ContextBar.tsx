// The bar above every screen: the valuation date and the market in use (a click opens the market drawer), the engine
// that computes, the search, the jobs in flight and the person signed in, with their preferences.
import { Avatar, Menu, SegmentedControl, Stack, Text, UnstyledButton, useMantineColorScheme } from "@mantine/core";
import { spotlight } from "@mantine/spotlight";
import { IconLogout, IconSearch } from "@tabler/icons-react";
import { type ReactNode, useState } from "react";
import { useNavigate } from "react-router";
import { useEngine, useMarketContext } from "../api/queries";
import { useMe, useSignOut } from "../auth/session";
import { formatDate } from "../lib/format";
import { MarketBadge } from "../ui/Markers";
import { type Density, density, type Palette, palette, setDensity, setPalette } from "./preferences";
import classes from "./Shell.module.css";

/** A market more than this many days older than today is called old (the demo's always is, and says so). */
const STALE_DAYS = 5;

function isStale(valuation: string | undefined): boolean {
  if (!valuation) return false;
  const days = (Date.now() - Date.parse(`${valuation}T00:00:00Z`)) / 86_400_000;
  return days > STALE_DAYS;
}

const ENGINE: Record<string, string> = { gpu: "GPU", cpu: "CPU", numpy: "NumPy" };

function UserMenu() {
  const me = useMe().data;
  const signOut = useSignOut();
  const navigate = useNavigate();
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const [pal, setPal] = useState<Palette>(palette());
  const [dens, setDens] = useState<Density>(density());
  const initials = (me?.name ?? "?").slice(0, 2).toUpperCase();
  return (
    <Menu position="bottom-end" width={260} withinPortal closeOnItemClick={false}>
      <Menu.Target>
        <UnstyledButton className={classes.search} style={{ minWidth: 0 }}>
          <Avatar size={24} radius="xl" color="gray" aria-hidden="true">
            {initials}
          </Avatar>
          <span className={classes.srNarrow}>
            {me?.name} <span style={{ color: "var(--ol-muted)" }}>{me?.roles.join(", ")}</span>
          </span>
          <span className="visually-hidden">, preferences and sign out</span>
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Display</Menu.Label>
        <Stack gap={8} px="xs" pb="xs">
          <SegmentedControl
            size="xs"
            fullWidth
            value={colorScheme === "light" ? "light" : "dark"}
            onChange={(v) => setColorScheme(v as "light" | "dark")}
            data={[
              { label: "Graphite", value: "dark" },
              { label: "Paper", value: "light" },
            ]}
            aria-label="Theme"
          />
          <SegmentedControl
            size="xs"
            fullWidth
            value={pal}
            onChange={(v) => {
              setPal(v as Palette);
              setPalette(v as Palette);
            }}
            data={[
              { label: "Green / red", value: "standard" },
              { label: "Blue / orange", value: "colorblind" },
            ]}
            aria-label="Colours of signed values"
          />
          <SegmentedControl
            size="xs"
            fullWidth
            value={dens}
            onChange={(v) => {
              setDens(v as Density);
              setDensity(v as Density);
            }}
            data={[
              { label: "Compact", value: "compact" },
              { label: "Comfortable", value: "comfortable" },
            ]}
            aria-label="Density"
          />
          <Text size="xs" c="dimmed">
            Kept in this browser. Press ? for the keyboard.
          </Text>
        </Stack>
        <Menu.Divider />
        <Menu.Item
          leftSection={<IconLogout size={15} />}
          onClick={() => signOut.mutate(undefined, { onSettled: () => navigate("/sign-in", { replace: true }) })}
        >
          Sign out
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

export function ContextBar({ onMarket, jobs }: { onMarket: () => void; jobs: ReactNode }) {
  const context = useMarketContext().data;
  const engine = useEngine().data;
  return (
    <header className={classes.bar}>
      <span>
        <span className={classes.label}>Valuation</span>
        <span className={classes.mono}>{context ? formatDate(context.valuation) : "…"}</span>
      </span>
      {context && (
        <MarketBadge
          market={{
            snapshot_id: context.snapshot_id,
            as_of: context.valuation,
            underlying: context.underlying,
            origin: context.origin.surface,
          }}
          // the demo's market is dated on purpose, and its banner says so
          stale={import.meta.env.MODE !== "demo" && isStale(context.valuation)}
          onOpen={onMarket}
        />
      )}
      {engine && (
        <span
          className={classes.hideNarrow}
          title={`asked: ${engine.engine}; ${engine.cpu_threads ?? "?"} CPU threads`}
        >
          <span className={classes.label}>Engine</span>
          {ENGINE[engine.resolved] ?? engine.resolved}
          {engine.devices?.[0] ? ` · ${engine.devices[0].name.replace("NVIDIA GeForce ", "")}` : ""}
        </span>
      )}
      <span className={classes.spacer} />
      <button type="button" className={classes.search} onClick={() => spotlight.open()}>
        <IconSearch size={14} aria-hidden="true" />
        <span className={classes.srNarrow}>Search trades, quotes, screens</span>
        <span className={`${classes.kbd} ${classes.hideNarrow}`}>Ctrl K</span>
      </button>
      {jobs}
      <UserMenu />
    </header>
  );
}
