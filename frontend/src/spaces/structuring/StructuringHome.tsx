// The structurer's home: start from a preset, pick up a recent quote.

import { Link } from "react-router";
import { useMarketContext, usePresets, useQuotes } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { formatDate } from "../../lib/format";
import { MarketBadge } from "../../ui/Markers";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est } from "../../ui/Value";
import { commonClasses as c, DataTable, QuoteLink } from "../common";

export function StructuringHome() {
  const presets = usePresets();
  const quotes = useQuotes();
  const context = useMarketContext().data;
  const items = quotes.data?.items ?? [];
  return (
    <SpacePage>
      <Panel
        title="Start from a preset"
        hint="the builder opens with its sheet; every field stays editable"
        actions={
          context && (
            <MarketBadge
              market={{ as_of: context.valuation, underlying: context.underlying, origin: context.origin.surface }}
            />
          )
        }
      >
        {presets.isPending && <Loading lines={2} />}
        {presets.error && <Failure error={presets.error} />}
        <div className={c.cards}>
          {presets.data?.presets.map((p) => (
            <Link key={p.name} to={`/structuring/builder?preset=${encodeURIComponent(p.name)}`} className={c.card}>
              <strong>{p.label}</strong>
              <span className={c.faint}>{p.type === "autocall" ? "autocallable" : "to maturity"}</span>
            </Link>
          ))}
        </div>
      </Panel>
      <Panel title="Recent quotes" hint={`${items.length} kept`} flush>
        {quotes.isPending && <Loading />}
        {quotes.data && items.length === 0 && (
          <Empty title="No quote yet">A quote solves a sheet's coupon or price; it is kept here with its market.</Empty>
        )}
        {items.length > 0 && (
          <DataTable
            caption="Recent quotes"
            head={["Quote", "Made", "Product", "Underlying", "Result", ""]}
            num={[4]}
            rows={items.map((q) => {
              return {
                key: q.quote_id,
                cells: [
                  <QuoteLink key="id" id={q.quote_id} />,
                  formatDate(q.created_at),
                  q.sheet.type,
                  q.sheet.general.underlying ?? q.sheet.general.underlyings?.join(", ") ?? "—",
                  q.headline ? <Est key="h" value={{ value: q.headline.value, se: q.headline.se }} /> : "—",
                  <span key="u" className={c.faint}>
                    {q.headline ? `${q.headline.unit} · ${q.headline.kind}` : ""}
                  </span>,
                ],
              };
            })}
          />
        )}
      </Panel>
    </SpacePage>
  );
}
