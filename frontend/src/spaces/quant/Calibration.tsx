// Calibration and model risk (Quant › Calibration): the exotics of the calibration priced under each model of the
// chain (Black-Scholes, Heston, local vol, SLV) with their errors, the spread between models as the measure of
// model risk, and the fit by maturity against the quotes' spread.
import { Anchor, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api, ok } from "../../api/client";
import { useMarketContext, useSavedQuote } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { type Estimate, formatNumber } from "../../lib/format";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { Est, Num } from "../../ui/Value";
import { commonClasses as c, DataTable } from "../common";
import { HestonTable, SviTable } from "./Params";

/** The largest price less the smallest, across the models of one product. */
export function modelSpread(prices: Estimate[]): number | null {
  if (prices.length < 2) return null;
  const v = prices.map((p) => p.value);
  return Math.max(...v) - Math.min(...v);
}

/** The model risk of one sheet, from its saved quote (a quote's "Model risk" bridge): the base model the policy chose
 * and why, the model provisions the desk priced on the base's random numbers, and the control and its gap. */
function QuoteModelRisk({ id }: { id: string }) {
  const saved = useSavedQuote(id);
  const q = saved.data?.result;
  const lines = (q?.desk?.lines ?? []).filter((l) => l.kind === "model");
  return (
    <Panel title={`The model risk of quote ${id}`} hint="% of nominal, on the base's random numbers">
      {saved.isPending && <Loading />}
      {saved.error && <Failure error={saved.error} title="This quote cannot be read" />}
      {q && (
        <>
          <Text size="sm">
            {q.policy.label}. <span className={c.muted}>{q.policy.reason}</span>
          </Text>
          {lines.length > 0 ? (
            <DataTable
              caption="The model provisions of this quote"
              head={["Provision", "Cost ± SE", "Charged"]}
              num={[1]}
              rows={lines.map((l) => ({
                key: l.key,
                cells: [l.label, <Est key="c" value={l.cost} digits={3} />, l.in_price ? "yes" : "shown only"],
              }))}
            />
          ) : (
            <Text size="sm" c="dimmed" mt="xs">
              No model provision on this quote: its class prices on the base model alone.
            </Text>
          )}
          {q.control && (
            <Text size="sm" mt="xs">
              Control ({q.policy.control}): <Est value={q.control.price} digits={3} />, a gap of{" "}
              {formatNumber(q.control_gap_se, { digits: 1 })} SE to the base.
            </Text>
          )}
          <Anchor component={Link} to={`/structuring/quotes/${encodeURIComponent(id)}`} size="sm" underline="always">
            Back to the quote
          </Anchor>
        </>
      )}
    </Panel>
  );
}

