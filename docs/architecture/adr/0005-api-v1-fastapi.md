# ADR-0005: API v1 on FastAPI and Pydantic v2

- Status: Accepted, 7 November 2026

## Context

The first desk mapped 57 routes by hand on the standard library's `ThreadingHTTPServer`: regex routing, JSON parsed
without a size bound, ad-hoc errors, a 500 that returned the exception's text. A web front needs a contract it can
generate its types from.

## Decision

- **FastAPI with Pydantic v2** under `/api/v1`, served by uvicorn. One router per domain: `system`, `structuring`,
  `trading`, `market`, `risk`, `quant`, `admin`; every route declares its roles (`x-roles` in the document).
- **`api/openapi.json` is versioned** and exported by `optlab api export`. `test_openapi.py` fails when it is stale. The
  front generates its TypeScript types from it and must compile on them (the CI job `api-contract`).
- **Conventions** ([api.md](../api.md)): levels as fractions; amounts in the payment currency, signed from the desk's
  side; every Monte Carlo value as `{value, se}`; every result carries `market: {snapshot_id, origin, as_of}` and
  `method`; stable ids; errors as `application/problem+json` (RFC 9457) with field paths; cursor pagination; ISO 8601
  dates.
- **Jobs**: a `POST` that computes answers `202 {job_id}`; `GET /jobs/{id}`, `GET /jobs/{id}/events` (Server-Sent
  Events: progress, step, ETA, result or error), `POST /jobs/{id}/cancel`. Submission is idempotent by key. SSE is a
  plain streaming response over the job row; the browser's `EventSource` reconnects with `Last-Event-ID`.
- **Bounds on everything that costs**: paths, grid sizes, scenarios, script length, the request body.
- The api serves the front's build on the same origin, under the CSP of [security.md](../security.md). No CORS.

## Consequences

- Pydantic models document every request and response; the front writes no API type by hand.
- FastAPI and uvicorn are dependencies of `optlab-server` only; the domain packages stay free of them.
- Tests: one file per router, `test_api_security.py`, and an authorisation matrix built from the OpenAPI document
  (every operation × every role → the expected status, `test_api_matrix.py`), so no route escapes it.

## Alternatives considered

- **Keep the standard-library server** and add authentication: every security feature (sessions, CSRF, body limits,
  problem details, OpenAPI) by hand. Rejected: more code to keep correct.
- **Litestar, or Starlette alone**: sound tools; FastAPI has the widest knowledge base and generates OpenAPI from the
  same models.
- **WebSockets for progress**: a two-way channel is not needed; SSE goes through any proxy and reconnects by itself.
