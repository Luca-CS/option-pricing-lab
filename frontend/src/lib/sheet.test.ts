import { describe, expect, it } from "vitest";
import { couponOrStrike, describeSheet, display, isPercent, parse, scaleOf, suffix, toRequest } from "./sheet";

const barrier = { name: "barrier", kind: "number", unit: "of initial" };
const rate = { name: "rate", kind: "number", unit: "per annum" };
const repo = { name: "repo", kind: "number", unit: "bp", optional: true };
const vol = { name: "vol", kind: "number", unit: "vol points", optional: true };
const months = { name: "maturity_months", kind: "int", unit: "months" };
const lag = { name: "pay_lag", kind: "int", unit: "business days" };
const names = { name: "underlyings", kind: "names", unit: "" };
const delta = { name: "delta_max_coupon", kind: "number", unit: "% per 1 %", optional: true };

describe("a sheet's fields on screen", () => {
  it("show levels and rates in percent, bp in bp, the rest as they are", () => {
    expect(isPercent(barrier)).toBe(true);
    expect(scaleOf(rate)).toBe(100);
    expect(scaleOf(repo)).toBe(1e4);
    expect(scaleOf(months)).toBe(1);
    expect(scaleOf({ name: "participation", kind: "number", unit: "" })).toBe(100);
    expect(display(barrier, 0.6)).toBe("60");
    expect(display(rate, 0.02775502929891407)).toBe("2.775503");
    expect(display(repo, -0.0005)).toBe("-5");
    expect(display(vol, -0.0025)).toBe("-0.25");
    expect(display(months, 24)).toBe("24");
    expect(display(names, ["SPX", "SX5E"])).toBe("SPX, SX5E");
    expect(display(barrier, null)).toBe("");
  });

  it("read what a person types back into the API's fractions", () => {
    expect(parse(barrier, "60")).toBe(0.6);
    expect(parse(barrier, "60 %")).toBe(0.6);
    expect(parse(rate, "7,5")).toBe(0.075);
    expect(parse(repo, "-5")).toBe(-0.0005);
    expect(parse(repo, "")).toBeNull();
    expect(parse(barrier, "")).toBe("");
    expect(parse(barrier, "sixty")).toBe("sixty");
    expect(parse(months, "18")).toBe(18);
    expect(parse(months, "")).toBe("");
    expect(parse(names, "spx, sx5e ndx")).toEqual(["SPX", "SX5E", "NDX"]);
    expect(parse({ name: "strike_date", kind: "date" }, "")).toBeNull();
  });

  it("say their unit", () => {
    expect(suffix(rate)).toBe("% p.a.");
    expect(suffix(barrier)).toBe("%");
    expect(suffix(vol)).toBe("pt");
    expect(suffix(repo)).toBe("bp");
    expect(suffix(lag)).toBe("days");
    expect(suffix(months)).toBe("months");
    expect(suffix(delta)).toBe("% / 1 %");
  });
});

describe("a sheet as a whole", () => {
  const sheet = {
    type: "autocall",
    general: { basket: "single", underlying: "SPX", maturity_months: 24 },
    wrapper: { solve: "coupon" },
    coupon: { type: "conditional", rate: 0.08, barrier: 0.7 },
    downside: { type: "pdi", barrier: 0.6, observation: "european" },
  };

  it("goes back to the API as {type, values}", () => {
    const r = toRequest(sheet);
    expect(r.type).toBe("autocall");
    expect(Object.keys(r.values)).toEqual(["general", "wrapper", "coupon", "downside"]);
    expect(r.values.coupon).toEqual(sheet.coupon);
    expect(r.values.coupon).not.toBe(sheet.coupon);
    expect(toRequest({ type: "bullet" }).type).toBe("bullet");
  });

  it("says in one line what it is", () => {
    expect(describeSheet(sheet)).toBe("24M · SPX · conditional coupon · PDI 60 % european");
    expect(describeSheet({ ...sheet, wrapper: { solve: "upfront" } })).toContain("conditional coupon 8.00 %");
    expect(
      describeSheet({ ...sheet, general: { basket: "worst_of", underlyings: ["SPX", "SX5E"], maturity_months: 12 } }),
    ).toContain("worst of SPX, SX5E");
  });
});

describe("what a trade pays on", () => {
  it("reads a coupon per annum or a swap's strike in vol", () => {
    expect(couponOrStrike({ coupon_rate: 0.0625 })).toBe("6.25 %");
    expect(couponOrStrike({ strike: 0.215 })).toBe("21.50 vol");
    expect(couponOrStrike(null)).toBe("—");
  });
});

describe("an autocall's mechanisms in its line", () => {
  it("says when it calls name by name, and its lizard", () => {
    const sheet = {
      type: "autocall",
      general: { maturity_months: 24, basket: "worst_of", underlyings: ["SPX", "NDX"] },
      autocall: { mechanism: "asynchronous", lizard: true, lizard_barrier: 0.75, lizard_months: 12 },
      coupon: { type: "snowball" },
    };
    expect(describeSheet(sheet)).toContain("asynchronous call · lizard 75 % at 12M");
  });
});
