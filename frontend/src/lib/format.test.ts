import { describe, expect, it } from "vitest";
import {
  DASH,
  digitsForSe,
  formatBp,
  formatDate,
  formatEstimate,
  formatNumber,
  formatPercent,
  formatSeconds,
  fromPercentInput,
  isNoise,
  MINUS,
  signOf,
  toPercentInput,
} from "./format";

describe("numbers", () => {
  it("separates thousands and writes a real minus", () => {
    expect(formatNumber(1234567.8)).toBe("1,234,568");
    expect(formatNumber(-39416.7)).toBe(`${MINUS}39,417`);
    expect(formatNumber(-0.5, { digits: 0 })).toBe(`${MINUS}1`);
    expect(formatNumber(96.69035, { digits: 2 })).toBe("96.69");
  });

  it("writes + only when asked, and never on a zero", () => {
    expect(formatNumber(734762.99, { sign: true })).toBe("+734,763");
    expect(formatNumber(734762.99)).toBe("734,763");
    expect(formatNumber(0.004, { digits: 2, sign: true })).toBe("0.00");
    expect(formatNumber(-0.004, { digits: 2, sign: true })).toBe("0.00");
    expect(formatNumber(0, { sign: true })).toBe("0");
  });

  it("writes a dash for what is not a number", () => {
    expect(formatNumber(Number.NaN)).toBe(DASH);
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe(DASH);
    expect(formatNumber(null)).toBe(DASH);
    expect(formatNumber(undefined)).toBe(DASH);
  });

  it("knows the sign as written", () => {
    expect(signOf(-0.004, 2)).toBe(0);
    expect(signOf(-0.006, 2)).toBe(-1);
    expect(signOf(12)).toBe(1);
    expect(signOf(Number.NaN)).toBe(0);
  });
});

describe("percentages and basis points", () => {
  it("shows fractions as percentages", () => {
    expect(formatPercent(0.2133)).toBe("21.33 %");
    expect(formatPercent(0.02775502929891407)).toBe("2.78 %");
    expect(formatPercent(-0.05, { digits: 1, sign: true })).toBe(`${MINUS}5.0 %`);
    expect(formatPercent(0.05, { digits: 1, sign: true })).toBe("+5.0 %");
    expect(formatPercent(null)).toBe(DASH);
  });

  it("shows fractions as basis points", () => {
    expect(formatBp(0.0025)).toBe("25 bp");
    expect(formatBp(-0.011256, { digits: 1 })).toBe(`${MINUS}112.6 bp`);
  });

  it("converts at the edge without float noise", () => {
    expect(toPercentInput(0.07)).toBe(7);
    expect(toPercentInput(0.0218386)).toBe(2.18386);
    expect(fromPercentInput(7)).toBe(0.07);
    expect(fromPercentInput(60)).toBe(0.6);
    expect(fromPercentInput(toPercentInput(0.123456789))).toBe(0.123456789);
    expect(toPercentInput(null)).toBeNull();
    expect(fromPercentInput(Number.NaN)).toBeNull();
  });
});

describe("Monte Carlo numbers", () => {
  it("say when they are inside their noise", () => {
    expect(isNoise({ value: -367, se: 209 })).toBe(true);
    expect(isNoise({ value: -9948, se: 299 })).toBe(false);
    expect(isNoise({ value: 1, se: null })).toBe(false);
    expect(isNoise({ value: 0, se: 0 })).toBe(false);
    expect(isNoise({ value: 0.1, se: 0.05 })).toBe(false);
  });

  it("show the standard error to one significant digit, the value with the same decimals", () => {
    expect(digitsForSe(191)).toBe(0);
    expect(digitsForSe(0.0658)).toBe(2);
    expect(digitsForSe(0.0004)).toBe(4);
    expect(digitsForSe(null, 3)).toBe(3);
    expect(formatEstimate({ value: -39416.7, se: 191.2 })).toEqual({
      value: `${MINUS}39,417`,
      se: "191",
      noise: false,
      sign: -1,
    });
    expect(formatEstimate({ value: 0.02775502929891407, se: 0.00065827 }, { scale: 100 })).toEqual({
      value: "2.78",
      se: "0.07",
      noise: false,
      sign: 1,
    });
    expect(formatEstimate({ value: 387.2, se: 321.6 }, { sign: true })).toEqual({
      value: "+387",
      se: "322",
      noise: true,
      sign: 1,
    });
    expect(formatEstimate({ value: 8.63, se: null }).se).toBeNull();
  });
});

describe("dates and durations", () => {
  it("reads ISO dates", () => {
    expect(formatDate("2021-06-30")).toBe("30 Jun 2021");
    expect(formatDate("2021-06-30T16:00:00")).toBe("30 Jun 2021");
    expect(formatDate("2021-09-05")).toBe("5 Sep 2021");
    expect(formatDate("2021-13-05")).toBe("2021-13-05");
    expect(formatDate(null)).toBe(DASH);
    expect(formatDate("soon")).toBe("soon");
  });

  it("says how long", () => {
    expect(formatSeconds(2.6)).toBe("2.6 s");
    expect(formatSeconds(13.4)).toBe("13 s");
    expect(formatSeconds(95)).toBe("1 min 35 s");
    expect(formatSeconds(120)).toBe("2 min");
    expect(formatSeconds(-1)).toBe(DASH);
  });
});
