// A model's fitted parameters (the vol lab's fit, the calibration in use): SVI and SSVI per maturity, Heston with what
// its numbers mean. The numbers are the API's; lib/params.ts only reads them.
import { Badge, Group, SegmentedControl, Text } from "@mantine/core";
import { useState } from "react";
import { formatNumber, formatPercent } from "../../lib/format";
import { hestonView, ssviRows, sviRows } from "../../lib/params";
import { commonClasses as c, DataTable } from "../common";

type Dict = Record<string, unknown>;
const f = (x: number | null | undefined, digits = 4) => formatNumber(x ?? null, { digits });

export function SviTable({ params, caption }: { params: Dict | null | undefined; caption: string }) {
  const rows = sviRows(params);
  if (rows.length === 0) return <p className={`${c.faint} ${c.pad}`}>No SVI parameters.</p>;
  return (
    <DataTable
      caption={caption}
      head={["T", "a", "b", "ρ", "m", "σ", "ATM vol"]}
      num={[0, 1, 2, 3, 4, 5, 6]}
      rows={rows.map((r) => ({
        key: String(r.T),
        cells: [f(r.T, 3), f(r.a, 5), f(r.b, 4), f(r.rho, 3), f(r.m, 4), f(r.sigma, 4), formatPercent(r.atmVol)],
      }))}
    />
  );
}

export function SsviTable({ params, caption }: { params: Dict | null | undefined; caption: string }) {
  const rows = ssviRows(params);
  if (rows.length === 0) return <p className={`${c.faint} ${c.pad}`}>No SSVI parameters.</p>;
  return (
    <DataTable
      caption={caption}
      head={["T", "θ (ATM total variance)", "ψ", "ρ", "ATM vol"]}
      num={[0, 1, 2, 3, 4]}
      rows={rows.map((r) => ({
        key: String(r.T),
        cells: [f(r.T, 3), f(r.theta, 5), f(r.psi, 4), f(r.rho, 3), formatPercent(r.atmVol)],
      }))}
    />
  );
}

export function HestonTable({ params, caption }: { params: Dict | null | undefined; caption: string }) {
  const h = hestonView(params);
  if (!h) return <p className={`${c.faint} ${c.pad}`}>No Heston parameters.</p>;
  const fellerOk = h.feller !== null && h.feller >= 1;
  return (
    <>
      <DataTable
        caption={caption}
        head={["Parameter", "Value", "Reads as"]}
        num={[1]}
        rows={[
          { key: "v0", cells: ["v0", f(h.v0, 5), `today's vol ${formatPercent(h.spotVol)}`] },
          {
            key: "kappa",
            cells: [
              "κ",
              f(h.kappa, 3),
              h.halfLife ? `a shock halves in ${f(h.halfLife, 2)} years` : "no mean reversion",
            ],
          },
          { key: "theta", cells: ["θ", f(h.theta, 5), `long-run vol ${formatPercent(h.longRunVol)}`] },
          { key: "xi", cells: ["ξ", f(h.xi, 3), "vol of the variance"] },
          { key: "rho", cells: ["ρ", f(h.rho, 3), "spot-vol correlation: the skew"] },
        ]}
      />
      <Group gap="xs" className={c.pad}>
        <Text size="sm">Feller 2κθ / ξ² = {f(h.feller, 3)}</Text>
        <Badge size="sm" variant="light" color={fellerOk ? "green" : "orange"}>
          {fellerOk ? "the variance stays positive" : "the variance can reach zero"}
        </Badge>
      </Group>
    </>
  );
}

/** One fit's models, a tab each: SVI, SSVI, Heston (SLV shows its mix and its Heston). */
export function FittedParams({ fits }: { fits: Record<string, { params?: Dict | null }> }) {
  const models = ["svi", "ssvi", "heston", "slv"].filter((m) => m in fits);
  const [tab, setTab] = useState<string | null>(null);
  const shown = tab && models.includes(tab) ? tab : models[0];
  if (!shown) return null;
  const params = fits[shown]?.params ?? null;
  const name: Record<string, string> = { svi: "SVI", ssvi: "SSVI", heston: "Heston", slv: "SLV" };
  return (
    <>
      {models.length > 1 && (
        <div className={c.pad}>
          <SegmentedControl
            size="xs"
            value={shown}
            onChange={setTab}
            data={models.map((m) => ({ value: m, label: name[m] ?? m }))}
            aria-label="Model"
          />
        </div>
      )}
      {shown === "svi" && <SviTable params={params} caption="SVI's parameters by maturity" />}
      {shown === "ssvi" && <SsviTable params={params} caption="SSVI's parameters by maturity" />}
      {shown === "heston" && <HestonTable params={params} caption="Heston's parameters" />}
      {shown === "slv" && <HestonTable params={(params?.heston as Dict) ?? null} caption="SLV's Heston parameters" />}
    </>
  );
}
