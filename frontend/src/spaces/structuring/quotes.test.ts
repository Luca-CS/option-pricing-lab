import { describe, expect, it } from "vitest";
import { sheetDifferences } from "./Quotes";
import { snapshotDate } from "./QuoteView";

describe("comparing variants", () => {
  it("lists the fields whose values differ, one value per sheet", () => {
    const a = { type: "autocall", coupon: { barrier: 0.7, memory: true }, general: { maturity_months: 24 } };
    const b = { type: "autocall", coupon: { barrier: 0.6, memory: true }, general: { maturity_months: 24 } };
    const c = { type: "autocall", coupon: { barrier: 0.7 }, general: { maturity_months: 36 } };
    expect(sheetDifferences([a, b])).toEqual([["coupon.barrier", [0.7, 0.6]]]);
    expect(sheetDifferences([a, b, c])).toEqual([
      ["coupon.barrier", [0.7, 0.6, 0.7]],
      ["coupon.memory", [true, true, undefined]],
      ["general.maturity_months", [24, 24, 36]],
    ]);
    expect(sheetDifferences([a, a])).toEqual([]);
  });

  it("reads a snapshot's date from its id", () => {
    expect(snapshotDate("SPX-20210630T1600-8c7d3283e7")).toBe("2021-06-30");
    expect(snapshotDate("in-use")).toBeNull();
    expect(snapshotDate(null)).toBeNull();
  });
});
