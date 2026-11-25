import { describe, expect, it } from "vitest";
import { waterfallBars } from "./charts";

// each bar spans base..base+part on its side of zero; the four lists must rebuild every span
function spans(b: ReturnType<typeof waterfallBars>) {
  return b.up.map((_, i) => {
    const up = [b.upBase[i] ?? 0, (b.upBase[i] ?? 0) + (b.up[i] ?? 0)];
    const down = [(b.downBase[i] ?? 0) + (b.down[i] ?? 0), b.downBase[i] ?? 0];
    const parts = [up, down].filter(([x, y]) => x !== y);
    return parts.length ? [Math.min(...parts.flat()), Math.max(...parts.flat())] : [0, 0];
  });
}

describe("a waterfall's bars", () => {
  it("float above zero when every level is positive (a coupon from the base to the client)", () => {
    const b = waterfallBars(492, [-113, -63], 316);
    expect(spans(b)).toEqual([
      [0, 492],
      [379, 492],
      [316, 379],
      [0, 316],
    ]);
    expect(b.down.every((v) => v === 0)).toBe(true);
  });

  it("go below zero for a loss, and split where they cross it", () => {
    // a P&L: start at 0, a spot loss, a vol gain that crosses back above zero, a total
    const b = waterfallBars(0, [-200, 250], 50);
    expect(spans(b)).toEqual([
      [0, 0],
      [-200, 0],
      [-200, 50],
      [0, 50],
    ]);
    expect(b.down[1]).toBe(-200);
    expect(b.up[1]).toBe(0);
    expect(b.down[2]).toBe(-200);
    expect(b.up[2]).toBe(50);
  });

  it("stay below zero when everything is a loss", () => {
    const b = waterfallBars(0, [-100, -50], -150);
    expect(spans(b)).toEqual([
      [0, 0],
      [-100, 0],
      [-150, -100],
      [-150, 0],
    ]);
    expect(b.downBase[2]).toBe(-100);
    expect(b.down[2]).toBe(-50);
  });
});
