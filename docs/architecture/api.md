# API v1: conventions

The contract session 2 builds the front on (handoff §7, ADR-0005, ADR-0020). The routes and their bodies are in
[`api/openapi.json`](../../api/openapi.json); this page says what the document cannot: how to sign in, what an error,
a job and a page look like, the units, the bounds, and how the front is served. Every function of v0 and its route
are in [`feature-inventory.md`](feature-inventory.md); roles in [`security.md`](security.md).

## 1. The document

- Base path `/api/v1`. The live document is `GET /api/v1/openapi.json` (public, like `health` and `ready`); the versioned
  copy is `api/openapi.json`, written by `uv run python -m optlab.server.openapi export` (`optlab api export` in A7).
  `packages/server/tests/test_openapi.py` fails when the copy drifts from the routes.
- `operationId` is the route's function name (`create_trade`, `get_quote`), unique. Tags are the domains: `structuring`,
  `trading`, `market`, `risk`, `quant`, `admin`; the system routes (health, auth, jobs) have none.
- Every operation carries `x-roles`: the roles that may call it (`public`, or `viewer` for any signed-in user, or a list
  such as `["risk", "trader"]`). `admin` may call everything. `GET /auth/me` returns the caller's `permissions`: the
  operation ids it may call, read from the same document, so the front hides what the server would refuse.
- `securitySchemes`: `session` (the cookie) on every non-public operation, plus `csrf` (the header) on every one that
  is not a `GET`.
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
- Accounts are created on the machine (`uv run python -m optlab.server.users create <name> --roles admin`, the password
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
- Monte Carlo values carry their standard error. In v1.0 the shapes are the library's: a quote's `headline` is
  `{kind, value, se}`; a price is `price` and `stderr` (`unit_price`, `unit_stderr` and `value`, `stderr` for a
  valuation); risk measures are `[value, se]` pairs under keys `"greek|name|bucket"` (`"delta|SPX|"`). Results name
  their market (`snapshot_id`, `valuation`) and their `method`. **A6** gives every job result one envelope,
  `{market: {snapshot_id, origin, as_of}, method, result}`, and every Monte Carlo number the shape `{value, se}`, with
  the job model of ADR-0006; the routes and ids do not change.

## 6. Pages

- Lists that grow return `{"items": [...], "next": "<cursor>" | null}`; pass `cursor=<next>` (and `limit`, 1 to 200)
  for the next page. The cursor is opaque. The audit log pages with `before=<next>` (an entry id).
- Small lists that do not grow (presets, scenarios, snapshots' smiles) are plain objects.

## 7. Jobs

A route that computes answers `202 {"job_id": "..."}`. Then:

- `GET /jobs/{job_id}`: `{id, kind, state, elapsed, result, error}`, `state` among `queued`, `running`, `done`,
  `error`, `cancelled`; `error` is `{type, message}` (the library's message).
- `GET /jobs/{job_id}/events`: Server-Sent Events, for `EventSource`. One event per change of state, then the last one
  with the whole job, and the stream ends. An event's id is the rank of its state, the same on every connection, so a
  reconnect with `Last-Event-ID` gets only what it missed:

  ```
  id: 2
  event: state
  data: {"id": "7145cd4beef9", "kind": "quote", "state": "running", "elapsed": 0.01}

  id: 3
  event: done
  data: {"id": "7145cd4beef9", "kind": "quote", "state": "done", "result": {...}, "error": null, "elapsed": 1.84}
  ```

  The last event is `done`, `error` or `cancelled`. **A6** adds `progress` events (step, fraction, ETA) between them,
  persistent jobs (a restart forgets nothing; a job cut by a crash ends `error`) and idempotent submission by key.
- `POST /jobs/{job_id}/cancel`: a queued job is dropped; a running one cannot be interrupted, its result is dropped.
- `GET /jobs`: the jobs queued or running, for the status bar.
- A job is visible to the user who submitted it and to admins; to anyone else it does not exist (404).
- Each job keeps the market in use when it was submitted: switching the market never changes a run in progress.

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
- A quick start without real data: `optlab serve --demo-data` on the synthetic mini-lake and an example book (A7).

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
- Domain payloads (a quote, a book valuation, a risk run) are described in prose and by the library's tests in v1.0;
  their response models come with the A6 envelope. The models the server shapes itself are typed now: `Me`, `Job`,
  `JobList`, `JobAccepted`, `Page`, `UserOut`, `AuditPage`, `Problem`.

## 11. Adding a route (session 2 may)

Follow the conventions above, declare who may call it with `**access(...)`, bound what it costs, audit what it writes
(`audited`), and add its test beside the router's (`packages/server/tests/test_api_<router>.py`). The authorisation
matrix (`test_api_matrix.py`) covers the new route the day it is added; export the document again.
