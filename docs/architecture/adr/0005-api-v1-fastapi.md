# ADR-0005: API v1 on FastAPI and Pydantic v2

- Status: Accepted (A0 checkpoint, 7 November 2026)
- Handoff §1: API; §4.2

## Context

v0 maps 57 routes by hand on the standard library's `ThreadingHTTPServer` (`app/server.py`, `app/risk_api.py`): regex
routing, JSON parsed without a size bound, errors as `{"error", "field"}`, a 500 that returns the exception's text.
The second session builds a new front on this API and needs a contract it can generate types from.

## Decision

- **FastAPI with Pydantic v2** under `/api/v1`, served by uvicorn. One router per domain: `structuring`, `trading`,
  `risk`, `market`, `quant`, `admin`, `auth`, plus `jobs`, `health` and `ready`.
- **`api/openapi.json` is versioned** and exported by `optlab api export`. A test fails when it is stale. CI generates
  TypeScript types with `openapi-typescript` and compiles a small `openapi-fetch` client against them: the contract is
  proven usable before session 2.
- **Conventions** (`docs/architecture/api.md`, written in A5): levels as fractions; amounts in the payment currency,
  signed from the desk's side; every Monte Carlo value as `{value, se}`; every result carries
  `market: {snapshot_id, origin, as_of}` and `method`; stable ids (`trade_id`, `quote_id`, `run_id`, `job_id`); errors
  as `application/problem+json` (RFC 9457) with field paths for form validation; cursor pagination; ISO 8601 dates.
- **Jobs**: a `POST` that computes returns `202 {job_id}`; `GET /jobs/{id}`, `GET /jobs/{id}/events` (Server-Sent
  Events: progress, step, ETA, result or error), `POST /jobs/{id}/cancel`. Submission is idempotent by key.
  SSE is a plain `StreamingResponse` over the job row (no extra dependency); the browser's `EventSource` reconnects
  with `Last-Event-ID`.
- **Bounds on everything that costs**: `n_paths`, grid sizes, number of scenarios, script length, request body size
  (a global limit in the ASGI layer). No denial of service by one giant parameter.
- The api serves the front's static build from `OPTLAB_FRONTEND_DIR` on the same origin, with the CSP of
  `security.md`. No CORS.
- Tests: one file per router (`httpx` client), and an authorisation matrix generated from the route table: every
  route x every role -> the expected status, so no route escapes it. It replaces `tests/test_app_api.py`.

## Consequences

- Pydantic models document every request and response; the front generates its types and writes none by hand.
- Two new runtime dependencies in `optlab-server` only (FastAPI, uvicorn); the domain packages stay free of them.
- The v0 server and `tests/test_app_js.py` go away with the old front (A5).

## Alternatives considered

- **Keep the standard-library server** and add auth: every security feature (sessions, CSRF, body limits, problem
  details, OpenAPI) by hand. Rejected: more code to keep correct.
- **Litestar, Starlette alone**: fine tools; FastAPI has the widest knowledge base and generates OpenAPI from the same
  models. Chosen for the showcase and for session 2.
- **WebSockets for progress**: two-way channel not needed; SSE goes through any proxy and reconnects by itself.

## Implementation (A5)

- `optlab.server.app`: `api_app()` holds the routes alone (what `api/openapi.json` describes, exported by
  `python -m optlab.server.openapi export`, checked by `test_openapi.py`); `create_app(settings, desk)` adds the
  desk, the accounts, the audit log, the front's build when `Settings.frontend` is set, and the guard. Routers in
  `optlab.server.routers` (`system`, `structuring`, `trading`, `market`, `risk`, `quant`, `admin`); `operationId` is the
  route's function name; `x-roles` on every operation; the document's 422 and error responses are `Problem`
  (`application/problem+json`), not FastAPI's default validation body.
- `optlab.server.web`: problem handlers (a service's `KeyError` is a 404, an `ApiError` keeps its status and field, an
  unknown exception is a 500 with no detail), the `access()` check, and `Guard`, a pure ASGI middleware: `Host`
  allow-list without the LAN profile, same-origin rule on writes, JSON only, body limit (by `Content-Length`, and by
  counting a chunked body), security headers and the CSPs.
- Request models in `schemas.py` forbid unknown fields and bound what costs; the response models the server shapes
  (`Me`, `Job`, `JobList`, `UserOut`, `AuditPage`, `Page`, `JobAccepted`) are typed. The domain payloads keep the
  library's shapes in v1.0; the `{value, se}` shape of every Monte Carlo number and the
  `{market, method, result}` envelope come with the job model of A6 (ADR-0006).
- SSE: event ids are the rank of the state (queued 1, running 2, the end 3), stable across connections, so
  `Last-Event-ID` resumes; progress events come with A6.
- `uvicorn` serves it (`python -m optlab.server`; `optlab serve` in A7), on 127.0.0.1 unless the LAN profile is on,
  `server_header=False`.
- The TypeScript proof is `api/client-check` (openapi-typescript 7.13.0 with `--default-non-nullable false`,
  openapi-fetch 0.17.0, TypeScript 5.9.3, `npm ci --ignore-scripts`), the CI job `api-contract`.
- Tests: one file per router (`test_api_system.py`, `_structuring`, `_trading`, `_market`, `_risk`, `_quant`, `_admin`),
  `test_api_security.py`, the matrix `test_api_matrix.py` (76 operations x 7 callers, from the OpenAPI document), and
  `test_api_inventory.py` (every v1 route of `feature-inventory.md` exists; every route is in it or named as new). The
  desk's services keep `test_desk.py` (the former `test_app_api.py`, without its HTTP part).
