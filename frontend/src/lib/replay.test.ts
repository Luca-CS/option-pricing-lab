import { describe, expect, it } from "vitest";
import { flatten, replayStart } from "./replay";

const phoenix = {
  type: "autocall",
  general: { underlying: "SPX", strike_date: null, maturity_months: 24 },
  coupon: { rate: 0.08, barrier: 0.7, memory: true },
  downside: { barrier: 0.6 },
};
const athena = {
  type: "autocall",
  general: { underlying: "SPX", strike_date: null, maturity_months: 36 },
  coupon: { rate: 0.06, barrier: 1.0, memory: false },
  downside: { barrier: 0.6 },
};
const capped = { type: "bullet", general: { underlying: "SPX", maturity_months: 24 }, downside: { barrier: null } };
const presets = [
  { name: "phoenix", sheet: phoenix },
  { name: "athena", sheet: athena },
  { name: "capital_protected", sheet: capped },
];
const all = presets.map((p) => p.name);

describe("a quote's backtest", () => {
  it("flattens a sheet to dotted fields, leaving its shape out", () => {
    expect(flatten(phoenix)).toMatchObject({ "general.underlying": "SPX", "coupon.barrier": 0.7 });
    expect(flatten(phoenix)).not.toHaveProperty("type");
  });

  it("starts from the closest preset of the quote's shape, with the fields that differ", () => {
    const quote = { ...phoenix, general: { ...phoenix.general, underlying: "NDX" }, downside: { barrier: 0.55 } };
    expect(replayStart(quote, presets, all)).toEqual({
      preset: "phoenix",
      underlying: "NDX",
      changes: { "downside.barrier": 0.55 },
      dates: [],
    });
  });

  it("leaves the dates to the replay's strike date, and says which", () => {
    const quote = { ...phoenix, general: { ...phoenix.general, strike_date: "2021-06-30" } };
    const start = replayStart(quote, presets, all);
    expect(start?.changes).toEqual({});
    expect(start?.dates).toEqual(["general.strike_date"]);
  });

  it("keeps to the presets the replay knows, and to the quote's shape", () => {
    expect(replayStart(phoenix, presets, ["athena"])?.preset).toBe("athena");
    expect(replayStart(capped, presets, ["phoenix", "athena"])).toBeNull();
  });

  it("takes a basket's first name when the sheet has no single underlying", () => {
    const quote = { ...phoenix, general: { underlying: "", underlyings: ["SX5E", "SPX"], maturity_months: 24 } };
    expect(replayStart(quote, presets, all)?.underlying).toBe("SX5E");
  });
});
