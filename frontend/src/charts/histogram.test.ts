import { describe, expect, it } from "vitest";
import { histogram } from "./charts";

describe("histogram", () => {
  it("counts every value once, the maximum in the last bin", () => {
    const { edges, counts } = histogram([0, 1, 2, 3, 4, 10], 5);
    expect(edges).toEqual([0, 2, 4, 6, 8, 10]);
    expect(counts).toEqual([2, 2, 1, 0, 1]);
  });

  it("puts equal values in one bin and drops what is not a number", () => {
    expect(histogram([-3, -3, Number.NaN], 10)).toEqual({ edges: [-3, -2], counts: [2] });
  });

  it("has nothing to bin without values", () => {
    expect(histogram([], 10)).toEqual({ edges: [], counts: [] });
  });

  it("bins losses and gains around zero", () => {
    const values = [-92_075, -50_000, -1_000, 500, 28_000];
    const { edges, counts } = histogram(values, 4);
    expect(counts.reduce((a, b) => a + b, 0)).toBe(values.length);
    expect(edges[0]).toBe(-92_075);
    expect(edges.at(-1)).toBe(28_000);
  });
});
