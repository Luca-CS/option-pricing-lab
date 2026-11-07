# ADR-0020: Local accounts, sessions, roles and an audit log

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: Sécurité; §4.10

## Context

v0 has no user: everyone who reaches the port can do everything; beyond localhost a shared token travels in the query
string (`?token=`), in a cookie and in a header. Scenarios carry a free-text `signed_by`.

## Decision

- **Local accounts**, passwords hashed with **argon2id** (`argon2-cffi`, parameters from OWASP's current guidance),
  created with `optlab users create` (Luca creates his admin account himself; test accounts get generated passwords
  kept in a local untracked file).
- **Sessions by cookie**: random 256-bit id stored hashed server-side, `HttpOnly`, `SameSite=Strict`, `Secure` under
  TLS, idle and absolute expiry, rotation at login and on privilege change, logout everywhere.
- **CSRF**: a per-session token sent in a header on every state-changing request, plus an `Origin` check.
- **Login throttling and temporary lockout** per account and per client address.
- **RBAC checked on the server on every route**: roles `admin`, `trader`, `structurer`, `risk`, `quant`, `viewer`; a
  user may hold several. Permissions are declared on the route; `GET /api/v1/auth/me` returns the roles and the
  permissions for the front. An authorisation matrix test covers every route x role.
- Every route needs authentication except `health`, `ready` and `login`.
- **Audit log** of every write (trades, fixings, scenarios, costs, users, schedules, the market in use, job
  submissions): who, when, what, before and after (or their hash), in the operational database, append-only from the
  application.
- `signed_by` of scenarios becomes the authenticated user.

## Consequences

- The front of session 2 hides what a role cannot do; the server refuses it anyway.
- Lost admin password: `optlab users reset-password` on the machine (local access is the root of trust).

## Alternatives considered

- OAuth or OIDC with a provider: a third-party account. Excluded.
- HTTP basic auth behind Caddy: no roles, no audit, no logout.
- JWT in local storage: exposed to any script injection; a server-side session is revocable.
