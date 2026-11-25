import { describe, expect, it } from "vitest";
import { describeShock } from "./shocks";

describe("describeShock", () => {
  it("words the starting crash scenario", () => {
    const lines = describeShock({
      spot: { "*": -0.2 },
      vol: { "*": { parallel: 0, relative: 0, skew: 0.02, nodes: {}, relative_nodes: {}, term: {} } },
      rule: "realistic",
      rates: { "*": { parallel: -50, tenors: {} } },
      dividend_relative: { "*": -0.3 },
      corr: 0.15,
    });
    expect(lines).toEqual([
      { what: "Spot", move: "all −20 %" },
      { what: "Vol", move: "all skew +2.0 pts" },
      { what: "Dividends", move: "all −30 %" },
      { what: "Rates", move: "all −50 bp" },
      { what: "Correlation", move: "every pair +15.0 pts" },
      { what: "Vol rule", move: "realistic (spot-vol betas)" },
    ]);
  });

  it("names a single underlying, a pair, nodes and the roll, and shows what it does not know", () => {
    const lines = describeShock({
      spot: { SPX: 0.05 },
      vol: { SX5E: { parallel: 0.1, nodes: { "1,2": 0.01, "2,2": 0.01 } } },
      corr_pairs: { "SPX|SX5E": -0.1 },
      roll_days: 1,
      rule: "sticky_moneyness",
      future_field: 3,
    });
    expect(lines).toEqual([
      { what: "Spot", move: "SPX +5 %" },
      { what: "Vol", move: "SX5E +10.0 pts, 2 nodes" },
      { what: "Correlation", move: "SPX / SX5E −10.0 pts" },
      { what: "Time", move: "1 business day later" },
      { what: "future_field", move: "3" },
    ]);
  });

  it("leaves out a move that is zero", () => {
    expect(describeShock({ spot: { "*": 0 }, rates: { "*": { parallel: 0, tenors: { "2": 0 } } } })).toEqual([]);
  });
});
