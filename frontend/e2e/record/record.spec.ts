// The recorder of the demo's sessions (`npm run demo:record`, playwright.record.config.ts): on a desk serving a fresh
// demo data folder (synthetic numbers only), it runs what the stories show (a quote, a booking, the book's Greeks, a
// three-week backfill, a VaR, a fit, a replay), then signs in as each profile and walks its story and every other screen,
// keeping each answer of the API as the front asked for it. demo/recorded/<profile>.json is what the demo replays.
// Never on a real desk: the files are published. The login is never recorded, a session token is dropped, and a file
// that would hold a local path is refused.
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { type APIRequestContext, type Browser, expect, type Page, request, test } from "@playwright/test";
import { type Recording, requestKey } from "../../src/demo/recording";
import { password, user } from "../fixtures";

const OUT = path.resolve(import.meta.dirname, "../../demo/recorded");
const PROFILES = ["structurer", "trader", "quant"] as const;
type Profile = (typeof PROFILES)[number];
const secretOf = (p: Profile) => `${password}-${p}-demo`; // derived from the demo folder's password, written nowhere

// every screen but Admin's (no demo profile holds it), so the visitor may wander off the story
const SCREENS = [
  "/structuring",
  "/structuring/builder",
  "/structuring/quotes",
  "/structuring/compare",
  "/trading",
  "/trading/book",
  "/trading/hedging",
  "/trading/risk",
  "/trading/pnl",
  "/trading/lifecycle",
  "/trading/costs",
  "/risk",
  "/risk/var",
  "/risk/stress",
  "/risk/backtest",
  "/risk/runs",
  "/quant",
  "/quant/market",
  "/quant/calibration",
  "/quant/vollab",
  "/quant/replay",
  "/quant/script",
  "/quant/engine",
];

const PRIVATE = /[A-Za-z]:[\\/]|\\\\|\/home\/|\/Users\/|AppData|\.sqlite\b|optlab-data/;

/** The strings of a value that would tell where the desk runs: a recording holding one is not written. */
function privateStrings(value: unknown, at = "", out: string[] = []): string[] {
  if (typeof value === "string" && PRIVATE.test(value)) out.push(`${at}: ${value.slice(0, 120)}`);
  else if (Array.isArray(value)) {
    for (const [i, v] of value.entries()) privateStrings(v, `${at}[${i}]`, out);
  } else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) privateStrings(v, `${at}.${k}`, out);
  }
  return out;
}

// fields the desk fills with a local path, which it already leaves out when it is shared (Desk.shared): the recorder
// does the same; any other path refuses the file
const SHARED_NULLS: Record<string, string[]> = { "/api/v1/market/lake/coverage": ["lake"] };

function asShared(pathname: string, body: unknown): unknown {
  const fields = SHARED_NULLS[pathname];
  if (!fields || !body || typeof body !== "object" || Array.isArray(body)) return body;
  return { ...(body as Record<string, unknown>), ...Object.fromEntries(fields.map((f) => [f, null])) };
}

// fields the front never reads that a secret scanner takes for keys: a risk run's `run_key` is the hash of what
// determines it (optlab.risk.store.run_key), not a secret, but gitleaks' generic-api-key rule reads it as one
const DROPPED = new Set(["run_key"]);

function withoutDropped(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(withoutDropped);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([k]) => !DROPPED.has(k))
        .map(([k, v]) => [k, withoutDropped(v)]),
    );
  }
  return value;
}

function withoutToken(body: unknown): unknown {
  if (!body || typeof body !== "object" || Array.isArray(body)) return body;
  const { csrf_token: _, ...rest } = body as Record<string, unknown>;
  return rest;
}

type Live = { rec: Recording; pending: Promise<void>[] };

function listen(page: Page, profile: Profile): Live {
  const live: Live = {
    rec: { profile, recorded_at: new Date().toISOString(), data_date: "", me: null, answers: {}, jobs: {}, refs: {} },
    pending: [],
  };
  page.on("response", (res) => {
    const url = new URL(res.url());
    const p = url.pathname;
    if (!p.startsWith("/api/v1/") || p.startsWith("/api/v1/auth/") || p.endsWith("/events")) return;
    const req = res.request();
    live.pending.push(
      (async () => {
        if (res.status() !== 204 && !(res.headers()["content-type"] ?? "").includes("json")) return;
        let body: unknown = null;
        try {
          body = res.status() === 204 ? null : await res.json();
        } catch {
          return; // a page left before its answer was read
        }
        live.rec.answers[requestKey(req.method(), res.url(), req.postData())] = {
          status: res.status(),
          body: asShared(p, body),
        };
      })(),
    );
  });
  return live;
}

