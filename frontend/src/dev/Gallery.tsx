// The design system on one page (docs/ux.md §4): every shared component, fed with numbers the API
// gave on the demo data. Served in development only (`npm run dev`, /dev/gallery); the production build leaves it out.
import { Button, Group, SegmentedControl, Stack, TextInput, useMantineColorScheme } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../api/client";
import { CurveChart, LadderChart, WaterfallChart } from "../charts";
import type { Estimate } from "../lib/format";
import { ladderSeries, nodeGrid, rows } from "../lib/greeks";
import { JobProgress, Timeline } from "../ui/Flow";
import { Kpi, KpiRow } from "../ui/Kpi";
import { EntityLink, MarketBadge, OriginBadge } from "../ui/Markers";
import { Panel } from "../ui/Panel";
import { type Cell, GreeksTable, NodeHeatmap } from "../ui/Risk";
import { Empty, Failure, Loading } from "../ui/States";
import { Est, Num, Pct } from "../ui/Value";
import classes from "./Gallery.module.css";
import { samples } from "./samples";

const book = samples.book as Record<string, Estimate>;
const grid = nodeGrid(book, "SPX", {
  strike_labels: [...samples.grid.strike_labels],
  tenor_labels: [...samples.grid.tenor_labels.slice(0, 5)],
});
const ladderBook = samples.ladder.book as Record<string, Estimate>;
const failure = new ApiError(422, {
  type: "about:blank",
  title: "Invalid input",
  status: 422,
  detail: "the request is not valid: see errors",
  errors: [{ field: "coupon.barrier", message: "Input should be less than or equal to 3" }],
});

function setRoot(name: string, value: string | null) {
  if (value === null) document.documentElement.removeAttribute(name);
  else document.documentElement.setAttribute(name, value);
}

