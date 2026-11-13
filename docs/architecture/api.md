# API v1: conventions

The contract session 2 builds the front on (handoff §7, ADR-0005, ADR-0020). The routes and their bodies are in
[`api/openapi.json`](../../api/openapi.json); this page says what the document cannot: how to sign in, what an error,
a job and a page look like, the units, the bounds, and how the front is served. Every function of v0 and its route
are in [`feature-inventory.md`](feature-inventory.md); roles in [`security.md`](security.md).

## 1. The document

- Base path `/api/v1`. The live document is `GET /api/v1/openapi.json` (public, like `health` and `ready`); the versioned
  copy is `api/openapi.json`, written by `uv run optlab api export`.
  `packages/server/tests/test_openapi.py` fails when the copy drifts from the routes.
- `operationId` is the route's function name (`create_trade`, `get_quote`), unique. Tags are the domains: `structuring`,
  `trading`, `market`, `risk`, `quant`, `admin`; the system routes (health, auth, jobs) have none.
- Every operation carries `x-roles`: the roles that may call it (`public`, or `viewer` for any signed-in user, or a list
  such as `["risk", "trader"]`). `admin` may call everything. `GET /auth/me` returns the caller's `permissions`: the
  operation ids it may call, read from the same document, so the front hides what the server would refuse.
- `securitySchemes`: `session` (the cookie) on every non-public operation, plus `csrf` (the header) on every one that
  is not a `GET`. Every operation that answers 202 (it submits a job) takes the `Idempotency-Key` header (§7).
- The interactive page `GET /api/v1/docs` is off by default (`OPTLAB_DOCS=true` turns it on): Swagger UI runs a
  third-party script on the desk's origin, pinned (5.34.0) and checked by its integrity, under a CSP that allows only
  that script and the page's own init (ADR-0021). Sign in with `POST /auth/login` (Try it out), then paste its
  `csrf_token` in Authorize.

## 2. Signing in

```
POST /api/v1/auth/login   {"name": "...", "password": "..."}
-> 200 {"user_id", "name", "roles": [...], "permissions": [...], "csrf_token": "..."}
   Set-Cookie: optlab_session=...; HttpOnly; SameSite=Strict; Path=/   (Secure under TLS)
```

- Every later request sends the cookie (same origin, `credentials: "same-origin"`). Every request that is not a `GET`
  also sends `X-CSRF-Token: <csrf_token>`; `GET /auth/me` gives it again after a reload.
- A session ends after `session_idle_minutes` without use (480) or `session_absolute_hours` after the login (168), at
  logout (`POST /auth/logout`, `{"everywhere": true}` for every browser), and when an admin changes the user's roles,
  password or state. A new login always opens a new session.
- Wrong name or wrong password give the same 401. After `login_max_failures` (5) the account is locked for
  `login_lockout_minutes` (15): 423. After `login_per_address` (20) failed logins in 10 minutes from one address: 429.
- Accounts are created on the machine (`uv run optlab users create <name> --roles admin`, the password
  asked twice, never an argument) or by an admin (`POST /admin/users`). Passwords are 12 characters at least.

## 3. Errors

Every error is `application/problem+json` (RFC 9457), never a stack trace:

```json
{
  "type": "about:blank",
  "title": "Invalid input",
  "status": 422,
  "detail": "the request is not valid: see errors",
  "errors": [{"field": "options.n_paths", "message": "Input should be less than or equal to 2000000"}]
}
```

- `errors[].field` is the dotted path of the field in the body or the query (`coupon.barrier`, `options.n_paths`,
  `day`). A form shows each message under its field.
- `POST /structuring/termsheet/form` answers 422 when a field of the sheet is wrong, and the problem carries the
  members `type`, `form`, `bounds` and `costs` of the sheet without that field, so the form stays on screen with the
  field to correct.
- Statuses: 400 (bad cursor), 401 (no session), 403 (role, CSRF, cross-site origin, a host other than this machine),
  404 (unknown id, or a job of someone else), 409 (a user that exists), 413 (body over `max_body_bytes`, 2 MB), 415
  (a body that is not JSON), 422 (validation, and a request the library refuses), 423 and 429 (login), 500 (logged on
  the server; the body says nothing more).

## 4. Requests

- JSON bodies only (`Content-Type: application/json`). Unknown fields are refused (422): every body model forbids
  extra keys, and the document says so (`additionalProperties: false`).
- A request that changes something and carries an `Origin` must come from the same origin (`null` is refused). The
  `Host` must be this machine (`127.0.0.1`, `localhost`, `[::1]`) unless the LAN profile is on.
- Bounds on everything that costs: `n_paths` at most 2,000,000 for quotes, valuations and risk (500,000 for the script
  lab, 200,000 for a replay), a script of 20,000 characters, 50 scenarios, 41 ladder points, 2,500 VaR days and 12
  workers, 500,000 characters of pasted fixings or 20,000 levels, pages of 200 items, a body of 2 MB.
