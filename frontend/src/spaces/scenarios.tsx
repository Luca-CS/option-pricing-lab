// A scenario run, read the same way in Trading › Book risk and Risk › Stress: the book under each signed scenario,
// each trade under each, and (when asked) the spot × vol grid of the book's P&L.
import type { Schemas } from "../api/client";
import { type Estimate, formatNumber, formatPercent } from "../lib/format";
import { Panel } from "../ui/Panel";
import { NodeHeatmap } from "../ui/Risk";
import { Est } from "../ui/Value";
import { commonClasses as c, DataTable, TradeLink } from "./common";

type Run = Schemas["ScenarioRun"];

/** A grid cell as the API sends it: [value, se], {value, se}, or nothing at the base point. */
function cell(x: unknown): Estimate | null {
  if (Array.isArray(x) && typeof x[0] === "number") return { value: x[0], se: typeof x[1] === "number" ? x[1] : null };
  if (x && typeof x === "object" && "value" in x) return x as Estimate;
  return null;
}

export function SpotVolGrid({ grid }: { grid: NonNullable<Run["grid"]> }) {
  const spots = (grid.spots as number[] | undefined) ?? [];
  const vols = (grid.vols as number[] | undefined) ?? [];
  const pnl = (grid.pnl as unknown[][] | undefined) ?? [];
  return (
    <Panel title="Spot × vol" hint="the book's P&L, currency; vol shifts in points, sticky moneyness">
      <NodeHeatmap
        strikes={spots.map((s) => formatPercent(s, { digits: 0, sign: true }))}
        tenors={vols.map((v) => `${formatNumber(v * 100, { digits: 0, sign: true })} pts`)}
        cells={spots.map((_, i) => vols.map((_, j) => cell(pnl[i]?.[j])))}
        caption="The book's P&L for each spot shift (rows) and vol shift (columns)"
        cornerLabel="spot \\ vol"
        tones={["loss", "gain"]}
      />
    </Panel>
  );
}

export function ScenariosView({ run }: { run: Run }) {
  return (
    <>
      <div className={c.grid}>
        <Panel
          title="The book under each scenario"
          hint={
            run.signed_by
              ? `library v${run.version}, signed by ${run.signed_by}`
              : "the starting library, not signed yet"
          }
          flush
        >
          <DataTable
            caption="The book's P&L under each scenario"
            head={["Scenario", "What it does", "Book P&L ± SE"]}
            num={[2]}
            rows={run.scenarios.map((s) => ({
              key: s.name,
              cells: [
                s.name,
                <span key="d" className={`${c.faint} ${c.wrap}`}>
                  {s.description ?? ""}
                </span>,
                <Est key="b" value={s.book} signed />,
              ],
            }))}
          />
        </Panel>
        <Panel title="By trade" hint="currency" flush>
          <DataTable
            caption="Each trade's P&L under each scenario"
            head={["Trade", ...run.scenarios.map((s) => s.name)]}
            num={run.scenarios.map((_, i) => i + 1)}
            rows={(run.positions ?? []).map((p) => ({
              key: p.name,
              cells: [
                <TradeLink key="t" id={p.name} />,
                ...run.scenarios.map((s) => {
                  const e = p.pnl?.[s.name];
                  return e && "value" in e ? <Est key={s.name} value={e as Estimate} signed /> : "—";
                }),
              ],
            }))}
          />
        </Panel>
      </div>
      {run.grid && <SpotVolGrid grid={run.grid} />}
    </>
  );
}
