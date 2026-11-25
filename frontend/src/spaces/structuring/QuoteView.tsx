// A quote as a structurer reads it: what was solved and how sure it is, why this method, where each basis point goes
// from the base to the client, when the product ends, its Greeks, and the script it compiles to. The same view for a
// fresh quote (the job's result) and a stored one.
import { Anchor, Button, Code, Group, Modal, Text, Textarea, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, type ReactNode, useState } from "react";
import { Link } from "react-router";
import type { Schemas } from "../../api/client";
import { savedQuote } from "../../api/queries";
import { WaterfallChart } from "../../charts";
import { formatDate, formatNumber, formatPercent, formatSeconds } from "../../lib/format";
import { describeSheet } from "../../lib/sheet";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { MarketBadge } from "../../ui/Markers";
import { Panel } from "../../ui/Panel";
import { Failure } from "../../ui/States";
import { Est } from "../../ui/Value";
import { commonClasses as c, DataTable } from "../common";
import classes from "./QuoteView.module.css";

export type QuoteResult = Schemas["QuoteResult"];

/** The market date a snapshot id carries: "SPX-20210630T1600-8c7d3283e7" → "2021-06-30". */
export function snapshotDate(id: string | null | undefined): string | null {
  const m = id ? /-(\d{4})(\d{2})(\d{2})T/.exec(id) : null;
  return m ? `${m[1]}-${m[2]}-${m[3]}` : null;
}

function Waterfall({ q }: { q: QuoteResult }) {
  const desk = q.desk;
  if (!desk) return null;
  const inPrice = desk.lines.filter((l) => l.in_price);
  if (q.headline.kind === "coupon" && desk.coupon_base !== null && desk.coupon_base !== undefined) {
    return (
      <WaterfallChart
        start={{ label: "base", value: desk.coupon_base * 1e4 }}
        steps={inPrice.map((l) => ({ label: l.label.replace(/^Digital: /, "digital: "), value: l.coupon_bp ?? 0 }))}
        end={{ label: "client", value: (desk.coupon_final ?? 0) * 1e4 }}
        digits={1}
        height={300}
        label="The coupon from the base model to the client, in basis points per annum"
      />
    );
  }
  return (
    <WaterfallChart
      start={{ label: "base", value: desk.base_price.value }}
      steps={inPrice.map((l) => ({ label: l.label, value: l.cost.value }))}
      end={{ label: "client", value: desk.final_price.value }}
      digits={2}
      height={300}
      label="The price from the base model to the client, in percent of nominal"
    />
  );
}

