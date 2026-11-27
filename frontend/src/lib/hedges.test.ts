import { describe, expect, it } from "vitest";
import { initialValues, instrumentBody, missing } from "./hedges";

const FIELDS = [
  { name: "name", kind: "choice", required: true },
  { name: "expiry", kind: "date", required: true },
  { name: "price", kind: "number", required: true },
  { name: "fixed_months", kind: "int", required: false, default: 12 },
  { name: "listed", kind: "bool", required: false, default: false },
];

describe("a hedging ticket", () => {
  it("reads each field by its kind and leaves the blanks to the server", () => {
    const body = instrumentBody("future", FIELDS, {
      name: "SPX",
      expiry: "2027-03-19",
      price: "6,012.5",
      fixed_months: "6.2",
      listed: "",
    });
    expect(body).toEqual({ type: "future", name: "SPX", expiry: "2027-03-19", price: 6012.5, fixed_months: 6 });
  });
  it("says which required fields are missing", () => {
    expect(missing(FIELDS, { name: "SPX", price: 1 })).toEqual(["expiry"]);
  });
  it("starts from the description's defaults", () => {
    expect(initialValues(FIELDS)).toEqual({ fixed_months: 12, listed: false });
  });
});
