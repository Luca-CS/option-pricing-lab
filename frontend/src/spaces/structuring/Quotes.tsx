// The quotes kept by the desk (Structuring › Quotes), a quote's own page, and the comparison of variants: existing
// quotes side by side, what differs in their sheets and in their prices.
import { Button, Checkbox, Group, MultiSelect, Text } from "@mantine/core";
import { useInfiniteQuery, useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, ok, type Schemas } from "../../api/client";
import { useSavedQuote } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { formatDate, formatNumber } from "../../lib/format";
import { describeSheet } from "../../lib/sheet";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est } from "../../ui/Value";
import { commonClasses as c, DataTable, QuoteLink } from "../common";
import { BookDialog } from "./BookDialog";
import { QuoteActions, QuoteView } from "./QuoteView";

type Saved = Schemas["SavedQuote"];

export function QuotesPage() {
  const pages = useInfiniteQuery({
    queryKey: ["structuring", "quotes", "all"],
    queryFn: ({ pageParam }) =>
      ok(
        api.GET("/api/v1/structuring/quotes", {
          params: { query: pageParam ? { limit: 100, cursor: pageParam } : { limit: 100 } },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const items = pages.data?.pages.flatMap((p) => p.items) ?? [];
  const [picked, setPicked] = useState<string[]>([]);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-4)));
  return (
    <SpacePage>
      <Panel
        title="Quotes"
        hint={`${items.length} kept, newest first`}
        flush
        actions={
          // a link only once two quotes are picked: a disabled link is still a link to assistive technology
          picked.length >= 2 ? (
            <Button
              size="xs"
              component={Link}
              to={`/structuring/compare?quotes=${picked.map(encodeURIComponent).join(",")}`}
            >
              Compare {picked.length}
            </Button>
          ) : (
            <Button size="xs" variant="default" disabled>
              Compare
            </Button>
          )
        }
      >
        {pages.isPending && <Loading />}
        {pages.error && <Failure error={pages.error} />}
        {pages.data && items.length === 0 && (
          <Empty title="No quote yet" action={<Link to="/structuring/builder">Open the builder</Link>}>
            A quote solves a sheet's coupon or price and is kept here with the market it priced on.
          </Empty>
        )}
        {items.length > 0 && (
          <DataTable
            caption="Kept quotes"
            head={["", "Quote", "Made", "Product", "Result", ""]}
            num={[4]}
            rows={items.map((q) => ({
              key: q.quote_id,
              cells: [
                <Checkbox
                  key="pick"
                  size="xs"
                  checked={picked.includes(q.quote_id)}
                  onChange={() => toggle(q.quote_id)}
                  aria-label={`Pick ${q.quote_id} to compare`}
                />,
                <QuoteLink key="id" id={q.quote_id} />,
                formatDate(q.created_at),
                describeSheet(q.sheet as Record<string, unknown>),
                q.headline ? <Est key="h" value={{ value: q.headline.value, se: q.headline.se }} /> : "—",
                <span key="u" className={c.faint}>
                  {q.headline ? `${q.headline.unit} · ${q.headline.kind}` : ""}
                </span>,
              ],
            }))}
          />
        )}
        {pages.hasNextPage && (
          <Group p="sm">
            <Button
              size="xs"
              variant="default"
              loading={pages.isFetchingNextPage}
              onClick={() => void pages.fetchNextPage()}
            >
              Older quotes
            </Button>
          </Group>
        )}
      </Panel>
    </SpacePage>
  );
}

export function QuotePage() {
  const { id } = useParams();
  const saved = useSavedQuote(id);
  const me = useMe().data;
  const [params] = useSearchParams();
  const mayBook = can(me, "create_trade");
  // "Book it" from a quote's link anywhere lands here with ?book=1: the dialog opens once the quote is read
  const [booking, setBooking] = useState(params.get("book") === "1");
  return (
    <SpacePage title={`Quote ${id ?? ""}`}>
      {saved.isPending && <Loading />}
      {saved.error && <Failure error={saved.error} title="This quote cannot be read" />}
      {saved.data && (
        <>
          <Text size="sm" c="dimmed">
            Made {formatDate(saved.data.created_at)} · {describeSheet(saved.data.sheet as Record<string, unknown>)}
          </Text>
          <QuoteView
            q={saved.data.result}
            quoteId={saved.data.quote_id}
            actions={
              <QuoteActions quoteId={saved.data.quote_id} onBook={mayBook ? () => setBooking(true) : undefined} />
            }
          />
          <BookDialog
            opened={booking && mayBook}
            onClose={() => setBooking(false)}
            quote={saved.data.result}
            quoteId={saved.data.quote_id}
          />
        </>
      )}
    </SpacePage>
  );
}

/** The fields whose values differ between sheets: "section.field" → one value per sheet. */
export function sheetDifferences(sheets: Record<string, unknown>[]): [string, unknown[]][] {
  const paths = new Set<string>();
  for (const s of sheets) {
    for (const [section, fields] of Object.entries(s)) {
      if (fields && typeof fields === "object" && !Array.isArray(fields)) {
        for (const f of Object.keys(fields)) paths.add(`${section}.${f}`);
      }
    }
  }
  const value = (s: Record<string, unknown>, path: string) => {
    const [section = "", field = ""] = path.split(".");
    return (s[section] as Record<string, unknown> | undefined)?.[field];
  };
  return [...paths]
    .sort()
    .map((p): [string, unknown[]] => [p, sheets.map((s) => value(s, p))])
    .filter(([, vs]) => new Set(vs.map((v) => JSON.stringify(v ?? null))).size > 1);
}

function shown(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return formatNumber(v, { digits: Math.abs(v) < 10 ? 4 : 0 });
  return Array.isArray(v) ? v.join(", ") : String(v).replaceAll("_", " ");
}

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const ids = (params.get("quotes") ?? "").split(",").filter(Boolean).slice(0, 4);
  const recent = useQuery({
    queryKey: ["structuring", "quotes"],
    queryFn: () => ok(api.GET("/api/v1/structuring/quotes", { params: { query: { limit: 50 } } })),
  });
  const quotes = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["structuring", "quote", id],
      queryFn: () => ok(api.GET("/api/v1/structuring/quotes/{quote_id}", { params: { path: { quote_id: id } } })),
    })),
  });
  const loaded = quotes.map((q) => q.data).filter((q): q is Saved => Boolean(q));
  const lines = [...new Set(loaded.flatMap((q) => q.result.desk?.lines.map((l) => l.label) ?? []))];
  const diffs = sheetDifferences(loaded.map((q) => q.sheet as Record<string, unknown>));
  return (
    <SpacePage>
      <Panel
        title="Variants side by side"
        hint="two to four kept quotes; what differs in their sheets, and what it does to the price"
      >
        <MultiSelect
          label="Quotes"
          data={(recent.data?.items ?? []).map((q) => ({
            value: q.quote_id,
            label: `${q.quote_id} · ${describeSheet(q.sheet as Record<string, unknown>)}`,
          }))}
          value={ids}
          onChange={(v) => setParams(v.length ? { quotes: v.slice(0, 4).join(",") } : {})}
          maxValues={4}
          searchable
          clearable
        />
      </Panel>
      {ids.length < 2 && (
        <Empty title="Pick at least two quotes">
          Variants are quotes of sheets that differ in a few fields: a barrier, a maturity, a coupon's memory. Pick them
          here or tick them in Structuring › Quotes.
        </Empty>
      )}
      {quotes.some((q) => q.error) && <Failure error={quotes.find((q) => q.error)?.error} />}
      {loaded.length >= 2 && (
        <>
          <Panel title="What they are" flush>
            <DataTable
              caption="The quotes compared"
              head={["", ...loaded.map((q) => q.quote_id)]}
              num={loaded.map((_, i) => i + 1)}
              rows={[
                {
                  key: "what",
                  cells: ["Product", ...loaded.map((q) => describeSheet(q.sheet as Record<string, unknown>))],
                },
                {
                  key: "head",
                  cells: [
                    "Solved",
                    ...loaded.map((q) => (
                      <span key={q.quote_id}>
                        <Est value={{ value: q.result.headline.value, se: q.result.headline.se }} />{" "}
                        <span className={c.faint}>{q.result.headline.unit}</span>
                      </span>
                    )),
                  ],
                },
                {
                  key: "base",
                  cells: [
                    "Base price, % nominal",
                    ...loaded.map((q) => <Est key={q.quote_id} value={q.result.result.price} />),
                  ],
                },
                {
                  key: "client",
                  cells: [
                    "Client price",
                    ...loaded.map((q) =>
                      q.result.desk ? <Est key={q.quote_id} value={q.result.desk.final_price} /> : "—",
                    ),
                  ],
                },
                {
                  key: "life",
                  cells: [
                    "Expected life, years",
                    ...loaded.map((q) => formatNumber(q.result.result.expected_life, { digits: 2 })),
                  ],
                },
                { key: "model", cells: ["Method", ...loaded.map((q) => q.result.policy.label)] },
              ]}
            />
          </Panel>
          <div className={c.grid}>
            <Panel title="Where their sheets differ" flush>
              {diffs.length === 0 ? (
                <Empty title="The same sheet">
                  These quotes priced the same sheet, perhaps on another market or with other options.
                </Empty>
              ) : (
                <DataTable
                  caption="Fields that differ"
                  head={["Field", ...loaded.map((q) => q.quote_id)]}
                  num={loaded.map((_, i) => i + 1)}
                  rows={diffs.map(([path, values]) => ({ key: path, cells: [path, ...values.map(shown)] }))}
                />
              )}
            </Panel>
            <Panel title="The desk's lines" hint="% of nominal" flush>
              <DataTable
                caption="Desk lines compared"
                head={["Line", ...loaded.map((q) => q.quote_id)]}
                num={loaded.map((_, i) => i + 1)}
                rows={lines.map((label) => ({
                  key: label,
                  cells: [
                    label,
                    ...loaded.map((q) => {
                      const l = q.result.desk?.lines.find((x) => x.label === label);
                      return l ? <Est key={q.quote_id} value={l.cost} digits={3} /> : "—";
                    }),
                  ],
                }))}
              />
            </Panel>
          </div>
        </>
      )}
    </SpacePage>
  );
}
