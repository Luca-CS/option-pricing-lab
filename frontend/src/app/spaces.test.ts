import { describe, expect, it } from "vitest";
import { homeOf, spaceOf, spacesFor } from "./spaces";

const ids = (xs: { id: string }[]) => xs.map((s) => s.id);

describe("spaces by role", () => {
  it("show a role's own space first, the others after, Admin to admins only", () => {
    const trader = spacesFor({ roles: ["trader"] });
    expect(ids(trader.own)).toEqual(["trading"]);
    expect(ids(trader.others)).toEqual(["structuring", "risk", "quant"]);
    const both = spacesFor({ roles: ["structurer", "quant"] });
    expect(ids(both.own)).toEqual(["structuring", "quant"]);
    expect(ids(spacesFor({ roles: ["viewer"] }).own)).toEqual([]);
    expect(ids(spacesFor({ roles: ["viewer"] }).others)).not.toContain("admin");
    expect(ids(spacesFor({ roles: ["admin"] }).own)).toEqual(["structuring", "trading", "risk", "quant", "admin"]);
  });

  it("land on the first role's home", () => {
    expect(homeOf({ roles: ["structurer"] })).toBe("/structuring");
    expect(homeOf({ roles: ["risk", "trader"] })).toBe("/risk");
    expect(homeOf({ roles: ["admin"] })).toBe("/admin");
    expect(homeOf({ roles: ["viewer"] })).toBe("/trading");
    expect(homeOf(null)).toBe("/trading");
  });

  it("know the space of a path", () => {
    expect(spaceOf("/trading/trades/DEMO-01")?.id).toBe("trading");
    expect(spaceOf("/")).toBeUndefined();
    expect(spaceOf("/jobs/abc")).toBeUndefined();
  });
});