async function settle(page: Page, live: Live): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(400); // the charts' chunk and the queries it starts
  await page.waitForLoadState("networkidle");
  await Promise.all(live.pending);
}

async function visit(page: Page, live: Live, to: string): Promise<void> {
  await page.goto(to);
  await settle(page, live);
}

async function signInPage(page: Page, name: string, secret: string): Promise<void> {
  await page.goto("/sign-in");
  await page.getByRole("textbox", { name: "Name", exact: true }).fill(name);
  await page.getByRole("textbox", { name: "Password", exact: true }).fill(secret);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/sign-in/);
}

async function api(baseURL: string, name: string, secret: string): Promise<{ ctx: APIRequestContext; csrf: string }> {
  const ctx = await request.newContext({ baseURL });
  const login = await ctx.post("/api/v1/auth/login", { data: { name, password: secret } });
  expect(login.status()).toBe(200);
  return { ctx, csrf: ((await login.json()) as { csrf_token: string }).csrf_token };
}

async function submit(a: { ctx: APIRequestContext; csrf: string }, to: string, body: unknown): Promise<string> {
  const r = await a.ctx.post(to, { data: body, headers: { "X-CSRF-Token": a.csrf } });
  expect(r.status(), `${to}: ${await r.text()}`).toBe(202);
  return ((await r.json()) as { job_id: string }).job_id;
}

async function done(ctx: APIRequestContext, id: string, minutes = 60): Promise<Record<string, unknown>> {
  const until = Date.now() + minutes * 60_000;
  for (;;) {
    const job = (await (await ctx.get(`/api/v1/jobs/${id}`)).json()) as Record<string, unknown>;
    if (job.state === "done") return job;
    expect(["queued", "running"], `job ${id}: ${JSON.stringify(job.error)}`).toContain(job.state);
    expect(Date.now()).toBeLessThan(until);
    await new Promise((r) => setTimeout(r, 2_000));
  }
}

/** The recorded jobs of a profile: every job its pages submitted, as GET /jobs/{id} gives it once done. */
async function closeJobs(page: Page, live: Live): Promise<void> {
  for (const a of Object.values(live.rec.answers)) {
    const id = a.status === 202 ? (a.body as { job_id?: string }).job_id : undefined;
    if (id) live.rec.jobs[id] = await done(page.request, id);
  }
}

function write(live: Live): void {
  live.rec = withoutDropped(live.rec) as Recording;
  const leaks = privateStrings(live.rec);
  expect(leaks, "a recorded answer tells where the desk runs").toEqual([]);
  mkdirSync(OUT, { recursive: true });
  writeFileSync(path.join(OUT, `${live.rec.profile}.json`), JSON.stringify(live.rec));
}

async function begin(page: Page, profile: Profile): Promise<Live> {
  const live = listen(page, profile);
  await signInPage(page, profile, secretOf(profile));
  live.rec.me = withoutToken(await (await page.request.get("/api/v1/auth/me")).json());
  const context = (await (await page.request.get("/api/v1/market/context")).json()) as { valuation?: string };
  live.rec.data_date = context.valuation ?? "";
  return live;
}

async function wander(page: Page, live: Live, extra: string[] = []): Promise<void> {
  for (const to of [...SCREENS, ...extra]) await visit(page, live, to);
}

// RECORD_ONLY=quant (a comma list) records those profiles again on the same folder, keeping the others' files
const ONLY = new Set((process.env.RECORD_ONLY ?? PROFILES.join(",")).split(",").map((x) => x.trim()));

test("record the demo's three sessions", async ({ page, browser, baseURL }) => {
  test.setTimeout(3 * 60 * 60_000);
  const base = baseURL ?? "";
  const admin = await api(base, user, password);
  const e = encodeURIComponent;
  for (const p of PROFILES) {
    const r = await admin.ctx.post("/api/v1/admin/users", {
      data: { name: p, password: secretOf(p), roles: [p] },
      headers: { "X-CSRF-Token": admin.csrf },
    });
    expect([200, 201, 409]).toContain(r.status());
  }
  if (ONLY.has("structurer") || ONLY.has("trader")) await structurerAndTrader(page, browser, admin, base);
  if (ONLY.has("quant")) await quant(browser, e);
  await admin.ctx.dispose();
});