export function QuoteView({
  q,
  quoteId,
  asOf,
  origin,
  actions,
}: {
  q: QuoteResult;
  quoteId?: string | null | undefined;
  asOf?: string | null | undefined;
  /** the surface's origin, when the caller has the job's market */
  origin?: string | null | undefined;
  actions?: ReactNode;
}) {
  const desk = q.desk;
  const coupon = q.headline.kind === "coupon";
  const schedule = q.schedule ?? [];
  const lifeMax = Math.max(1e-9, ...schedule.map((s) => s.p_end));
  return (
    <div className={classes.view}>
      <section className={classes.headline} aria-label="The quote">
        <div className={classes.solved}>
          <span className={c.muted}>{q.headline.label}</span>
          <span className={classes.big}>
            <Est value={{ value: q.headline.value, se: q.headline.se }} />
            <span className={classes.unit}>{q.headline.unit}</span>
          </span>
          <span className={c.faint}>{describeSheet(q.sheet)}</span>
        </div>
        <div className={classes.how}>
          <span>
            {q.policy.label}{" "}
            <Tooltip label={q.policy.reason} multiline w={340} withArrow>
              <Anchor component="button" type="button" size="sm">
                why
              </Anchor>
            </Tooltip>
          </span>
          <span className={c.faint}>
            {formatNumber(q.result.n_paths)} paths · {formatSeconds(q.seconds ?? q.result.seconds)}
            {quoteId ? ` · quote ${quoteId}` : ""}
          </span>
          {q.snapshot_id && (
            <MarketBadge market={{ snapshot_id: q.snapshot_id, as_of: asOf ?? snapshotDate(q.snapshot_id), origin }} />
          )}
        </div>
        {actions && (
          <Group gap="xs" className={classes.actions}>
            {actions}
          </Group>
        )}
      </section>

      <KpiRow label="The quote in figures">
        <Kpi label="Price" unit="% of nominal" sub="the base model's, before the desk's lines">
          <Est value={q.result.price} />
        </Kpi>
        {desk && (
          <Kpi label="Client price" unit="% of nominal" sub={`desk side: ${desk.side}`}>
            <Est value={desk.final_price} />
          </Kpi>
        )}
        <Kpi
          label="Expected life"
          unit="years"
          sub={q.product.maturity ? `matures ${formatDate(q.product.maturity)}` : ""}
        >
          <span className={c.mono}>{formatNumber(q.result.expected_life, { digits: 2 })}</span>
        </Kpi>
        {q.greeks?.delta && (
          <Kpi label="Delta" unit="% of notional per +1 %" sub={q.greeks.vega ? "vega below" : ""}>
            <Est value={q.greeks.delta} signed />
          </Kpi>
        )}
        {q.greeks?.vega && (
          <Kpi label="Vega" unit="% of notional per vol point">
            <Est value={q.greeks.vega} signed />
          </Kpi>
        )}
      </KpiRow>

      {desk && (
        <div className={c.grid}>
          <Panel
            title={coupon ? "From the base coupon to the client's" : "From the base price to the client's"}
            hint={coupon ? "bp of coupon per annum" : "% of nominal"}
          >
            <Waterfall q={q} />
          </Panel>
          <Panel
            title="The desk's lines"
            hint="each on the base's random numbers; % of nominal"
            flush
            actions={
              // bridge 9: the cost table these lines were drawn from (Trading; read-only to whoever may not edit it)
              <Anchor component={Link} to="/trading/costs" size="xs" underline="always">
                The pricing policy
              </Anchor>
            }
          >
            <DataTable
              caption="The desk price, line by line"
              head={["Line", "Kind", "Cost ± SE", coupon ? "Coupon, bp" : "", ""]}
              num={[2, 3]}
              rows={desk.lines.map((l) => ({
                key: l.key,
                cells: [
                  l.label,
                  <span key="k" className={c.faint}>
                    {l.kind}
                  </span>,
                  <Est key="c" value={l.cost} digits={3} />,
                  coupon && l.coupon_bp !== null && l.coupon_bp !== undefined
                    ? formatNumber(l.coupon_bp, { digits: 1, sign: true })
                    : "",
                  l.in_price ? (
                    ""
                  ) : (
                    <span key="n" className={c.faint} title="An interaction is shown, not charged">
                      not charged
                    </span>
                  ),
                ],
              }))}
            />
          </Panel>
        </div>
      )}

      <div className={c.grid}>
        <Panel title="When it ends" hint="probability by date, expected flows in % of nominal" flush>
          <table className={classes.schedule}>
            <caption className="visually-hidden">The product's dates and the probability it ends at each</caption>
            <thead>
              <tr>
                <th scope="col">Observation</th>
                <th scope="col">Ends here</th>
                <th scope="col" className={classes.right}>
                  Coupon
                </th>
                <th scope="col" className={classes.right}>
                  Redemption
                </th>
              </tr>
            </thead>
            <tbody>
              {schedule.map((s) => (
                <tr key={s.observation}>
                  <td className={c.mono}>{formatDate(s.observation)}</td>
                  <td>
                    <span className={classes.bar}>
                      <span
                        className={classes.fill}
                        style={{ "--p": `${(100 * s.p_end) / lifeMax}%` } as CSSProperties}
                      />
                    </span>
                    <span className={c.mono}>{formatPercent(s.p_end, { digits: 1 })}</span>
                  </td>
                  <td className={`${classes.right} ${c.mono}`}>{formatNumber(s.flows?.coupon, { digits: 3 })}</td>
                  <td className={`${classes.right} ${c.mono}`}>{formatNumber(s.flows?.redemption, { digits: 2 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="The product" hint={q.product.label}>
          <dl className={classes.facts}>
            <dt>Underlyings</dt>
            <dd>{q.product.underlyings?.join(", ")}</dd>
            <dt>Strike</dt>
            <dd>{formatDate(q.product.strike_date)}</dd>
            <dt>Maturity</dt>
            <dd>{formatDate(q.product.maturity)}</dd>
            {q.control && (
              <>
                <dt>Control ({q.policy.control})</dt>
                <dd>
                  <Est value={q.control.price} /> · gap {formatNumber(q.control_gap_se, { digits: 1 })} SE
                </dd>
              </>
            )}
          </dl>
          {(q.warnings?.length ?? 0) > 0 && (
            <ul className={classes.warnings}>
              {q.warnings?.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {q.product.script && (
            <details>
              <summary>The payoff script it compiles to</summary>
              <Code block className={classes.script}>
                {q.product.script}
              </Code>
            </details>
          )}
        </Panel>
      </div>
    </div>
  );
}

/** The links a quote opens: book it, its model risk, its backtest, its report, the builder, a comparison. */
/** The quote's term sheet as JSON: to the clipboard where the page may write it (a secure context: this machine, or
 * HTTPS), else shown selected, for Ctrl C (the desk over plain HTTP on the LAN). */
function CopySheet({ quoteId }: { quoteId: string }) {
  const client = useQueryClient();
  const [text, setText] = useState<string | null>(null);
  const copy = useMutation({
    mutationFn: async () => {
      const q = await client.fetchQuery(savedQuote(quoteId));
      const json = JSON.stringify(q.sheet, null, 1);
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(json);
          notifications.show({ message: "Term sheet copied as JSON" });
          return;
        } catch {
          // refused: shown instead
        }
      }
      setText(json);
    },
  });
  return (
    <>
      <Button size="xs" variant="subtle" loading={copy.isPending} onClick={() => copy.mutate()}>
        Copy term sheet
      </Button>
      <Modal opened={text !== null} onClose={() => setText(null)} title="Term sheet (JSON)" size="lg">
        <Text size="sm" c="dimmed" mb="xs">
          Selected: Ctrl C copies it.
        </Text>
        <Textarea
          value={text ?? ""}
          readOnly
          autosize
          minRows={8}
          maxRows={20}
          classNames={{ input: c.mono }}
          aria-label="The term sheet as JSON"
          onFocus={(ev) => ev.currentTarget.select()}
          data-autofocus
        />
      </Modal>
      {copy.error && <Failure error={copy.error} title="Not copied" />}
    </>
  );
}

export function QuoteActions({ quoteId, onBook }: { quoteId: string; onBook?: (() => void) | undefined }) {
  const e = encodeURIComponent(quoteId);
  return (
    <>
      {onBook && (
        <Button color="amber" size="xs" onClick={onBook}>
          Book it
        </Button>
      )}
      <Button component={Link} to={`/structuring/builder?quote=${e}`} size="xs" variant="default">
        Open in the builder
      </Button>
      <Button component={Link} to={`/structuring/compare?quotes=${e}`} size="xs" variant="default">
        Compare
      </Button>
      <Button component={Link} to={`/quant/calibration?quote=${e}`} size="xs" variant="subtle">
        Model risk
      </Button>
      <Button component={Link} to={`/quant/replay?quote=${e}`} size="xs" variant="subtle">
        Backtest
      </Button>
      <CopySheet quoteId={quoteId} />
      {/* a report is a page the desk writes: the demo has no desk behind it */}
      {import.meta.env.MODE !== "demo" && (
        <Button
          component="a"
          href={`/api/v1/structuring/quotes/${e}/report`}
          target="_blank"
          rel="noopener"
          size="xs"
          variant="subtle"
        >
          Report
        </Button>
      )}
    </>
  );
}
