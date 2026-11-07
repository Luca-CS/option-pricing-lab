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
