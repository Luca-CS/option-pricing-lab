// The one client of API v1: types generated from api/openapi.json (`npm run generate`, never written by hand), the
// session cookie sent on the same origin, the CSRF token added to every request that changes something
// (docs/architecture/api.md §2), and errors raised as ApiError with the problem+json of the server (§3).
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Problem = Schemas["Problem"];
export type Me = Schemas["Me"];

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);
let csrf: string | null = null;

/** The session's CSRF token, from the login or from GET /auth/me after a reload. */
export function setCsrf(token: string | null): void {
  csrf = token;
}

export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (!SAFE.has(request.method.toUpperCase()) && csrf !== null) {
      request.headers.set("X-CSRF-Token", csrf);
    }
    return request;
  },
};

export function makeClient(baseUrl: string, fetchImpl?: typeof fetch) {
  const client = createClient<paths>({
    baseUrl,
    credentials: "same-origin",
    ...(fetchImpl ? { fetch: fetchImpl } : {}),
  });
  client.use(csrfMiddleware);
  return client;
}

export const api = makeClient(globalThis.location?.origin ?? "");

/** An answer of the API that is not a success: its status and its problem+json. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;

  constructor(status: number, problem: Problem) {
    super(problem.detail || problem.title || `error ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }

  /** The message of each field at fault, by its dotted path (`coupon.barrier`), to show under the field. */
  fields(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of this.problem.errors ?? []) {
      out[e.field] = out[e.field] ? `${out[e.field]}; ${e.message}` : e.message;
    }
    return out;
  }
}

function asProblem(status: number, error: unknown): Problem {
  if (error !== null && typeof error === "object" && "status" in error && "title" in error) {
    return error as Problem;
  }
  return { type: "about:blank", title: "Error", status, detail: typeof error === "string" ? error : "" };
}

/** The data of an answer, or an ApiError: `const me = await ok(api.GET("/api/v1/auth/me"))`. */
export async function ok<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || !response.ok) {
    throw new ApiError(response.status, asProblem(response.status, error));
  }
  return data as T;
}
