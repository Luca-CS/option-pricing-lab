// The demo's stand-in for the desk (build mode `demo`, GitHub Pages): the API client's fetch answers from the recorded
// session of the profile the visitor picked, and a job's event stream replays the recorded job over a couple of
// seconds, so a "Quote" or a "Fit" of the story runs as it does on the desk. Nothing leaves the browser: a request the
// recording does not hold is refused, read-only, with what it would have done.
import { type Recording, requestKey } from "./recording";

export const PROFILES = ["structurer", "trader", "quant"] as const;
export type Profile = (typeof PROFILES)[number];

const PROFILE_KEY = "optlab-demo-profile";
/** how long a replayed job runs, seconds: long enough to see its progress, short enough to keep the story moving */
export const RUN_SECONDS = 2.4;

let recording: Recording | null = null;
let loading: Promise<Recording | null> | null = null;
const started = new Map<string, number>();

function storedProfile(): string | null {
  try {
    return sessionStorage.getItem(PROFILE_KEY);
  } catch {
    return null;
  }
}

function rememberProfile(profile: string | null): void {
  try {
    if (profile) sessionStorage.setItem(PROFILE_KEY, profile);
    else sessionStorage.removeItem(PROFILE_KEY);
  } catch {
    // a browser that keeps nothing: the profile lasts until the page reloads
  }
}

async function load(profile: string): Promise<Recording | null> {
  const r = await globalThis.fetch(`${import.meta.env.BASE_URL}demo/${encodeURIComponent(profile)}.json`);
  return r.ok ? ((await r.json()) as Recording) : null;
}

async function current(): Promise<Recording | null> {
  if (recording) return recording;
  const profile = storedProfile();
  if (!profile) return null;
  loading ??= load(profile);
  recording = await loading;
  return recording;
}

/** The visitor picks a profile: its recording is read, and its session starts. */
export async function startProfile(profile: Profile): Promise<Recording | null> {
  rememberProfile(profile);
  started.clear();
  loading = load(profile);
  recording = await loading;
  return recording;
}

/** The recording in use, once read (the banner's date). */
export function currentRecording(): Recording | null {
  return recording;
}

export function currentProfile(): Profile | null {
  const p = storedProfile();
  return PROFILES.find((x) => x === p) ?? null;
}

function endProfile(): void {
  rememberProfile(null);
  recording = null;
  loading = null;
  started.clear();
}

function answer(status: number, body: unknown): Response {
  if (status === 204) return new Response(null, { status });
  const type = status >= 400 ? "application/problem+json" : "application/json";
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": type } });
}

function problem(status: number, title: string, detail: string): Response {
  return answer(status, { type: "about:blank", title, status, detail });
}

/** A recorded job as it stands now: running while its replay lasts, then as the desk returned it. */
function jobNow(id: string, job: Record<string, unknown>): Record<string, unknown> {
  const t0 = started.get(id);
  const elapsed = t0 === undefined ? Number.POSITIVE_INFINITY : (performance.now() - t0) / 1000;
  if (elapsed >= RUN_SECONDS) return job;
  return {
    ...job,
    state: "running",
    progress: Math.min(0.95, elapsed / RUN_SECONDS),
    step: "replaying the recorded run",
    result: null,
    error: null,
    finished_at: null,
  };
}

export async function demoFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(input, init);
  const url = new URL(request.url);
  const method = request.method.toUpperCase();
  const path = url.pathname;
  if (method === "POST" && path === "/api/v1/auth/logout") {
    endProfile();
    return answer(204, null);
  }
  const rec = await current();
  if (!rec) return problem(401, "No profile", "Pick a profile on the demo's first page.");
  if (method === "GET" && path === "/api/v1/auth/me") return answer(200, rec.me);
  if (method === "GET" && path === "/api/v1/jobs") return answer(200, { jobs: [] });
  const job = method === "GET" ? /^\/api\/v1\/jobs\/([^/]+)$/.exec(path) : null;
  if (job) {
    const id = decodeURIComponent(job[1] ?? "");
    const recorded = rec.jobs[id] as Record<string, unknown> | undefined;
    return recorded
      ? answer(200, jobNow(id, recorded))
      : problem(404, "Not in the demo", "The recorded session holds no such job.");
  }
  const body = method === "GET" || method === "HEAD" ? null : await request.clone().text();
  const found = rec.answers[requestKey(method, url.href, body)];
  if (found) {
    const submitted = found.status === 202 ? (found.body as { job_id?: string }).job_id : undefined;
    if (submitted) started.set(submitted, performance.now());
    return answer(found.status, found.body);
  }
  if (method !== "GET" && method !== "HEAD") {
    return problem(
      403,
      "The demo is read-only",
      "The demo is read-only: on the desk this would run now, but the demo replays a recorded session and holds only its story's own requests.",
    );
  }
  return problem(404, "Not in the demo", "The recorded session did not open this data.");
}

type Listener = (ev: MessageEvent<string>) => void;

/** A job's event stream in the demo: running, its progress, then its end as recorded (EventSource's surface). */
class DemoEvents {
  static readonly CLOSED = 2;
  readyState = 0;
  onerror: ((ev: Event) => void) | null = null;
  private readonly listeners = new Map<string, Listener[]>();
  private readonly timers: ReturnType<typeof setTimeout>[] = [];

  constructor(url: string) {
    const id = decodeURIComponent(/\/jobs\/([^/]+)\/events/.exec(url)?.[1] ?? "");
    void current().then((rec) => this.replay(id, rec?.jobs[id] as Record<string, unknown> | undefined));
  }

  private emit(type: string, data: unknown): void {
    const ev = new MessageEvent<string>(type, { data: JSON.stringify(data) });
    for (const fn of this.listeners.get(type) ?? []) fn(ev);
  }

  private replay(id: string, job: Record<string, unknown> | undefined): void {
    if (this.readyState === DemoEvents.CLOSED) return;
    if (!job) {
      this.readyState = DemoEvents.CLOSED;
      this.onerror?.(new Event("error"));
      return;
    }
    this.readyState = 1;
    const t0 = started.get(id);
    const left = t0 === undefined ? 0 : Math.max(0, RUN_SECONDS * 1000 - (performance.now() - t0));
    const ticks = Math.floor(left / 400);
    for (let i = 0; i < ticks; i++) {
      this.timers.push(setTimeout(() => this.emit(i === 0 ? "state" : "progress", jobNow(id, job)), i * 400));
    }
    this.timers.push(setTimeout(() => this.emit(String(job.state ?? "done"), job), left));
  }

  addEventListener(type: string, fn: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }

  close(): void {
    this.readyState = DemoEvents.CLOSED;
    for (const t of this.timers) clearTimeout(t);
  }
}

/** The event stream of a job: the desk's, or the demo's replay of it. */
export function openEvents(url: string): EventSource {
  return import.meta.env.MODE === "demo" ? (new DemoEvents(url) as unknown as EventSource) : new EventSource(url);
}
