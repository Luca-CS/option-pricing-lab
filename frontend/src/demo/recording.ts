// The recorded session the demo replays (docs/ux.md §5): for one profile, every answer of the API its
// screens asked for while the recorder (e2e/record) walked them on a desk with the demo's synthetic data, keyed by the
// request, and every job it ran as GET /jobs/{id} gave it once done. Shared by the recorder and the demo's transport, so
// both key a request the same way.

export type Answer = { status: number; body: unknown };

export type Recording = {
  profile: string;
  /** when it was recorded, ISO */
  recorded_at: string;
  /** the demo market's valuation date, ISO: the banner says it */
  data_date: string;
  me: unknown;
  /** requestKey -> the answer */
  answers: Record<string, Answer>;
  /** job id -> the job, done */
  jobs: Record<string, unknown>;
  /** the ids the recorder made, that the stories' steps open (`{quote}`, `{greeks}`...) */
  refs: Record<string, string>;
};

/** JSON with its object keys sorted, so that two equal bodies give one key whatever their order. */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/** A request's key: method, path and query as sent; a body that changes something is part of it, canonically. */
export function requestKey(method: string, url: string, body?: string | null): string {
  const u = new URL(url, "http://demo.invalid");
  const m = method.toUpperCase();
  const base = `${m} ${u.pathname}${u.search}`;
  if (m === "GET" || m === "HEAD" || !body) return base;
  try {
    return `${base} ${canonical(JSON.parse(body))}`;
  } catch {
    return `${base} ${body}`;
  }
}