export default function Calibration() {
  const [params] = useSearchParams();
  const quoteId = params.get("quote");
  const context = useMarketContext();
  const risk = useQuery({ queryKey: ["quant", "model-risk"], queryFn: () => ok(api.GET("/api/v1/quant/model-risk")) });
  const r = risk.data;
  const exotics = Object.entries(r?.exotics ?? {});
  const models = [...new Set(exotics.flatMap(([, byModel]) => Object.keys(byModel)))];
  const errors = (r?.errors ?? []) as Record<string, unknown>[];
  const bp = (x: unknown) => formatNumber(typeof x === "number" ? x : null, { digits: 1 });
  return (
    <SpacePage>
      <Text size="sm" c="dimmed">
        The market in use: <span className={c.mono}>{context.data?.snapshot_id ?? "…"}</span>.{" "}
        <Anchor component={Link} to="/quant/market" size="sm" underline="always">
          Its surface
        </Anchor>
      </Text>
      {quoteId && <QuoteModelRisk id={quoteId} />}
      {risk.isPending && <Loading />}
      {risk.error && <Failure error={risk.error} />}
      {r && exotics.length === 0 && (
        <Empty title="No model-risk table for this market">
          {r.note ? `${r.note[0]?.toUpperCase()}${r.note.slice(1)}. ` : ""}The calibration pipeline writes one (`optlab
          calib run`), with the exotics priced under each model.
        </Empty>
      )}
      {exotics.length > 0 && (
        <Panel
          title="The calibration's exotics under each model"
          hint="price per 100 ± SE; the spread across models is the model risk"
          flush
        >
          <DataTable
            caption="The exotics priced under each model, and the spread between them"
            head={["Product", ...models, "Spread"]}
            num={[...models.map((_, i) => i + 1), models.length + 1]}
            rows={exotics.map(([product, byModel]) => {
              const prices = models.flatMap((m): Estimate[] => {
                const p = byModel[m]?.price;
                return p ? [p] : [];
              });
              return {
                key: product,
                cells: [
                  product,
                  ...models.map((m) => <Est key={m} value={byModel[m]?.price} digits={3} />),
                  <Num key="s" value={modelSpread(prices)} digits={3} />,
                ],
              };
            })}
          />
        </Panel>
      )}
      {r?.params && (
        <div className={c.grid}>
          <Panel title="The SVI surface" hint="per maturity, the raw SVI of the total variance" flush>
            <SviTable
              params={r.params.svi as Record<string, unknown> | undefined}
              caption="The surface's SVI parameters"
            />
          </Panel>
          <Panel
            title="Heston, and the leverage"
            hint={
              r.params.slv
                ? `SLV mix ${formatNumber(r.params.slv.mix as number | undefined, { digits: 2 })}, ${formatNumber(
                    r.params.slv.particles as number | undefined,
                  )} particles`
                : "the stochastic part of the SLV"
            }
            flush
          >
            <HestonTable
              params={r.params.heston as Record<string, unknown> | undefined}
              caption="Heston's parameters"
            />
            {r.params.arbitrage && (
              <p className={`${c.faint} ${c.pad}`}>
                Surface: {r.params.arbitrage.butterfly_free ? "free of butterfly arbitrage" : "butterfly arbitrage"},{" "}
                {r.params.arbitrage.calendar_free ? "free of calendar arbitrage" : "calendar arbitrage"} (min g{" "}
                {formatNumber(r.params.arbitrage.min_g as number | undefined, { digits: 3 })}, largest Lee slope{" "}
                {formatNumber(r.params.arbitrage.max_lee_slope as number | undefined, { digits: 2 })}).
              </p>
            )}
          </Panel>
        </div>
      )}
      {errors.length > 0 && (
        <Panel
          title="The fit by maturity"
          hint="error against the quotes, bp of implied vol; the median spread for scale"
          flush
        >
          <DataTable
            caption="Each model's error by maturity"
            head={[
              "Expiry",
              "T",
              "Quotes",
              "Median spread",
              "SVI",
              "SSVI",
              "Heston",
              "Local vol",
              "SLV",
              "SLV max",
              "MC noise",
            ]}
            num={[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
            rows={errors.map((e) => ({
              key: String(e.expiry ?? e.T),
              cells: [
                <span key="x" className={c.mono}>
                  {String(e.expiry ?? "")}
                </span>,
                formatNumber(typeof e.T === "number" ? e.T : null, { digits: 3 }),
                String(e.n_quotes ?? "—"),
                bp(e.median_spread_bp),
                bp(e.svi_rmse_bp),
                bp(e.ssvi_rmse_bp),
                bp(e.heston_rmse_bp),
                bp(e.lv_mae_bp),
                bp(e.slv_mae_bp),
                bp(e.slv_max_bp),
                bp(e.mc_stderr_bp),
              ],
            }))}
          />
          <p className={`${c.faint} ${c.pad}`}>
            SVI, SSVI and Heston: root mean square error. Local vol and SLV: mean absolute error of a Monte Carlo smile,
            whose own noise is the last column.
          </p>
        </Panel>
      )}
    </SpacePage>
  );
}
