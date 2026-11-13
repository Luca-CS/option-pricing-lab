// A small client on the generated types: if this compiles, the contract is usable by the front (session 2).
// Generated types: `npm run generate` (schema.d.ts, not versioned). Compiled by CI: `npm run check`.
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Problem = components["schemas"]["Problem"];
type Job = components["schemas"]["Job"];
type Estimate = components["schemas"]["Estimate"];
type FixingsIn = components["schemas"]["FixingsIn"];

const api = createClient<paths>({ baseUrl: "http://127.0.0.1:8765", credentials: "same-origin" });

function fail(error: Problem): never {
  const fields = (error.errors ?? []).map((e) => `${e.field}: ${e.message}`).join("; ");
  throw new Error(fields ? `${error.detail} (${fields})` : error.detail);
}

export async function signIn(name: string, password: string): Promise<{ csrf: string; permissions: string[] }> {
  const { data, error } = await api.POST("/api/v1/auth/login", { body: { name, password } });
  if (error) fail(error);
  return { csrf: data.csrf_token, permissions: data.permissions };
}

export async function quickQuote(csrf: string, key: string): Promise<{ jobId: string; reused: boolean }> {
  // the same Idempotency-Key gives back the same job: a retried click never quotes twice
  const { data, error } = await api.POST("/api/v1/structuring/quotes", {
    body: { sheet: { type: "autocall", values: { coupon: { barrier: 0.7 } } }, options: { precision: "quick" } },
    params: { header: { "Idempotency-Key": key } },
    headers: { "X-CSRF-Token": csrf },
  });
  if (error) fail(error);
  return { jobId: data.job_id, reused: data.reused ?? false };
}

export async function job(
  jobId: string,
): Promise<{ state: Job["state"]; progress: number | null | undefined; market: string | null | undefined }> {
  const { data, error } = await api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } });
  if (error) fail(error);
  return { state: data.state, progress: data.progress, market: data.result?.market?.snapshot_id };
}

export function shown(e: Estimate): string {
  // every Monte Carlo number of a result: its value and its standard error
  return e.se === null ? `${e.value}` : `${e.value} ± ${2 * e.se}`;
}

export async function trades(cursor?: string): Promise<{ count: number; next: string | null | undefined }> {
  const { data, error } = await api.GET("/api/v1/trading/trades", {
    params: { query: cursor === undefined ? { limit: 50 } : { limit: 50, cursor } },
  });
  if (error) fail(error);
  return { count: data.items.length, next: data.next };
}

export async function addFixings(csrf: string, text: string): Promise<void> {
  const body: FixingsIn = { underlying: "SPX", text };
  const { error } = await api.POST("/api/v1/trading/fixings", {
    body,
    headers: { "X-CSRF-Token": csrf },
  });
  if (error) fail(error);
}

export function contractIsStrict(): void {
  // @ts-expect-error the API refuses fields it does not know (extra="forbid" gives additionalProperties: false).
  // openapi-fetch's generic `body` does not run TypeScript's excess-property check: type a body as below to get it.
  const sneaky: components["schemas"]["FixingsIn"] = { underlying: "SPX", sneaky: 1 };
  void sneaky;
  // @ts-expect-error a job's state is one of five words
  const state: Job["state"] = "finished";
  void state;
}