- Path ids are checked against what is stored (a replay id names one of the versioned `results/replay*.json` files
  or a stored run; snapshot ids, trade ids and names follow a pattern), never used as a file path.

## 5. Values

- Levels and rates are fractions (a barrier of 60 % is `0.6`, a coupon of 8 % p.a. is `0.08`): the front converts at
  the edge. Dates are ISO 8601 (`2026-11-12`). Amounts are in the payment currency, signed from the desk's side.
- The model works at a spot of 100: fixings, smiles and the market context are in that scale; a real level is
  converted with the real spot of the market (`real_spot` on `POST /trading/fixings`).
- Ids are stable strings: `trade_id`, `quote_id`, `run_id`, `snapshot_id`, `job_id`.
- Every job result is one envelope (A6):

  ```json
  {
    "market": {"snapshot_id": "SPX-20261104T1600-8b7fa10118", "origin": {"surface": "observed", "...": "..."}, "as_of": "2026-11-04"},
    "method": {"name": "monte_carlo", "model": "slv", "n_paths": 100000},
    "result": {"...": "the kind's payload"}
  }
  ```

  `market` is the market the job priced on (the one in use when it was submitted; `snapshot_id` null when a job prices
  none, such as a fit or a backup), `method.name` how it was computed (`monte_carlo`, `replication`,
  `full_revaluation`, `historical_full_revaluation`, `point_in_time`, `fit`, `replay`, `eod_backfill`, ...) with its
  parameters beside it.
- **Every Monte Carlo number is `{"value": v, "se": s}`** (the `Estimate` schema): a quote's `result.price`,
  `coupon_rate`, a valuation's `unit_price` and `value`, a book's `value`, its positions and its groups, the Greeks
  (`delta`, `vega`, per name), the risk measures under keys `"greek|name|bucket"` (`book["delta|SPX|"]`), the VaR's
  factors, a script's `price`. `se` is null where the library has no error (a replication, a factor's interaction); a
  closed-form number stays a plain number. A quote's `headline` keeps `{kind, value, se, unit, label}`. The same shape
  comes back from the stored copies (`GET /structuring/quotes/{id}`, `GET /risk/runs/{id}`, `GET /quant/replay/runs/{id}`,
  `GET /risk/pnl`, `GET /quant/model-risk`). Rule of the conversion, and why the stores keep the library's shapes:
  `optlab.server.present`.

## 6. Pages

- Lists that grow return `{"items": [...], "next": "<cursor>" | null}`; pass `cursor=<next>` (and `limit`, 1 to 200)
  for the next page. The cursor is opaque. The audit log pages with `before=<next>` (an entry id).
