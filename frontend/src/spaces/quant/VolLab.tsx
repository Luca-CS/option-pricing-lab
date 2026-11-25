// The vol lab (Quant › Vol lab): a day of real option quotes from the lake, the smile with its bid and ask, and SSVI,
// SVI, Heston and SLV fitted to it: each model's error against the spread by maturity (RMSE in bp of vol, the share of
// quotes it puts inside the spread). Every fit is stored as a run that can be fitted again to check it reproduces.
import { Button, Checkbox, Group, Select, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, ok, type Schemas } from "../../api/client";
import { SpacePage } from "../../app/Shell";
import { can, useMe } from "../../auth/session";
import { CurveChart } from "../../charts";
import type { Curve } from "../../charts/charts";
import { useScreenJob } from "../../jobs/useScreenJob";
import { formatDate, formatNumber, formatPercent, formatSeconds } from "../../lib/format";
import { JobProgress } from "../../ui/Flow";
import { Panel } from "../../ui/Panel";
import { Empty, Failure, Loading } from "../../ui/States";
import { commonClasses as c, DataTable } from "../common";

const MODEL_LABEL: Record<string, string> = { svi: "SVI", ssvi: "SSVI", heston: "Heston", slv: "SLV" };
type FitModel = NonNullable<Schemas["FitIn"]["models"]>[number];
// the models the fit route takes (the type checks each against the document)
const FITTABLE = ["ssvi", "svi", "heston"] satisfies FitModel[];
const label = (m: string) => MODEL_LABEL[m] ?? m;

