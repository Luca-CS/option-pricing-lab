// The command palette (Ctrl+K): a screen, a trade, a quote or a name, found by typing and opened in its place.
import { Spotlight, type SpotlightActionGroupData } from "@mantine/spotlight";
import { IconFileText, IconLayoutGrid, IconSearch, IconTrendingUp } from "@tabler/icons-react";
import { useMemo } from "react";
import { useNavigate } from "react-router";
import { useQuotes, useTrades, useUniverse } from "../api/queries";
import { useMe } from "../auth/session";
import { formatDate } from "../lib/format";
import { spacesFor } from "./spaces";

export function Palette() {
  const navigate = useNavigate();
  const me = useMe().data;
  const trades = useTrades().data?.items ?? [];
  const quotes = useQuotes().data?.items ?? [];
  const names = useUniverse().data?.instruments ?? [];

  const actions = useMemo<SpotlightActionGroupData[]>(() => {
    const { own, others } = spacesFor(me);
    return [
      {
        group: "Screens",
        actions: [...own, ...others].flatMap((space) =>
          space.screens.map((screen) => ({
            id: `screen:${screen.path}`,
            label: screen.label === "Home" ? `${space.label} home` : `${space.label} › ${screen.label}`,
            keywords: [space.label, screen.label],
            leftSection: <IconLayoutGrid size={16} />,
            onClick: () => navigate(screen.path),
          })),
        ),
      },
      {
        group: "Trades",
        actions: trades.map((t) => ({
          id: `trade:${t.trade_id}`,
          label: t.trade_id,
          description: `${t.summary?.type ?? "trade"} on ${t.summary?.underlyings.join(", ") ?? "?"} · ${t.status} · ${t.counterparty}`,
          keywords: [t.counterparty, t.status, ...(t.summary?.underlyings ?? []), ...(t.tags ?? [])],
          leftSection: <IconTrendingUp size={16} />,
          onClick: () => navigate(`/trading/trades/${encodeURIComponent(t.trade_id)}`),
        })),
      },
      {
        group: "Quotes",
        actions: quotes.map((q) => ({
          id: `quote:${q.quote_id}`,
          label: `Quote ${q.quote_id}`,
          description: `${q.headline?.label ?? ""} · ${formatDate(q.created_at)}`,
          leftSection: <IconFileText size={16} />,
          onClick: () => navigate(`/structuring/quotes/${encodeURIComponent(q.quote_id)}`),
        })),
      },
      {
        group: "Names",
        actions: names.map((n) => ({
          id: `name:${n.id}`,
          label: n.id,
          description: `${n.name} · ${n.asset_class} · ${n.currency}`,
          keywords: [n.name],
          leftSection: <IconSearch size={16} />,
          onClick: () => navigate(`/quant/market/${encodeURIComponent(n.id)}`),
        })),
      },
    ].filter((g) => g.actions.length > 0);
  }, [me, trades, quotes, names, navigate]);

  return (
    <Spotlight
      actions={actions}
      shortcut={["mod + K"]}
      nothingFound="Nothing found"
      highlightQuery
      limit={12}
      scrollable
      maxHeight={420}
      searchProps={{ leftSection: <IconSearch size={18} />, placeholder: "A trade, a quote, a name, a screen…" }}
    />
  );
}
