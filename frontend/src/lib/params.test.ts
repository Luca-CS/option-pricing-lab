import { describe, expect, it } from "vitest";
import { hestonView, ssviRows, sviRows } from "./params";

describe("fitted parameters", () => {
  it("reads SVI per maturity and its ATM vol from the total variance at k = 0", () => {
    // w(0) = a + b (-rho m + sqrt(m^2 + sigma^2)) = 0.01 + 0.1 (0 + 0.1) = 0.02 at T = 0.5, so a vol of 20 %
    const [row] = sviRows({ maturities: [0.5], params: [[0.01, 0.1, -0.5, 0, 0.1]] });
    expect(row?.a).toBe(0.01);
    expect(row?.atmVol).toBeCloseTo(0.2, 12);
    expect(sviRows({ maturities: [0.5], params: [[0.01, 0.1]] })).toEqual([]);
    expect(sviRows(null)).toEqual([]);
  });

  it("reads SSVI's ATM total variance as a vol", () => {
    const rows = ssviRows({ maturities: [0.25, 1], thetas: [0.01, 0.04], psis: [0.3, 0.4], rhos: [-0.6, -0.5] });
    expect(rows.map((r) => r.atmVol)).toEqual([0.2, 0.2]);
    expect(rows[1]?.psi).toBe(0.4);
  });

  it("gives Heston's vols, the half-life of a shock and Feller's ratio", () => {
    const h = hestonView({ v0: 0.04, kappa: 2, theta: 0.09, xi: 0.5, rho: -0.7 });
    expect(h?.spotVol).toBeCloseTo(0.2, 12);
    expect(h?.longRunVol).toBeCloseTo(0.3, 12);
    expect(h?.halfLife).toBeCloseTo(Math.LN2 / 2, 12);
    expect(h?.feller).toBeCloseTo((2 * 2 * 0.09) / 0.25, 12);
    expect(hestonView({ v0: 0.04 })).toBeNull();
  });
});