export default function VolLab() {
  const me = useMe().data;
  const days = useQuery({
    queryKey: ["quant", "vollab", "days"],
    queryFn: () => ok(api.GET("/api/v1/quant/vollab/days")),
  });
  const sets = days.data?.sets ?? [];
  const [setKey, setSetKey] = useState<string | null>(null);
  const set = sets.find((s) => `${s.underlying}|${s.source}` === setKey) ?? sets[0];
  const [day, setDay] = useState<string | null>(null);
  const shownDay = day ?? set?.days?.at(-1) ?? null;
  const [models, setModels] = useState<string[]>(["ssvi", "svi", "heston"]);
  const [T, setT] = useState<string | null>(null);
  const quotes = useQuery({
    queryKey: ["quant", "vollab", "quotes", set?.underlying, shownDay, set?.source],
    queryFn: () =>
      ok(
        api.GET("/api/v1/quant/vollab/quotes", {
          params: {
            query: { underlying: set?.underlying ?? "SPX", day: shownDay ?? "", source: set?.source ?? undefined },
          },
        }),
      ),
    enabled: Boolean(set && shownDay && can(me, "vollab_quotes")),
  });
  const runs = useQuery({
    queryKey: ["quant", "vollab", "runs"],
    queryFn: () => ok(api.GET("/api/v1/quant/vollab/runs")),
  });
  const fit = useScreenJob("fit", "Vol lab fit", "job");
  const again = useScreenJob("reproduce", "Vol lab reproduction", "again");
  const result = fit.envelope?.result;
  const slices = quotes.data?.slices ?? [];
  const slice = slices.find((s) => String(s.T) === T) ?? slices[Math.min(2, slices.length - 1)];
  const curves: Curve[] = [];
  if (slice) {
    const pts = (ys: (number | null)[]) =>
      slice.moneyness.flatMap((m, i) =>
        ys[i] === null || ys[i] === undefined ? [] : [[m, ys[i] as number] as [number, number]],
      );
    curves.push({ name: "bid", points: pts(slice.iv_bid ?? []), kind: "points" });
    curves.push({ name: "ask", points: pts(slice.iv_ask ?? []), kind: "points" });
    for (const [m, f] of Object.entries(result?.fits ?? {})) {
      const cv = (f.curves ?? []).find((x) => Math.abs(x.T - slice.T) < 1e-9);
      if (cv) {
        curves.push({
          name: label(m),
          points: cv.moneyness.flatMap((k, i) =>
            cv.iv[i] === null || cv.iv[i] === undefined ? [] : [[k, cv.iv[i] as number] as [number, number]],
          ),
        });
      }
    }
  }
  if (days.isPending)
    return (
      <SpacePage>
        <Loading />
      </SpacePage>
    );
  return (
    <SpacePage>
      {days.error && <Failure error={days.error} />}
      {days.data && !days.data.available && (
        <Empty title="No option quotes in this lake">
          The vol lab fits the models to real chains: `optlab lake update` loads them (CBOE samples, Yahoo during US
          hours). The demo's lake is synthetic and holds none.
        </Empty>
      )}
      {days.data?.available && set && (
        <>
          <Group gap="sm" align="flex-end" wrap="wrap">
            <Select
              label="Name and source"
              size="sm"
              w={240}
              data={sets.map((s) => ({
                value: `${s.underlying}|${s.source}`,
                label: `${s.underlying} · ${s.source} (${s.n} days)`,
              }))}
              value={`${set.underlying}|${set.source}`}
              onChange={(k) => {
                setSetKey(k);
                setDay(null);
              }}
              allowDeselect={false}
            />
            <Select
              label="Day"
              size="sm"
              w={170}
              searchable
              data={[...(set.days ?? [])].reverse()}
              value={shownDay}
              onChange={setDay}
              allowDeselect={false}
            />
            <Checkbox.Group label="Models" value={models} onChange={setModels}>
              <Group gap="sm" mt={6}>
                {FITTABLE.filter((m) => (days.data?.models ?? []).includes(m)).map((m) => (
                  <Checkbox key={m} value={m} label={label(m)} size="xs" />
                ))}
              </Group>
            </Checkbox.Group>
            {can(me, "vollab_fit") &&
              (fit.running && fit.tracked ? (
                <JobProgress job={fit.tracked} label="Fit" onCancel={() => void fit.cancel()} />
              ) : (
                <Button
                  size="sm"
                  disabled={!shownDay || models.length === 0}
                  loading={fit.start.isPending}
                  onClick={() =>
                    fit.start.mutate(() =>
                      ok(
                        api.POST("/api/v1/quant/vollab/fits", {
                          body: {
                            underlying: set.underlying,
                            day: shownDay ?? "",
                            source: set.source ?? null,
                            models: models as FitModel[],
                          },
                        }),
                      ),
                    )
                  }
                >
                  Fit the models
                </Button>
              ))}
          </Group>
          {fit.failed && <Failure error={new Error(fit.failed)} title="The fit failed" />}
          {(set.quality ?? []).length > 0 && (
            <Text size="sm" c="dimmed">
              Quality: {(set.quality ?? []).map(String).join("; ")}
            </Text>
          )}
          <div className={c.grid}>
            <Panel
              title={slice ? `The smile at ${slice.expiry ?? formatNumber(slice.T, { digits: 2 })}` : "The smile"}
              hint="bid and ask implied vols, and the fitted models"
              actions={
                slices.length > 1 && (
                  <Select
                    size="xs"
                    data={slices.map((s) => ({
                      value: String(s.T),
                      label: s.expiry ?? formatNumber(s.T, { digits: 2 }),
                    }))}
                    value={slice ? String(slice.T) : null}
                    onChange={setT}
                    allowDeselect={false}
                    aria-label="Maturity"
                  />
                )
              }
            >
              {!can(me, "vollab_quotes") && (
                <Text size="sm" c="dimmed">
                  Raw quotes take the quant's role.
                </Text>
              )}
              {quotes.isPending && can(me, "vollab_quotes") && <Loading lines={2} />}
              {quotes.error && <Failure error={quotes.error} />}
              {curves.length > 0 && (
                <CurveChart
                  curves={curves}
                  xName="K / F(T)"
                  yName="implied vol"
                  height={300}
                  label="The day's smile, bid and ask, and the fitted models"
                />
              )}
            </Panel>
            <Panel
              title="The fit"
              hint={
                result
                  ? `${result.underlying} on ${formatDate(result.day)}, ${result.source}`
                  : "fit the models to see it"
              }
              flush
            >
              {result ? (
                <DataTable
                  caption="Each model's error against the quotes"
                  head={["Model", "RMSE, bp", "Inside the spread", "Quotes", "Took", "Run"]}
                  num={[1, 2, 3, 4]}
                  rows={Object.entries(result.fits ?? {}).map(([m, f]) => ({
                    key: m,
                    cells: [
                      label(m),
                      formatNumber(f.overall?.rmse_bp as number | undefined, { digits: 1 }),
                      formatPercent(f.overall?.within_spread as number | undefined, { digits: 0 }),
                      String(f.overall?.n ?? "—"),
                      formatSeconds(f.seconds),
                      <span key="r" className={c.mono}>
                        {f.calibration_id ?? "—"}
                      </span>,
                    ],
                  }))}
                />
              ) : (
                <p className={`${c.faint} ${c.pad}`}>No fit on this screen yet.</p>
              )}
            </Panel>
          </div>
          {result && (
            <Panel title="By maturity" hint="RMSE in bp of implied vol; the median half spread for scale" flush>
              <DataTable
                caption="Each model's error by maturity"
                head={[
                  "T",
                  "Half spread, bp",
                  ...Object.keys(result.fits ?? {}).flatMap((m) => [`${label(m)} RMSE`, `${label(m)} inside`]),
                ]}
                num={Array.from({ length: 1 + 2 * Object.keys(result.fits ?? {}).length }, (_, i) => i + 1)}
                rows={(result.maturities ?? []).map((t) => ({
                  key: String(t),
                  cells: [
                    formatNumber(t, { digits: 3 }),
                    formatNumber(
                      Object.values(result.fits ?? {})[0]?.by_maturity?.find((r) => Math.abs(r.T - t) < 1e-9)
                        ?.median_half_spread_bp,
                      { digits: 1 },
                    ),
                    ...Object.values(result.fits ?? {}).flatMap((f) => {
                      const r = f.by_maturity?.find((x) => Math.abs(x.T - t) < 1e-9);
                      return [formatNumber(r?.rmse_bp, { digits: 1 }), formatPercent(r?.within_spread, { digits: 0 })];
                    }),
                  ],
                }))}
              />
            </Panel>
          )}
        </>
      )}
      <Panel title="Stored fits" hint="each model of each fit, the newest first" flush>
        {runs.isPending && <Loading lines={2} />}
        {(runs.data?.runs ?? []).length === 0 && !runs.isPending && (
          <p className={`${c.faint} ${c.pad}`}>No stored fit.</p>
        )}
        {again.envelope?.result && (
          <p className={c.pad}>
            {again.envelope.result.calibration_id}:{" "}
            {again.envelope.result.identical
              ? "reproduced exactly"
              : `largest parameter gap ${formatNumber(again.envelope.result.largest_parameter_gap, { digits: 6 })}`}
            , {again.envelope.result.same_quotes ? "same quotes" : "the quotes changed"}, RMSE{" "}
            {formatNumber(again.envelope.result.stored_rmse_bp, { digits: 2 })} then{" "}
            {formatNumber(again.envelope.result.new_rmse_bp, { digits: 2 })} bp.
          </p>
        )}
        {again.failed && <Failure error={new Error(again.failed)} title="Not reproduced" />}
        <DataTable
          caption="Stored fits"
          head={["Run", "Name", "Day", "Model", "RMSE, bp", "Inside", "Took", "Code", ""]}
          num={[4, 5, 6]}
          rows={(runs.data?.runs ?? []).slice(0, 50).map((r) => ({
            key: r.calibration_id,
            cells: [
              <span key="id" className={c.mono}>
                {r.calibration_id.slice(0, 18)}
              </span>,
              r.underlying,
              formatDate(r.day),
              label(r.model),
              formatNumber(r.rmse_bp, { digits: 1 }),
              formatPercent(r.within_spread, { digits: 0 }),
              formatSeconds(r.seconds),
              <span key="c" className={c.faint}>
                {r.code_version ?? ""}
              </span>,
              can(me, "vollab_reproduce") ? (
                <Button
                  key="a"
                  size="compact-xs"
                  variant="subtle"
                  disabled={again.running}
                  onClick={() =>
                    again.start.mutate(() =>
                      ok(
                        api.POST("/api/v1/quant/vollab/runs/{run_id}/reproduce", {
                          params: { path: { run_id: r.calibration_id } },
                        }),
                      ),
                    )
                  }
                  aria-label={`Fit ${r.calibration_id} again`}
                >
                  Fit again
                </Button>
              ) : (
                ""
              ),
            ],
          }))}
        />
      </Panel>
    </SpacePage>
  );
}
