import { describe, expect, it } from "vitest";
import { deltaNote, nodeDeltas } from "./greeks";
import { extremes, liveTenors, together, worstTenor } from "./pnlDetail";

describe("a day of the P&L opened", () => {
  it("adds a rule's three steps, the same amount whatever the rule", () => {
    const sm = { steps: { spot: { value: 10, se: 1 }, vol: { value: -4, se: 1 }, skew: { value: 1, se: 0.5 } } };
    const ss = { steps: { spot: { value: 12, se: 1 }, vol: { value: -6, se: 1 }, skew: { value: 1, se: 0.5 } } };
    expect(together(sm)).toBe(7);
    expect(together(ss)).toBe(together(sm));
    expect(together({})).toBe(0);
  });

  it("leaves out the tenors the book does not reach", () => {
    expect(liveTenors(["1m", "3m", "6m", "1y"], [3])).toEqual([0, 1, 2]);
    expect(liveTenors(["1m", "3m"], undefined)).toEqual([0, 1]);
  });

  it("names the node and the maturity that lost most", () => {
    const e = (value: number) => ({ value, se: 0.1 });
    const nodes = [
      [e(1), e(-3)],
      [e(5), null],
    ];
    const { loss, gain } = extremes(nodes, ["90%", "100%"], ["1m", "3m"]);
    expect(loss).toMatchObject({ strike: "90%", tenor: "3m", value: -3 });
    expect(gain).toMatchObject({ strike: "100%", tenor: "1m", value: 5 });
    expect(extremes([[e(0)]], ["100%"], ["1m"])).toEqual({ loss: null, gain: null });
    expect(worstTenor([2, -1, -4], ["1m", "3m", "6m"])).toEqual({ tenor: "6m", value: -4 });
    expect(worstTenor([1, 2], ["1m", "3m"])).toBeNull();
  });

  it("reads a node in delta, from the group that holds its name", () => {
    expect(deltaNote(0.5237)).toBe("52Δ");
    expect(deltaNote(null)).toBeNull();
    const groups = [{ names: ["NDX"] }, { names: ["SPX"], node_deltas: { SPX: [[0.9], [0.5]] } }];
    expect(nodeDeltas(groups, "SPX")).toEqual([[0.9], [0.5]]);
    expect(nodeDeltas(groups, "NDX")).toBeNull();
  });
});