type Admin = { ctx: APIRequestContext; csrf: string };

async function structurerAndTrader(page: Page, browser: Browser, admin: Admin, base: string): Promise<void> {
  const e = encodeURIComponent;
  // what the trader's story reads, computed first (three weeks of EOD runs and a VaR, the book's Greeks)
  const greeksJob = await submit(admin, "/api/v1/risk/greeks", { options: { precision: "quick" } });
  const greeks = await done(admin.ctx, greeksJob);
  const eod = await submit(admin, "/api/v1/risk/eod-runs", {
    from: "2020-03-02",
    to: "2020-03-20",
    options: { precision: "quick" },
  });
  await done(admin.ctx, eod, 120);
  const varJob = await submit(admin, "/api/v1/risk/var", {
    days: 250,
    stressed: false,
    ten_day: true,
    options: { precision: "quick" },
  });
  const varRun = await done(admin.ctx, varJob);
  // the optimizer on the book's delta and vega per tenor (OTC vanillas and futures), with its grid and VaR control,
  // run by the trader: a job is read by its owner only, and the trader's page follows it
  const traderApi = await api(base, "trader", secretOf("trader"));
  const hedgeJob = await submit(traderApi, "/api/v1/trading/hedges/optimizations", {
    options: { precision: "quick" },
    families: ["delta", "vega_tenor"],
    universe: { rates: false, dividends: false, funding: false },
    var_days: 250,
  });
  await done(traderApi.ctx, hedgeJob, 120);
  await traderApi.ctx.dispose();

  // the structurer: a Phoenix quoted in the builder, its page, its model risk, the quotes
  const s = await begin(page, "structurer");
  await visit(page, s, "/structuring/builder?preset=phoenix");
  await page.getByRole("button", { name: "Quote", exact: true }).click();
  await expect(page).toHaveURL(/job=/);
  const quoteJob = new URL(page.url()).searchParams.get("job") ?? "";
  const quoted = await done(page.request, quoteJob);
  await settle(page, s);
  const quote = String(((quoted.result as Record<string, unknown>).result as { quote_id: string }).quote_id);
  s.rec.refs = { preset: "phoenix", quote };

  // the trader books it from the quote (a context of its own: the structurer may not book)
  const traderContext = await browser.newContext();
  const traderPage = await traderContext.newPage();
  const t = await begin(traderPage, "trader");
  await visit(traderPage, t, `/structuring/quotes/${encodeURIComponent(quote)}?book=1`);
  await traderPage.getByRole("dialog", { name: "Book this quote" }).getByRole("button", { name: "Book" }).click();
  await expect(traderPage).toHaveURL(/\/trading\/trades\//);
  const trade = decodeURIComponent(new URL(traderPage.url()).pathname.split("/").at(-1) ?? "");
  await settle(traderPage, t);
  s.rec.refs.trade = trade;
  for (const to of [
    `/structuring/quotes/${encodeURIComponent(quote)}`,
    `/quant/calibration?quote=${encodeURIComponent(quote)}`,
    `/trading/trades/${encodeURIComponent(trade)}`,
    `/structuring/compare?quotes=${encodeURIComponent(quote)}`,
  ]) {
    await visit(page, s, to);
  }
  await wander(page, s);
  await closeJobs(page, s);
  write(s);

  // the trader: the book's Greeks down to its largest node vega, the day that moved the book most, the VaR and its day
  const greeksRun = String((greeks.result as { result?: { run_id?: string } }).result?.run_id ?? "");
  const book = ((greeks.result as { result: { book: Record<string, { value: number }> } }).result.book ?? {}) as Record<
    string,
    { value: number }
  >;
  const nodes = Object.entries(book).filter(([k]) => k.startsWith("vega_node|"));
  nodes.sort(([, a], [, b]) => Math.abs(b.value) - Math.abs(a.value));
  const [, name = "SPX", at = "100%@1y"] = (nodes[0]?.[0] ?? "").split("|");
  const [strike = "100%", tenor = "1y"] = at.split("@");
  const pnl = (await (await traderPage.request.get("/api/v1/risk/pnl?from=2020-03-02&to=2020-03-20")).json()) as {
    daily: { from: string; to: string; total: number }[];
  };
  const worstDay = [...pnl.daily].sort((a, b) => Math.abs(b.total) - Math.abs(a.total))[0];
  const worst = worstDay?.to ?? "2020-03-16";
  // that day's vol move opened node by node, as its panel asks (the panel reads the stored opening back)
  if (worstDay) {
    const opened = await submit(admin, "/api/v1/risk/pnl/detail", {
      from: worstDay.from,
      to: worstDay.to,
      options: { precision: "quick" },
    });
    await done(admin.ctx, opened, 120);
  }
  const varId = String((varRun.result as { result?: { run_id?: string } }).result?.run_id ?? "");
  t.rec.refs = {
    greeks: greeksRun,
    name,
    node: at,
    strike,
    tenor,
    pnl_from: "2020-03-02",
    pnl_to: "2020-03-20",
    pnl_day: worst,
    var: varId,
    hedge: hedgeJob,
  };
  const r = t.rec.refs;
  for (const to of [
    `/trading/risk?run=${e(r.greeks ?? "")}`,
    `/trading/risk?run=${e(r.greeks ?? "")}&view=vega&name=${e(name)}&node=${e(at)}`,
    `/quant/market/${e(name)}?strike=${e(strike)}&tenor=${e(tenor)}`,
    `/trading/pnl?from=2020-03-02&to=2020-03-20&day=${worst}`,
    `/risk/var/${e(varId)}`,
    "/trading/hedging",
    "/trading/hedging?view=ticket",
    `/trading/hedging?view=optimizer&hedgeJob=${e(hedgeJob)}`,
  ]) {
    await visit(traderPage, t, to);
  }
  // the book valued as its button does (quick): the demo's "Value the book", and the home's "Revalue the book" (the
  // same request), replay it
  await visit(traderPage, t, "/trading/book");
  await traderPage.getByRole("button", { name: "Value the book" }).click();
  await expect(traderPage.getByRole("heading", { name: "Simulations" })).toBeVisible({ timeout: 600_000 });
  await settle(traderPage, t);
  const trades = (await (await traderPage.request.get("/api/v1/trading/trades?limit=200")).json()) as {
    items: { trade_id: string }[];
  };
  await wander(
    traderPage,
    t,
    trades.items.flatMap((x) => [`/trading/trades/${e(x.trade_id)}`, `/trading/lifecycle/${e(x.trade_id)}`]),
  );
  await closeJobs(traderPage, t);
  write(t);
  await traderContext.close();
}

async function quant(browser: Browser, e: (x: string) => string): Promise<void> {
  // the quant: the vol lab's fit, a replay, the calibration, the script lab, the engine
  const quantContext = await browser.newContext();
  const quantPage = await quantContext.newPage();
  const q = await begin(quantPage, "quant");
  await visit(quantPage, q, "/quant/vollab");
  await quantPage.getByRole("button", { name: "Fit the models" }).click();
  await expect(quantPage.getByRole("table", { name: "Each model's error against the quotes" })).toBeVisible({
    timeout: 600_000,
  });
  await settle(quantPage, q);
  await visit(quantPage, q, "/quant/replay");
  await quantPage.getByLabel("Strike date").fill("2020-01-02");
  await quantPage.getByRole("textbox", { name: "Every, business days" }).fill("5");
  await quantPage.getByRole("textbox", { name: "Paths" }).fill("10000");
  await quantPage.getByRole("button", { name: "Replay", exact: true }).click();
  await expect(quantPage.getByRole("heading", { name: "The P&L explained" })).toBeVisible({ timeout: 1_800_000 });
  await settle(quantPage, q);
  const replays = (await (await quantPage.request.get("/api/v1/quant/replay/runs")).json()) as {
    runs?: { id: string }[];
  };
  const replay = replays.runs?.find((x) => x.id.includes("2020-01-02"))?.id ?? replays.runs?.[0]?.id ?? "";
  q.rec.refs = { replay };
  await visit(quantPage, q, `/quant/replay/${e(replay)}`);
  await wander(quantPage, q, ["/quant/market/SPX"]);
  await closeJobs(quantPage, q);
  write(q);
  await quantContext.close();
}
