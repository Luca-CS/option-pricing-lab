// The vol lab (Quant › Vol lab): a day of real option quotes from the lake, the smile with its bid and ask, and SSVI,
// SVI, Heston and SLV fitted to it: each model's error against the spread by maturity (RMSE in bp of vol, the share of
// quotes it puts inside the spread), and its fitted parameters. The fit's settings (which maturities, how quotes are
// weighted, a moneyness window, the quality filter, Heston's strikes) are the quant's; only those changed are sent.
// Every fit is stored as a run that can be fitted again to check it reproduces.
import { Accordion, Button, Checkbox, Group, NumberInput, SegmentedControl, Select, Text } from "@mantine/core";
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
import { FittedParams } from "./Params";

const MODEL_LABEL: Record<string, string> = { svi: "SVI", ssvi: "SSVI", heston: "Heston", slv: "SLV" };
type FitModel = NonNullable<Schemas["FitIn"]["models"]>[number];
// the models the fit route takes (the type checks each against the document)
const FITTABLE = ["ssvi", "svi", "heston"] satisfies FitModel[];
const label = (m: string) => MODEL_LABEL[m] ?? m;
const TARGETS = [1 / 12, 0.25, 0.5, 1, 2, 3, 5];

/** A target maturity as a desk says it: 1m, 3m, 6m, 1y, 2y. */
export function tenorLabel(t: number): string {
  return t < 1 ? `${Math.round(t * 12)}m` : `${Number(t.toFixed(2))}y`;
}

type FitSettings = Pick<
  Schemas["FitIn"],
  "maturities" | "weights" | "moneyness" | "max_rel_spread" | "n_strikes_heston"
>;

/** The settings a fit sends: only those that differ from the lab's defaults, so a default fit keeps its run id. */
export function fitSettings(s: {
  targets: number[];
  chosen: number[] | null;
  weights: "spread" | "equal";
  lo: number | string;
  hi: number | string;
  maxSpread: number | string;
  hestonStrikes: number | string;
}): FitSettings {
  const out: FitSettings = {};
  if (s.chosen && s.chosen.length !== s.targets.length) out.maturities = [...s.chosen].sort((a, b) => a - b);
  if (s.weights !== "spread") out.weights = s.weights;
  if (typeof s.lo === "number" && typeof s.hi === "number") out.moneyness = [s.lo / 100, s.hi / 100];
  if (typeof s.maxSpread === "number") out.max_rel_spread = s.maxSpread / 100;
  if (typeof s.hestonStrikes === "number") out.n_strikes_heston = s.hestonStrikes;
  return out;
}

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
  const defaults = (days.data?.defaults ?? {}) as Record<string, unknown>;
  const targets = Array.isArray(defaults.targets) ? (defaults.targets as number[]) : TARGETS;
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [weights, setWeights] = useState<"spread" | "equal">("spread");
  const [lo, setLo] = useState<number | string>("");
  const [hi, setHi] = useState<number | string>("");
  const [maxSpread, setMaxSpread] = useState<number | string>("");
  const [hestonStrikes, setHestonStrikes] = useState<number | string>("");
  const picked = chosen ?? targets.map(String);
  const windowBad = typeof lo === "number" && typeof hi === "number" && lo >= hi;
  const settings = fitSettings({
    targets,
    chosen: chosen ? chosen.map(Number) : null,
    weights,
    lo,
    hi,
    maxSpread,
    hestonStrikes,
  });
  const changed = Object.keys(settings).length;
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
                  disabled={!shownDay || models.length === 0 || picked.length === 0 || windowBad}
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
                            ...settings,
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
          <Accordion variant="contained" chevronPosition="left">
            <Accordion.Item value="settings">
              <Accordion.Control>
                Fit settings{" "}
                <Text span size="xs" c="dimmed">
                  {changed === 0 ? "the lab's defaults" : `${changed} changed`}
                </Text>
              </Accordion.Control>
              <Accordion.Panel>
                <Group gap="lg" align="flex-end" wrap="wrap">
                  <Checkbox.Group label="Maturities" value={picked} onChange={setChosen}>
                    <Group gap="sm" mt={6}>
                      {targets.map((t) => (
                        <Checkbox key={t} value={String(t)} label={tenorLabel(t)} size="xs" />
                      ))}
                    </Group>
                  </Checkbox.Group>
                  <div>
                    <Text size="sm" fw={500} mb={6}>
                      Weights
                    </Text>
                    <SegmentedControl
                      size="xs"
                      value={weights}
                      onChange={(v) => setWeights(v as "spread" | "equal")}
                      data={[
                        { value: "spread", label: "By the spread" },
                        { value: "equal", label: "Equal" },
                      ]}
                      aria-label="Weights"
                    />
                  </div>
                  <NumberInput
                    label="Moneyness from, % of F"
                    size="sm"
                    w={170}
                    min={20}
                    max={300}
                    value={lo}
                    onChange={setLo}
                    placeholder="every quote"
                  />
                  <NumberInput
                    label="to, % of F"
                    size="sm"
                    w={120}
                    min={20}
                    max={300}
                    value={hi}
                    onChange={setHi}
                    placeholder="every quote"
                    error={windowBad ? "above the low bound" : undefined}
                  />
                  <NumberInput
                    label="Widest spread, % of mid"
                    size="sm"
                    w={180}
                    min={2}
                    max={200}
                    value={maxSpread}
                    onChange={setMaxSpread}
                    placeholder={formatNumber(
                      typeof defaults.max_rel_spread === "number" ? defaults.max_rel_spread * 100 : null,
                      { digits: 0 },
                    )}
                  />
                  <NumberInput
                    label="Heston's strikes per maturity"
                    size="sm"
                    w={210}
                    min={6}
                    max={80}
                    value={hestonStrikes}
                    onChange={setHestonStrikes}
                    placeholder={String(defaults.n_strikes_heston ?? "")}
                  />
                </Group>
                <Text size="xs" c="dimmed" mt="sm">
                  The closest expiry to each maturity is fitted. By the spread, a quote counts by the inverse of its
                  bid-ask in vol; a window keeps the quotes between two strikes over the forward. A run keeps its
                  settings, and fitting it again uses them.
                </Text>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>
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
          {result && Object.keys(result.fits ?? {}).length > 0 && (
            <Panel title="The fitted parameters" hint="as fitted, with what they mean" flush>
              <FittedParams fits={result.fits ?? {}} />
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
                  aria-label={`Fit again ${r.calibration_id}`}
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
