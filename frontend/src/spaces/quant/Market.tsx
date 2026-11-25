// The market explorer (Quant › Market explorer, and a name's page `/quant/market/:name`): the surface of the market in
// use (smiles by maturity, the at-the-money term structure, forward and discount), the lake behind it (coverage,
// quality, the volatility indices) and the other names a worst-of may hold. A risk figure lands here on its node:
// `?strike=80%&tenor=1y` marks that point on its smile.
import { Anchor, Badge, Select, Text } from "@mantine/core";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, ok } from "../../api/client";
import { useLakeCoverage, useLakeQuality, useMarketContext, useUniverse } from "../../api/queries";
import { SpacePage } from "../../app/Shell";
import { CurveChart, SeriesChart } from "../../charts";
import type { Curve } from "../../charts/charts";
import { formatDate, formatNumber, formatPercent } from "../../lib/format";
import { Kpi, KpiRow } from "../../ui/Kpi";
import { OriginBadge } from "../../ui/Markers";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

const TENORS: [string, number][] = [
  ["1m", 1 / 12],
  ["3m", 0.25],
  ["6m", 0.5],
  ["1y", 1],
  ["2y", 2],
];

/** A tenor as the risk grid writes it ("3m", "1y", "18m") in years; null when it is not one. */
export function tenorYears(tenor: string | null | undefined): number | null {
  const m = /^(\d+(?:\.\d+)?)\s*([dwmy])$/i.exec((tenor ?? "").trim());
  if (!m) return null;
  const n = Number(m[1]);
  const unit = (m[2] ?? "").toLowerCase();
  return unit === "y" ? n : unit === "m" ? n / 12 : unit === "w" ? (7 * n) / 365 : n / 365;
}

/** A strike as the risk grid writes it ("80%", "100 %") as a moneyness; null when it is not one. */
export function strikeMoneyness(strike: string | null | undefined): number | null {
  const m = /^(\d+(?:\.\d+)?)\s*%$/.exec((strike ?? "").trim());
  return m ? Number(m[1]) / 100 : null;
}

/** Linear interpolation of y at x on points sorted by x; null outside them. */
export function interpolate(xs: number[], ys: number[], x: number): number | null {
  for (let i = 1; i < xs.length; i++) {
    const x0 = xs[i - 1] ?? 0;
    const x1 = xs[i] ?? 0;
    if (x >= x0 && x <= x1) {
      const y0 = ys[i - 1] ?? 0;
      const y1 = ys[i] ?? 0;
      return x1 === x0 ? y0 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
    }
  }
  return null;
}

function VolIndexChart({ name }: { name: string }) {
  const index = useQuery({
    queryKey: ["market", "vol-index", name],
    queryFn: () => ok(api.GET("/api/v1/market/lake/vol-indices/{name}", { params: { path: { name } } })),
  });
  if (index.isPending) return <Loading lines={2} />;
  if (index.error) return <Failure error={index.error} />;
  const v = index.data;
  return (
    <>
      <SeriesChart
        x={v.dates}
        series={[{ name, values: v.close, kind: "line" }]}
        unit="points"
        height={220}
        label={`The ${name} index, close by close`}
      />
      <OriginBadge origin={v.origin} label={v.source ?? "source"} />
    </>
  );
}

