# ADR-0020: Local accounts, sessions, roles and an audit log

- Status: Accepted, 7 November 2026

## Context

The first desk had no users: whoever reached the port could do everything, and beyond localhost a shared token
travelled in the query string. Scenarios carried a free-text `signed_by`.

## Decision

- **Local accounts**, passwords hashed with **argon2id** (`argon2-cffi`'s defaults, rehashed at sign-in when they
  change), at least 12 characters, created with `optlab users create`; the password is read with `getpass` (or
  standard input with `--stdin`), never an argument. An unknown name costs the same time as a wrong password.
- **Sessions by cookie**: a random 256-bit token in `optlab_session`, its sha256 in the table, `HttpOnly`,
  `SameSite=Strict`, `Secure` under TLS; idle (8 hours) and absolute (7 days) expiry; a new session at every sign-in;
  any change of a user's roles, password or state revokes their sessions.
- **CSRF**: a 256-bit token per session, sent in `X-CSRF-Token` on every state-changing request, plus an `Origin`
  check.
- **Throttling**: an account locks after 5 failures for 15 minutes (423); an address that fails 20 times in 10 minutes
  gets 429. Only failures count, so users behind one address do not block each other.
- **Roles checked on the server on every route**: `admin`, `trader`, `structurer`, `risk`, `quant`, `viewer`; a user
  may hold several. Each route declares its roles (`**access("trader", "risk")`), carried in the OpenAPI document as
  `x-roles`; `GET /api/v1/auth/me` returns the caller's roles and permissions. Every route needs a session except
  `health`, `ready` and `login`. A job is visible to its submitter and to admins only.
- **Audit log** of every write (trades, fixings, scenarios, costs, users, schedules, the market in use, job
  submissions and cancels, sign-ins and failed sign-ins): who, when, action, target, the sha256 of the state before
  and after, append-only from the application. Admins read it on `GET /admin/audit`.
- `signed_by` of a scenario is the authenticated user.

## Consequences

- The front hides what a role cannot do; the server refuses it anyway (`test_api_matrix.py`: every operation × every
  role).
- A lost admin password is reset on the machine (`optlab users password <name>`): local access is the root of trust.

## Alternatives considered

- OAuth or OIDC with a provider: a third-party account. Excluded.
- HTTP basic authentication behind Caddy: no roles, no audit, no sign-out.
- A JWT in local storage: exposed to any script injection; a server-side session is revocable.
