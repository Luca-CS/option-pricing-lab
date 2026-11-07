# Security: threat model and controls

Status: **draft** (A0, 7 November 2026); reviewed and completed in A9 with the results of every check. Principle:
safety above all; between a nicer option and a safer one, the safer one, written in the ADR.

## 1. What is protected

| Asset | Why it matters |
|---|---|
| Luca's machine (Windows 11, WSL2, GPU, his files) | the desk runs code on it; a hole is a hole in his PC |
| His data: the lake (Yahoo personal-use data, licensed samples), the stores (trades, fixings), the archive | personal-use licences forbid redistribution; the trades are his work |
| The repository and its history | public; must hold no secret and no data; its integrity is the integrity of every image |
| The images on GHCR and the release assets | what `optlab deploy` runs |
| The accounts of the app | who may write trades, scenarios, costs, users |
| The static demo on GitHub Pages | the public face; must leak nothing and call nothing on the machine |

## 2. Entry points

| Entry | Exposure in v1 | Main controls |
|---|---|---|
| HTTP API and front (`api`) | `127.0.0.1:8765` only; LAN only through the `lan` profile (Caddy, TLS), with Luca's agreement | accounts, sessions, CSRF, RBAC, Host and Origin checks, bounds, CSP |
| The worker's outbound calls (market data, image pulls) | outbound HTTPS to named hosts | fixed URLs per source, timeouts, responses parsed as data (`parse` per source, tested offline) |
| Pull requests and CI | public repository | hosted runners only, no secret in PR workflows, no `pull_request_target`, SHA-pinned actions, approval for outside contributors |
| Dependencies (PyPI, npm in session 2, base images) | supply chain | `uv.lock` with hashes, `uv sync --locked`, Dependabot PRs reviewed, `pip-audit`, Trivy, images by digest, SBOM, provenance |
| The static demo | public | no network call to the machine; recorded responses only; no raw market level |
| Local shell | Luca | root of trust: `optlab users`, `deploy`, `restore` |

## 3. Threats and controls (STRIDE, by entry)

| Threat | Example | Control in v1 | Step |
|---|---|---|---|
| Spoofing a user | stolen session cookie, guessed password | argon2id, login throttling and lockout, `HttpOnly` + `SameSite=Strict` + `Secure` cookies, server-side sessions with expiry and rotation | A5 |
| Cross-site request forgery | a web page posts to `http://127.0.0.1:8765` from Luca's browser | CSRF header token, `Origin` check, JSON content type required, `SameSite=Strict` | A5 (and v0 fix, §5) |
| DNS rebinding | a page on `evil.example` resolves to 127.0.0.1 and reads the API | `Host` header allow-list (`127.0.0.1`, `localhost`, the LAN name) | A5 (and v0 fix, §5) |
| Tampering with trades or scenarios | a viewer edits the book | RBAC on every route, authorisation matrix test, audit log of every write | A5 |
| Repudiation | "I did not sign this scenario" | `signed_by` = the authenticated user; audit log append-only from the app | A5 |
| Information disclosure | stack traces, raw quotes, levels in a report, data in an image | `problem+json` without traces, `desk.shared` rule for raw quotes beyond localhost, data never in images (test), gitleaks | A5, A7 |
| Denial of service | `n_paths = 10^12`, a 1 GB body, a script of a million lines | bounds on every costly parameter, body size limit, job concurrency, worker timeouts | A5, A6 |
| Elevation of privilege | code execution through the API | no `eval`/`exec` (the script language is a hand-written parser, size-bounded); `pickle` only between internal processes, never on network data; `subprocess` with fixed arguments only; no file path from the API (ids checked against a registry) | A5 |
| Container escape or reach | a compromised api reaches the host | non-root, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, no Docker socket mounted, minimal volumes (lake read-only for api), resource limits | A7 |
| Network exposure | a port opened on every interface by Docker | `127.0.0.1:` on every published port, daemon on its unix socket only, WSL in NAT, `docker inspect` and `netstat` checks | A7, A9 |
| Supply chain | a malicious dependency update | lock with hashes, updates by reviewed PR only, `pip-audit`, Trivy, CodeQL | A1 |
| CI compromise | a PR exfiltrates a token | no secret in PR workflows, `permissions: contents: read`, `packages: write` only in `release.yml` on tags | A1, A9 |

## 4. Headers

- Front: `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
  connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'` (session 2 builds without inline
  scripts).
- Standalone reports (inline CSS and SVG, no script): `default-src 'none'; style-src 'unsafe-inline'; img-src data:;
  frame-ancestors 'none'`.
- Everywhere: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` denying camera,
  microphone, geolocation; `Cache-Control: no-store` on the API; HSTS under TLS (LAN profile).

## 5. Findings on v0 (read in A0)

| # | Finding | Severity | Fate |
|---|---|---|---|
| 1 | **No `Host` or `Origin` check, and the body is parsed as JSON whatever its content type**: while the desk runs, any web page open in Luca's browser can send a simple cross-site `POST` (`Content-Type: text/plain`) to `127.0.0.1:8765` and create, edit or delete trades, fixings, scenarios, cost tables, or switch the market in use; with DNS rebinding it can also read the responses (book, quotes, levels on the owner's machine). Chrome's Private Network Access blocks part of this, not every browser | High | fixed in v0 right away (Host allow-list, `Origin` check on writes, `application/json` required), with tests; v1 adds CSRF tokens and sessions |
| 2 | Shared token accepted in the query string (`?token=`) and kept in a cookie without `Secure` | Medium | removed with accounts (A5) |
| 3 | Public tunnel through localhost.run (`--tunnel`) | High when used | removed (ADR-0002) |
| 4 | A 500 returns `f"{type(exc).__name__}: {exc}"`, which can carry paths and SQL | Low | `problem+json` without details (A5) |
| 5 | No bound on the request body (`Content-Length` read in full) nor on `n_paths` and other costly fields (`replay_run` takes any `n_paths`) | Medium (local DoS) | bounds and a body limit (A5) |
| 6 | `subprocess` calls: `git rev-parse` for the code version (`marketdata/snapshot.py`), PowerShell with a fixed script (`netcheck.py`) | Info | fixed arguments confirmed; kept |
| 7 | Static files: resolved and checked with `is_relative_to` | Info | no traversal; kept in spirit (served by Starlette's `StaticFiles` in v1) |
| 8 | Replay run ids: `[\w.-]+` without separators | Info | no traversal; v1 looks ids up in the database |
| 9 | HTML reports escape every value (`html.escape`) | Info | kept; CSP added |
| 10 | Process pools (spawn) pickle internal objects | Info | internal only; kept |

## 6. Secrets

None today (checked in A0: no key, token or password in the tree; gitleaks in A1 checks the history). Future secrets
(for example a data provider key) go to `optlab-data/config/`, outside the repository, with restricted rights.
Test accounts of the session use generated passwords stored in an untracked local file; Luca creates his own admin
account and the session never types his password.

## 7. Checks of A9 (results to be filled in)

- A security review pass per domain (the `security-review` skill on every PR that touches the server, auth or CI).
- OWASP ZAP baseline scan of the local instance (free Docker image).
- Trivy: no critical or high vulnerability left untreated.
- The authorisation matrix green.
- Container inspection (`docker inspect`: user, capabilities, ports on `127.0.0.1`).
- A restore drill from a backup.