export default function Gallery() {
  const { colorScheme, setColorScheme } = useMantineColorScheme({ keepTransitions: true });
  const [cell, setCell] = useState<Cell | null>({ row: 2, col: 3 });
  const [palette, setPalette] = useState("standard");
  const [density, setDensity] = useState("compact");
  const selected = cell ? grid.cells[cell.row]?.[cell.col] : null;
  const desk = samples.quote.desk;
  const steps = desk.lines
    .filter((l) => l.in_price)
    .map((l) => ({ label: l.label.replace(/^Digital: /, "digital: "), value: l.coupon_bp }));

  return (
    <main className={classes.page}>
      <header className={classes.head}>
        <h1 className={classes.title}>Design system</h1>
        <span className={classes.muted}>numbers from the API on the demo data · development only</span>
        <Group gap="sm" ml="auto">
          <SegmentedControl
            size="xs"
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
            value={palette}
            onChange={(v) => {
              setPalette(v);
              setRoot("data-palette", v === "colorblind" ? "colorblind" : null);
            }}
            data={[
              { label: "Green / red", value: "standard" },
              { label: "Blue / orange", value: "colorblind" },
            ]}
            aria-label="Signed values"
          />
          <SegmentedControl
            size="xs"
            value={density}
            onChange={(v) => {
              setDensity(v);
              setRoot("data-density", v === "comfortable" ? "comfortable" : null);
            }}
            data={[
              { label: "Compact", value: "compact" },
              { label: "Comfortable", value: "comfortable" },
            ]}
            aria-label="Density"
          />
        </Group>
      </header>

      <Panel title="Numbers" hint="monospace, tabular, a real minus; ± SE muted; inside its noise: grey italic">
        <div className={classes.specimens}>
          <span>Signed</span>
          <Num value={1234567.8} signed />
          <span>Estimate</span>
          <Est value={book["delta|SPX|"]} signed />
          <span>In its noise</span>
          <Est value={book["theta|*|"]} signed />
          <span>Percent of a fraction</span>
          <Pct value={samples.context.atm_vols["1"]} />
          <span>Quote headline, % p.a.</span>
          <Est value={{ value: samples.quote.headline.value, se: samples.quote.headline.se }} />
          <span>Closed form, no SE</span>
          <Num value={8.63} digits={2} />
        </div>
      </Panel>

      <KpiRow label="Key figures">
        <Kpi label="Delta" unit="per +1 % spot">
          <Est value={book["delta|SPX|"]} signed hideSe />
        </Kpi>
        <Kpi label="Vega" unit="per vol point" sub={<Est value={book["vega|SPX|"]} hideSe={false} />}>
          <Est value={book["vega|SPX|"]} signed hideSe />
        </Kpi>
        <Kpi label="Theta" unit="per business day" sub="in the noise">
          <Est value={book["theta|*|"]} signed hideSe />
        </Kpi>
        <Kpi label="Reserve left" unit="USD" meter={0.44} sub="44 % of 13,237 frozen at issue">
          <Num value={5857.5} />
        </Kpi>
      </KpiRow>

      <div className={classes.row}>
        <Panel title="Markers" hint="origin on every market object; entities open where they lead">
          <Stack gap="sm">
            <Group gap="xs">
              <OriginBadge origin="observed" />
              <OriginBadge origin="derived" />
              <OriginBadge origin="lake:reconstructed" />
              <OriginBadge origin="synthetic" />
              <OriginBadge origin="flat" />
            </Group>
            <Group gap="xs">
              <MarketBadge
                market={{
                  snapshot_id: samples.market.snapshot_id,
                  as_of: samples.market.as_of,
                  origin: samples.market.origin.surface,
                }}
                onOpen={() => undefined}
              />
              <MarketBadge market={{ underlying: "SPX", as_of: "2021-06-30", origin: "observed" }} stale />
            </Group>
            <Group gap="xs">
              <EntityLink
                label="DEMO-03"
                to="/trading/trades/DEMO-03"
                places={[
                  { label: "Its Greeks", to: "/trading/risk?trade=DEMO-03" },
                  { label: "Its replay", to: "/quant/replay?trade=DEMO-03" },
                ]}
              />
            </Group>
          </Stack>
        </Panel>
        <Panel title="Jobs" hint="the button becomes the progress; never a frozen page">
          <Stack gap="sm" align="flex-start">
            <Button>Quote</Button>
            <JobProgress job={{ state: "queued" }} onCancel={() => undefined} />
            <JobProgress
              job={{ state: "running", progress: 0.42, step: "quote of a Phoenix", eta: 2.6 }}
              onCancel={() => undefined}
            />
            <JobProgress job={{ state: "running", progress: null, step: "VaR: revaluations" }} />
            <JobProgress job={{ state: "error", error: "ScriptError: line 4: a NaN condition" }} />
          </Stack>
        </Panel>
      </div>

      <div className={classes.row}>
        <Panel title="Greeks" hint={`${samples.method.name}, ${samples.method.n_paths} paths`} flush>
          <GreeksTable rows={rows(book)} units={samples.units} caption="Greeks of the demo book" />
        </Panel>
        <Panel title="Vega by node" hint="per +1 vol point at one node · K / F(T)">
          <NodeHeatmap
            strikes={grid.strikes}
            tenors={grid.tenors}
            cells={grid.cells}
            selected={cell}
            onSelect={setCell}
            caption="Vega of the demo book by strike and tenor"
          />
          <p className={classes.muted}>
            Selected:{" "}
            {cell ? (
              <>
                {grid.strikes[cell.row]} · {grid.tenors[cell.col]} <Est value={selected} signed />
              </>
            ) : (
              "none"
            )}
          </p>
        </Panel>
      </div>

      <div className={classes.row}>
        <Panel title="Delta ladder" hint="per +1 % spot, three vol rules">
          <LadderChart
            shifts={[...samples.ladder.points]}
            series={ladderSeries(ladderBook, "delta", samples.ladder.rules, samples.ladder.points)}
            unit="per +1 %"
            label="Delta of the demo book against a spot shift, three vol rules"
          />
        </Panel>
        <Panel title="From the base coupon to the client's" hint="bp of coupon p.a.">
          <WaterfallChart
            start={{ label: "base (LV)", value: desk.coupon_base * 10_000 }}
            steps={steps}
            end={{ label: "client", value: desk.coupon_final * 10_000 }}
            digits={1}
            height={300}
            label="Coupon waterfall of a Phoenix quote"
          />
        </Panel>
      </div>

      <div className={classes.row}>
        <Panel title="ATM term structure" hint="implied vol at the money">
          <CurveChart
            curves={[
              {
                name: "ATM vol",
                points: Object.entries(samples.context.atm_vols).map(([t, v]) => [Number(t), v] as [number, number]),
              },
            ]}
            xName="years"
            yName="vol"
            xPercent={false}
            label="ATM implied volatility by maturity"
          />
        </Panel>
        <Panel title="When it ends" hint="a quote's call dates">
          <Timeline
            label="Call dates of a Phoenix"
            events={samples.quote.schedule.map((s, i) => ({
              id: s.observation,
              date: s.observation,
              kind: i === 0 ? "called" : "upcoming",
              label: `ends here with probability ${(s.p_end * 100).toFixed(1)} %`,
            }))}
          />
        </Panel>
      </div>

      <div className={classes.row}>
        <Panel title="States">
          <Stack gap="md">
            <Loading />
            <Empty title="No P&L run yet" action={<Button variant="light">Run the EOD backfill</Button>}>
              The explained P&L reads the EOD runs. Run the backfill over a period to see it here.
            </Empty>
            <Failure error={failure} />
          </Stack>
        </Panel>
        <Panel title="Controls">
          <Stack gap="sm" align="flex-start">
            <Group gap="xs">
              <Button>Primary</Button>
              <Button color="amber">Book it</Button>
              <Button variant="default">Default</Button>
              <Button variant="subtle">Subtle</Button>
            </Group>
            <TextInput label="Coupon barrier" description="% of initial" defaultValue="70.00" w={220} />
            <TextInput
              label="Coupon barrier"
              description="% of initial"
              defaultValue="320"
              error="must be at most 300 %"
              w={220}
            />
          </Stack>
        </Panel>
      </div>
    </main>
  );
}
