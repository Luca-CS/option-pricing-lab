// Compile-time checks of the contract, run by `npm run typecheck` (was api/client-check): if this compiles, the
// generated types still say what the front relies on. Nothing here is imported by the app.
import type { components } from "./schema";

type Schemas = components["schemas"];

export function contractIsStrict(): void {
  // @ts-expect-error the API refuses fields it does not know (extra="forbid" gives additionalProperties: false).
  // openapi-fetch's generic `body` does not run TypeScript's excess-property check: type a body as below to get it.
  const sneaky: Schemas["FixingsIn"] = { underlying: "SPX", sneaky: 1 };
  void sneaky;
  // @ts-expect-error a job's state is one of five words
  const state: Schemas["Job"]["state"] = "finished";
  void state;
  // every Monte Carlo number is {value, se}, se null where the library has no error
  const estimate: Schemas["Estimate"] = { value: 1, se: null };
  void estimate;
  // a request body may leave out what has a default (--default-non-nullable false)
  const quote: Schemas["QuoteIn"] = { sheet: { type: "autocall", values: { coupon: { barrier: 0.7 } } } };
  void quote;
}
