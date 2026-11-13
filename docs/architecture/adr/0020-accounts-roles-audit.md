# ADR-0020: Local accounts, sessions, roles and an audit log

- Status: Accepted (A0 checkpoint, 7 November 2026)
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

## Implementation (A5)

- `optlab.server.security.Accounts` on the database (Alembic revision `0002`: `users`, `sessions`, `audit_log`):
  argon2id through `argon2-cffi`'s `PasswordHasher` defaults (rehash at login when they change), a dummy verification
  for unknown names so timing says nothing, passwords of 12 characters at least.
- Sessions: a 256-bit token in the cookie `optlab_session`, its sha256 in the table, a 256-bit CSRF token per session
  sent back in `X-CSRF-Token`; idle (`session_idle_minutes`, 480) and absolute (`session_absolute_hours`, 168) expiry;
  a new session at every login; every change of roles, password or state revokes the user's sessions.
- Lockout after `login_max_failures` (5) for `login_lockout_minutes` (15), answered 423; per address, failed logins
  only are counted (`login_per_address`, 20 in 10 minutes, then 429), so users behind one address do not block each
  other.
- Roles are declared on each route (`**access("trader", "risk")`), carried in the OpenAPI document as `x-roles`, and
  read back by `GET /auth/me` to list the caller's permissions (operation ids). A job is visible to its submitter and
  to admins only (404 for anyone else).
- `optlab.server.audit.Audit`: one row per write (who, when, action, target, sha256 of the state before and after,
  a detail without secrets), including logins, failed logins, job submissions and cancels, and the commands of
  `optlab users` (as the system). Read by admins on `GET /admin/audit`.
- The command line is `optlab users create|list|roles|password|disable|enable` (`python -m optlab.server.users` before
  A7, an alias since): the password is read with `getpass` (or standard input with `--stdin`), never an argument. A lost admin
  password is reset there (`password <name>`).
