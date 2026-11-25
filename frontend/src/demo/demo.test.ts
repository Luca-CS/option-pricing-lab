import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canonical, type Recording, requestKey } from "./recording";
import { STORIES, stepPath } from "./stories";
import { demoFetch, RUN_SECONDS, startProfile } from "./transport";

const job = { id: "J1", kind: "quote", state: "done", progress: 1, result: { result: { quote_id: "Q1" } } };

const recording: Recording = {
  profile: "structurer",
  recorded_at: "2026-11-25T00:00:00Z",
  data_date: "2021-06-30",
  me: { name: "structurer", roles: ["structurer"], permissions: ["create_quote"] },
  answers: {
    "GET /api/v1/market/context": { status: 200, body: { snapshot_id: "SPX-1" } },
    [requestKey("POST", "/api/v1/structuring/quotes", JSON.stringify({ sheet: { b: 2, a: 1 } }))]: {
      status: 202,
      body: { job_id: "J1" },
    },
  },
  jobs: { J1: job },
  refs: { quote: "Q1", preset: "phoenix" },
};

const at = (path: string, init?: RequestInit) => demoFetch(`http://demo.test${path}`, init);

describe("the demo's transport", () => {
  let now = 0;
  beforeEach(async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(recording), { status: 200 }));
    vi.spyOn(performance, "now").mockImplementation(() => now);
    await startProfile("structurer");
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keys a body by its content, whatever the order of its keys", () => {
    expect(canonical({ b: [1, { d: 1, c: 2 }], a: null })).toBe('{"a":null,"b":[1,{"c":2,"d":1}]}');
    expect(requestKey("post", "http://x/api/v1/q?x=1", '{"b":1,"a":2}')).toBe('POST /api/v1/q?x=1 {"a":2,"b":1}');
    expect(requestKey("GET", "http://x/api/v1/q?x=1", '{"a":1}')).toBe("GET /api/v1/q?x=1");
  });

  it("answers what was recorded, and says what it does not hold", async () => {
    expect(await (await at("/api/v1/market/context")).json()).toEqual({ snapshot_id: "SPX-1" });
    expect((await at("/api/v1/auth/me")).status).toBe(200);
    const missing = await at("/api/v1/trading/book");
    expect(missing.status).toBe(404);
    expect(((await missing.json()) as { title: string }).title).toBe("Not in the demo");
  });

  it("refuses a write it did not record: the demo is read-only", async () => {
    const r = await at("/api/v1/trading/trades", { method: "POST", body: JSON.stringify({ any: 1 }) });
    expect(r.status).toBe(403);
    expect(((await r.json()) as { title: string }).title).toBe("The demo is read-only");
  });

  it("replays a recorded job: running for a moment, then as the desk returned it", async () => {
    now = 1_000;
    const submitted = await at("/api/v1/structuring/quotes", {
      method: "POST",
      body: JSON.stringify({ sheet: { a: 1, b: 2 } }),
    });
    expect(submitted.status).toBe(202);
    now += 500;
    const running = (await (await at("/api/v1/jobs/J1")).json()) as { state: string; progress: number };
    expect(running.state).toBe("running");
    expect(running.progress).toBeGreaterThan(0);
    now += RUN_SECONDS * 1000;
    expect(await (await at("/api/v1/jobs/J1")).json()).toEqual(job);
  });

  it("ends the profile's session on sign-out", async () => {
    expect((await at("/api/v1/auth/logout", { method: "POST" })).status).toBe(204);
    expect((await at("/api/v1/auth/me")).status).toBe(401);
  });
});

describe("the stories", () => {
  it("put the recording's references in their paths", () => {
    const step = STORIES.find((s) => s.profile === "structurer")?.steps[1];
    expect(step && stepPath(step, { quote: "Q 1" })).toBe("/structuring/quotes/Q%201");
  });

  it("each have steps, and every reference a step names is one the recorder writes", () => {
    const written = {
      structurer: ["preset", "quote", "trade"],
      trader: ["greeks", "name", "node", "strike", "tenor", "pnl_from", "pnl_to", "pnl_day", "var"],
      quant: ["replay"],
    };
    for (const story of STORIES) {
      expect(story.steps.length).toBeGreaterThan(3);
      const named = story.steps.flatMap((s) => [...s.to.matchAll(/\{([a-z_]+)\}/g)].map((m) => m[1]));
      for (const ref of named) expect(written[story.profile]).toContain(ref);
    }
  });
});