export default function Market() {
  const { name: named } = useParams();
  const [params] = useSearchParams();
  const context = useMarketContext();
  const ctx = context.data;
  const sid = ctx?.snapshot_id ?? "";
  const name = named ?? ctx?.underlying ?? "";
  const ours = !named || named === ctx?.underlying;
  const tenor = params.get("tenor");
  const strike = params.get("strike");
  const T = tenorYears(tenor);
  const K = strikeMoneyness(strike);
  const maturities = [...TENORS];
  if (T !== null && !maturities.some(([, t]) => Math.abs(t - T) < 1e-9)) maturities.push([tenor ?? "", T]);
  maturities.sort((a, b) => a[1] - b[1]);
  const smiles = useQueries({
    queries: maturities.map(([, t]) => ({
      queryKey: ["market", "smile", sid, t],
      queryFn: () =>
        ok(
          api.GET("/api/v1/market/snapshots/{snapshot_id}/smile", {
            params: { path: { snapshot_id: sid }, query: { T: t } },
          }),
        ),
      enabled: Boolean(sid) && ours,
    })),
  });
  const term = useQuery({
    queryKey: ["market", "term", sid],
    queryFn: () =>
      ok(api.GET("/api/v1/market/snapshots/{snapshot_id}/term", { params: { path: { snapshot_id: sid } } })),
    enabled: Boolean(sid) && ours,
  });
  const others = useQuery({
    queryKey: ["market", "other-underlyings"],
    queryFn: () => ok(api.GET("/api/v1/market/other-underlyings")),
  });
  const universe = useUniverse();
  const coverage = useLakeCoverage();
  const quality = useLakeQuality();
  const indices = [
    ...new Set((universe.data?.instruments ?? []).map((i) => i.vol_index).filter((x): x is string => Boolean(x))),
  ];
  const [index, setIndex] = useState<string | null>(null);
  const shownIndex = index ?? indices[0] ?? null;

  const curves: Curve[] = smiles.flatMap((q, i) => {
    const d = q.data;
    const label = maturities[i]?.[0] ?? "";
    return d ? [{ name: label, points: d.moneyness.map((m, j) => [m, d.iv[j] ?? 0] as [number, number]) }] : [];
  });
  const at = T !== null ? smiles[maturities.findIndex(([, t]) => Math.abs(t - T) < 1e-9)]?.data : undefined;
  const node = at && K !== null ? interpolate(at.moneyness, at.iv, K) : null;
  if (node !== null && K !== null) curves.push({ name: `${strike} · ${tenor}`, points: [[K, node]], kind: "mark" });
  const t = term.data;

  return (
    <SpacePage title={named ? `${named}'s market` : undefined}>
      {context.isPending && <Loading />}
      {context.error && <Failure error={context.error} />}
      {ctx && (
        <KpiRow label="The market in use">
          <Kpi label="Snapshot" sub={ctx.snapshot_id ?? "flat inputs"}>
            <span className={c.mono}>{ctx.underlying}</span>
          </Kpi>
          <Kpi label="Valuation" sub={`calibrated up to ${formatNumber(ctx.t_max, { digits: 1 })} years`}>
            <span className={c.mono}>{formatDate(ctx.valuation)}</span>
          </Kpi>
          <Kpi label="1y ATM vol" sub="at the model's spot of 100">
            <span className={c.mono}>{formatPercent(ctx.atm_vols["1"] ?? null)}</span>
          </Kpi>
          <Kpi label="1y zero rate" sub={`carry ${formatPercent(ctx.carry["1"] ?? null)}`}>
            <span className={c.mono}>{formatPercent(ctx.zero_rates["1"] ?? null)}</span>
          </Kpi>
        </KpiRow>
      )}
      {ctx && (
        <Text size="sm" c="dimmed">
          {Object.entries(ctx.origin ?? {})
            .filter(([k, v]) => typeof v === "string" && !["source", "layer"].includes(k))
            .map(([k, v]) => (
              <OriginBadge key={k} label={k} origin={String(v)} />
            ))}
        </Text>
      )}
      {named && !ours && (
        <Empty
          title={`No snapshot of ${named} in use`}
          action={
            <Anchor component={Link} to="/quant/market" size="sm">
              The market in use
            </Anchor>
          }
        >
          The desk prices {named} on its own market: the one in use for that name, else the lake's at the same date,
          else the inputs typed below. Build one from the lake in the Market drawer.
        </Empty>
      )}
      {ours && (
        <div className={c.grid}>
          <Panel
            title="Smiles"
            hint={node !== null ? `${strike} at ${tenor}: ${formatPercent(node)}` : "implied vol against K / F(T)"}
          >
            {smiles.some((q) => q.isPending) && <Loading lines={2} />}
            {curves.length > 0 && (
              <CurveChart
                curves={curves}
                xName="K / F(T)"
                yName="implied vol"
                height={300}
                label={`The smiles of ${name} by maturity${node !== null ? `, with the node ${strike} at ${tenor}` : ""}`}
              />
            )}
          </Panel>
          <Panel title="At the money" hint="implied vol along the maturities, years">
            {t && (
              <CurveChart
                curves={[{ name: "ATM vol", points: t.T.map((x, i) => [x, t.atm_vol[i] ?? 0] as [number, number]) }]}
                xName="years"
                yName="implied vol"
                xPercent={false}
                height={300}
                label="The at-the-money implied vol by maturity"
              />
            )}
          </Panel>
          <Panel title="Forward and discount" hint="the forward over the spot, and the discount factor">
            {t && (
              <CurveChart
                curves={[
                  { name: "forward / spot", points: t.T.map((x, i) => [x, t.forward[i] ?? 0] as [number, number]) },
                  { name: "discount", points: t.T.map((x, i) => [x, t.discount[i] ?? 0] as [number, number]) },
                ]}
                xName="years"
                yName=""
                xPercent={false}
                yPercent={false}
                digits={3}
                height={260}
                label="The forward over the spot and the discount factor by maturity"
              />
            )}
          </Panel>
          <Panel
            title="Volatility indices"
            hint="closes from the lake"
            actions={
              indices.length > 1 && (
                <Select
                  size="xs"
                  data={indices}
                  value={shownIndex}
                  onChange={setIndex}
                  allowDeselect={false}
                  aria-label="Volatility index"
                />
              )
            }
          >
            {shownIndex ? (
              <VolIndexChart name={shownIndex} />
            ) : (
              <Text size="sm" c="dimmed">
                No volatility index in the universe.
              </Text>
            )}
          </Panel>
        </div>
      )}
      <div className={c.grid}>
        <Panel
          title="Other names"
          hint="what a worst-of reads beside the calibrated name: typed inputs, correlations"
          flush
        >
          {others.data && Object.keys(others.data.names ?? {}).length === 0 ? (
            <p className={`${c.faint} ${c.pad}`}>
              None typed: a worst-of's other names take the lake's market at the same date, else the defaults (vol{" "}
              {formatPercent(others.data.defaults?.vol ?? null, { digits: 0 })}, correlation{" "}
              {formatNumber(others.data.defaults?.corr ?? null, { digits: 2 })}).
            </p>
          ) : (
            <DataTable
              caption="The other names' typed inputs"
              head={["Name", "Inputs"]}
              rows={Object.entries(others.data?.names ?? {}).map(([n, v]) => ({
                key: n,
                cells: [
                  <Anchor key="n" component={Link} to={`/quant/market/${encodeURIComponent(n)}`} className={c.mono}>
                    {n}
                  </Anchor>,
                  <span key="v" className={c.mono}>
                    {JSON.stringify(v)}
                  </span>,
                ],
              }))}
            />
          )}
          {Object.keys(others.data?.corr ?? {}).length > 0 && (
            <DataTable
              caption="Correlations between names"
              head={["Pair", "Correlation"]}
              num={[1]}
              rows={Object.entries(others.data?.corr ?? {}).map(([pair, v]) => ({
                key: pair,
                cells: [pair.replaceAll("|", " / "), formatNumber(v, { digits: 2 })],
              }))}
            />
          )}
        </Panel>
        <Panel title="The lake" hint="what it holds, and where it comes from" flush>
          {coverage.isPending && <Loading lines={2} />}
          <DataTable
            caption="The lake's datasets"
            head={["Dataset", "Origin", "Source", "Rows", "From", "To"]}
            num={[3]}
            rows={(coverage.data?.coverage ?? []).map((r) => ({
              key: `${r.dataset}-${r.source}-${r.origin}`,
              cells: [
                <span key="d" className={c.mono}>
                  {r.dataset}
                </span>,
                <OriginBadge key="o" origin={r.origin} />,
                r.source ?? "—",
                formatNumber(r.n),
                formatDate(r.first),
                formatDate(r.last),
              ],
            }))}
          />
          <p className={`${c.faint} ${c.pad}`}>
            {(quality.data?.issues ?? []).length === 0 ? (
              "No open quality issue."
            ) : (
              <>
                <Badge size="xs" color="red" variant="light">
                  {(quality.data?.issues ?? []).length}
                </Badge>{" "}
                open quality issues.
              </>
            )}
          </p>
        </Panel>
      </div>
    </SpacePage>
  );
}
