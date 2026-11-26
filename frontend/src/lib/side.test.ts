import { describe, expect, it } from "vitest";
import { pricedSide, sideOf, signedQuantity } from "./side";

describe("the desk's side", () => {
  it("signs the size by the side", () => {
    expect(signedQuantity("sold", 10)).toBe(-10);
    expect(signedQuantity("bought", 10)).toBe(10);
    expect(signedQuantity("sold", -3)).toBe(-3);
  });
  it("reads a signed quantity back", () => {
    expect(sideOf(-2.5)).toEqual({ side: "sold", size: 2.5 });
    expect(sideOf(4)).toEqual({ side: "bought", size: 4 });
  });
  it("defaults to the side the sheet was priced for", () => {
    expect(pricedSide({ costs: { side: "buy" } })).toBe("bought");
    expect(pricedSide({ costs: { side: "sell" } })).toBe("sold");
    expect(pricedSide({})).toBe("sold");
  });
});
