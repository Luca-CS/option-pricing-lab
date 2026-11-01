// JSON API of the desk, and background jobs (quotes and book runs take seconds).

const TOKEN = new URLSearchParams(location.search).get("token");

export class ApiError extends Error {
  constructor(message, status, field) {
    super(message);
    this.status = status;
    this.field = field;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) headers["X-Optlab-Token"] = TOKEN;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new ApiError("The desk server does not answer: is python -m optlab.app still running?", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.error || `HTTP ${res.status}`, res.status, data.field);
  return data;
}

// Poll a job until it ends; onTick(job) is called on every poll (state, elapsed).
export async function runJob(jobId, onTick, signal) {
  window.dispatchEvent(new Event("optlab:jobs"));
  try {
    return await poll(jobId, onTick, signal);
  } finally {
    window.dispatchEvent(new Event("optlab:jobs"));
  }
}

async function poll(jobId, onTick, signal) {
  let delay = 300;
  for (;;) {
    if (signal && signal.aborted) {
      await api(`/api/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
      throw new ApiError("Cancelled", 499);
    }
    const job = await api(`/api/jobs/${jobId}`);
    if (onTick) onTick(job);
    if (job.state === "done") return job.result;
    if (job.state === "error") throw new ApiError(job.error ? job.error.message : "The job failed", 500);
    if (job.state === "cancelled") throw new ApiError("Cancelled", 499);
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 1.3, 1000);
  }
}

export function withToken(url) {
  return TOKEN ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}` : url;
}
