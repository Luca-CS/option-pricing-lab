import { describe, expect, it } from "vitest";
import { modelSpread } from "./Calibration";
import { interpolate, strikeMoneyness, tenorYears } from "./Market";
import { cashPaid } from "./Replay";

describe("the quant screens' readings", () => {
  it("read a risk grid's tenor and strike", () => {
    expect(tenorYears("1y")).toBe(1);
    expect(tenorYears("18m")).toBe(1.5);
    expect(tenorYears("3M")).toBe(0.25);
    expect(tenorYears("soon")).toBeNull();
    expect(strikeMoneyness("80%")).toBe(0.8);
    expect(strikeMoneyness("100 %")).toBe(1);
    expect(strikeMoneyness("K")).toBeNull();
  });

  it("interpolate a smile between its points, and nothing outside them", () => {
    expect(interpolate([0.8, 0.9, 1], [0.3, 0.25, 0.2], 0.85)).toBeCloseTo(0.275);
    expect(interpolate([0.8, 0.9], [0.3, 0.25], 0.8)).toBe(0.3);
    expect(interpolate([0.8, 0.9], [0.3, 0.25], 1.2)).toBeNull();
  });

  it("measure the spread across models", () => {
    expect(modelSpread([{ value: 8.6 }, { value: 8.83 }, { value: 8.76 }])).toBeCloseTo(0.23);
    expect(modelSpread([{ value: 8.6 }])).toBeNull();
  });

  it("count the cash paid by each date from the payments", () => {
    const events = [
      { date: "2020-04-02", payment: "2020-04-09", amount: 1.14 },
      { date: "2021-01-04", payment: "2021-01-11", amount: 100 },
    ];
    expect(cashPaid(["2020-04-08", "2020-04-09", "2021-01-11"], events)).toEqual([0, 1.14, 101.14]);
  });
});