- Small lists that do not grow (presets, scenarios, snapshots' smiles) are plain objects.

## 7. Jobs

A route that computes answers `202 {"job_id": "...", "reused": false}`. Jobs are rows of the database (ADR-0006), run
by the worker; a restart of the api or of the worker forgets nothing: queued jobs run when a worker is back, a job a
crash interrupted ends `error` with `{"type": "Interrupted", ...}` (never resumed: submit it again). Then:

- `GET /jobs/{job_id}`: `{id, kind, state, progress, step, eta, elapsed, submitted_at, started_at, finished_at,
  engine, version, cancel_requested, result, error}`. `state` among `queued`, `running`, `done`, `error`,
  `cancelled`; `progress` the fraction done (0 to 1, never backwards), `step` what the job is doing ("price SPX",
  "VaR: revaluations"), `eta` seconds left once the progress says enough (null early on); `result` the envelope of §5
  once done; `error` `{type, message}` (the library's message).
- `GET /jobs/{job_id}/events`: Server-Sent Events, for `EventSource`. `state` when the job is queued or starts,
  `progress` while it runs (at most two a second), then `done`, `error` or `cancelled` with the whole job, and the
  stream ends; a comment line every 15 s keeps it open, and the session is checked then (`unauthorized` and the end
  once it is over: sign in again). An event's id is the job's `version` (+1 at every change of
  its row), so a reconnect with `Last-Event-ID` gets the latest state only if it changed since, and always the last
  event:

  ```
  id: 2
  event: state
  data: {"id": "7145cd4beef91a2c", "kind": "quote", "state": "running", "progress": 0.0, "step": null, "eta": null, "elapsed": 0.0}

  id: 9
  event: progress
  data: {"id": "7145cd4beef91a2c", "kind": "quote", "state": "running", "progress": 0.42, "step": "quote of a Phoenix", "eta": 2.6, "elapsed": 1.9}

  id: 14
  event: done
  data: {"id": "7145cd4beef91a2c", "kind": "quote", "state": "done", "progress": 1.0, "result": {"market": {...}, "method": {...}, "result": {...}}, ...}
  ```

- `POST /jobs/{job_id}/cancel` → `{ok, state}`: a queued job ends `cancelled` at once; a running one stops at its next
  batch of paths (or chunk of VaR days) and ends `cancelled` (`state` is still `running` in the answer until then).
- `GET /jobs`: the caller's jobs queued or running, with their progress (an admin's: everyone's), for the status bar.
  `GET /admin/jobs`: every job, newest first, paged (admin).
- **Idempotency.** Send `Idempotency-Key: <1 to 128 of A-Z a-z 0-9 . _ : ->` on a submission: the same key from the
  same user gives back the job it first created (`reused: true`), even once done; the same key with another request is
  refused (422). Without a key, the same request (same parameters, same market, same book) from the same user while
  the first is still queued or running is given back too (a double click computes once); once it is done, asking again
  computes again.
- A job is visible to the user who submitted it and to admins; to anyone else it does not exist (404). Jobs of a
  schedule (the backup, the EOD) belong to the system: admins only.
- Each job freezes what the desk held when it was submitted (the market in use, the typed basket settings, the cost
  table's version, the trades by version and their fixings): switching the market, editing a trade or a setting
  never changes a run in progress.
- The worker runs in the api's process by default; `OPTLAB_WORKER=external` leaves the queue to
  `optlab worker` (the `worker` container). `GET /ready` answers 503 while no worker serves the queue.
- Schedules (`GET /admin/pipelines`, `PATCH /admin/pipelines/{name}`, `POST /admin/pipelines/{name}/run`, admin and
  audited): `backup` (nightly copy of the database, 14 days kept, on), `housekeeping` (old jobs, sessions and logs, on),
  `lake` (public sources, off), `eod` (the risk EOD of the day, off).

## 8. Reports

`GET /structuring/quotes/{quote_id}/report`, `GET /trading/book/report`, `GET /trading/desk-snapshot` and
`GET /risk/report` return standalone HTML (inline CSS and SVG, no script) with their own CSP: a file to save or send.

## 9. Serving the front

- Production: the front's static build is served by the API on the same origin when `OPTLAB_FRONTEND` names its
  folder (`Settings.frontend`), under the CSP of `security.md` §4 (`script-src 'self'`, no inline script). No CORS.
- Development: Vite serves the front and proxies the API, so the browser sees one origin:

  ```ts
  // vite.config.ts
  export default defineConfig({
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
    },
  });
  ```

  Keep `changeOrigin: false` (Vite's default): the API compares the browser's `Origin` (`http://127.0.0.1:5173`)
  with the `Host` the proxy forwards, and refuses a write when they differ. The session cookie is set for the Vite
  origin and sent back through the proxy.
- A quick start without real data: `optlab serve --demo-data`. The first time it builds `~/.cache/optlab/demo`
  (about two minutes): the synthetic mini-lake (`optlab.marketdata.synthetic`, seven names, 2019-01-01 to
  2021-06-30), the simulated calibration, the market of 30 June 2021 in use and four trades struck on their own past
  markets (`DEMO-01` to `DEMO-04`), and an admin account `demo` whose password is printed once. For a CI job or a
  fixed folder: `printf '%s\n' "$PASSWORD" | optlab demo data --to DIR --password-stdin`, then
  `optlab --data DIR serve`. The folder holds made-up numbers only (`docs/architecture/operations.md`).

## 10. Types for the front

```
npx openapi-typescript api/openapi.json --default-non-nullable false -o src/api/schema.d.ts
```

- `--default-non-nullable false` matters: version 7 otherwise makes every field with a default required, so a request
  body would have to spell out every option.
- `openapi-fetch` gives a typed client (`createClient<paths>()`); its generic `body` does not run TypeScript's
  excess-property check, so type a body with its schema (`const body: components["schemas"]["FixingsIn"] = ...`) to
  get it.
- `api/client-check/` is the proof CI runs on every push (`npm ci --ignore-scripts`, generate, `tsc --noEmit`): a small
  client signing in, quoting, polling a job, reading a page of trades and adding fixings.
- Typed: the models the server shapes itself (`Me`, `Job`, `JobList`, `JobAccepted`, `JobResult` with `MarketRef` and
  `Method`, `CancelOut`, `Pipeline`, `Page`, `UserOut`, `AuditPage`, `Problem`) and `Estimate`. The domain payload
  inside `JobResult.result` (a quote, a book valuation, a risk run) is an object whose fields are described here and
  by the routers' tests (`packages/server/tests/test_api_*.py` read them field by field); session 2 may add response
  models for the ones its screens read, without changing a field.

## 11. Adding a route (session 2 may)

Follow the conventions above, declare who may call it with `**access(...)`, bound what it costs, audit what it writes
(`audited`), and add its test beside the router's (`packages/server/tests/test_api_<router>.py`). The authorisation
matrix (`test_api_matrix.py`) covers the new route the day it is added; export the document again.
