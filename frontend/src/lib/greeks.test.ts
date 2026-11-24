import { describe, expect, it } from "vitest";
import { greekLabel, ladderKey, ladderSeries, nodeGrid, parseKey, rows, unitOf } from "./greeks";

const units = {
  delta: "currency per +1 % spot",
  vega: "currency per +1 implied vol point",
  vega_relative: "currency per +1 % of every implied vol",
  theta: "currency per business day (roll, market frozen)",
  value: { value: "currency", se: null },
};

describe("risk measures", () => {
  it("split their keys", () => {
    expect(parseKey("vega_node|SPX|80%@1y")).toEqual({ greek: "vega_node", name: "SPX", bucket: "80%@1y" });
    expect(parseKey("theta|*|")).toEqual({ greek: "theta", name: "*", bucket: "" });
    expect(parseKey("odd")).toEqual({ greek: "odd", name: "", bucket: "" });
  });

  it("find their unit, the most precise first", () => {
    expect(unitOf("delta", units)).toBe(units.delta);
    expect(unitOf("delta_sticky_strike", units)).toBe(units.delta);
    expect(unitOf("vega_relative", units)).toBe(units.vega_relative);
    expect(unitOf("vega_node", units)).toBe(units.vega);
    expect(unitOf("rho", units)).toBeUndefined();
    expect(unitOf("value", units)).toBeUndefined();
  });

  it("have names for people", () => {
    expect(greekLabel("gamma_5")).toBe("Gamma 5 %");
    expect(greekLabel("something_new")).toBe("something new");
  });

  it("keep the headline greeks of each name, without buckets", () => {
    const book = {
      "vega|SPX|": { value: -9948, se: 299 },
      "delta|SPX|": { value: -39417, se: 191 },
      "vega_tenor|SPX|1y": { value: -9380, se: 544 },
      "delta|SX5E|": { value: 10, se: 1 },
      "theta|*|": { value: -367, se: 209 },
    };
    expect(rows(book).map((r) => r.key)).toEqual(["delta|SPX|", "delta|SX5E|", "vega|SPX|", "theta|*|"]);
  });

  it("read a ladder by rule and shift", () => {
    expect(ladderKey("delta", "realistic", -0.05)).toBe("delta@realistic@-0.0500");
    expect(ladderKey("delta", "realistic", 0)).toBe("delta@realistic@+0.0000");
    const book = {
      "delta@realistic@-0.0500": { value: -36571.7, se: 220.7 },
      "delta@realistic@+0.0000": { value: -39416.7, se: 191.2 },
      "delta@sticky_strike@+0.0000": { value: -39416.7, se: 191.2 },
    };
    expect(ladderSeries(book, "delta", ["realistic", "sticky_strike"], [-0.05, 0])).toEqual([
      { name: "realistic", values: [book["delta@realistic@-0.0500"], book["delta@realistic@+0.0000"]] },
      { name: "sticky strike", values: [book["delta@sticky_strike@+0.0000"]] },
    ]);
  });

  it("lay the node vegas out on the run's grid", () => {
    const book = { "vega_node|SPX|80%@1y": { value: -2539, se: 198 }, "vega_node|SPX|90%@1m": { value: 364, se: 175 } };
    const g = nodeGrid(book, "SPX", { strike_labels: ["80%", "90%"], tenor_labels: ["1m", "1y"] });
    expect(g.cells).toEqual([
      [null, { value: -2539, se: 198 }],
      [{ value: 364, se: 175 }, null],
    ]);
  });
});
