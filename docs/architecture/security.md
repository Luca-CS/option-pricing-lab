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
| Denial of service | `n_paths = 10^12`, a 1 GB body, a script of a million lines | bounds on every costly parameter (schedules' parameters too), body size limit, two jobs at a time, a double submission computed once, cooperative cancellation; no per-job timeout (the bounds keep every job finite) | A5, A6 |
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
| 2 | Shared token accepted in the query string (`?token=`) and kept in a cookie without `Secure` | Medium | removed with accounts (A5, done: `server.py` deleted, `test_api_security.py`) |
| 3 | Public tunnel through localhost.run (`--tunnel`) | High when used | removed (ADR-0002) |
| 4 | A 500 returns `f"{type(exc).__name__}: {exc}"`, which can carry paths and SQL | Low | `problem+json` without details (A5, done; a job's error keeps the library's message for its submitter) |
| 5 | No bound on the request body (`Content-Length` read in full) nor on `n_paths` and other costly fields (`replay_run` takes any `n_paths`) | Medium (local DoS) | bounds and a body limit (A5, done: `api.md` §4) |
| 6 | `subprocess` calls: `git rev-parse` for the code version (`marketdata/snapshot.py`), PowerShell with a fixed script (`netcheck.py`) | Info | fixed arguments confirmed; kept |
| 7 | Static files: resolved and checked with `is_relative_to` | Info | no traversal; kept in spirit (served by Starlette's `StaticFiles` in v1) |
| 8 | Replay run ids: `[\w.-]+` without separators | Info | no traversal; v1 checks the id's pattern and looks it up among the stored runs and the `results/replay*.json` files (A5) |
| 9 | HTML reports escape every value (`html.escape`) | Info | kept; CSP added |
| 10 | Process pools (spawn) pickle internal objects | Info | internal only; kept |

### Found in A5

| # | Finding | Severity | Fate |
|---|---|---|---|
| 11 | `data_vol_index` put the index name into the SQL text (after stripping it to `[A-Z0-9.]`) | Low | bound as a parameter (`Lake.read(params=...)`), the route checks the name's pattern |
| 12 | The per-address login throttle counted successful logins: users behind one address (everyone on 127.0.0.1) would lock each other out | Low | only failures count |
| 13 | A body sent without `Content-Length` (chunked) over the limit turned into a 400 inside FastAPI | Info | the guard's limit is an HTTP 413 FastAPI passes on |
| 14 | FastAPI's docs page loads `swagger-ui-dist@5` (a floating version) from a CDN | Medium when on | off by default; when on, pinned, integrity-checked, and its CSP allows only that script and its init by hash |
| 15 | A 500 was answered by Starlette's outermost error middleware, outside the guard: no security headers on it | Info | the guard answers it (problem+json, headers), test |
| 16 | The 404 of an unknown snapshot named the database file's local path | Low | the message names the id only |
| 17 | With the LAN profile on, any `Host` is accepted (this table says "the LAN name") | Low (cookies stay tied to the real name, so a rebound page has no session) | an allow-list of the LAN names with Caddy's profile (A7) |
| 18 | The lockout answers 423 for an existing account only, which tells that the name exists once it is locked | Low | kept: the owner must know the account is locked; the CLI unlocks it (A9 review) |

### Found in A6 (the `security-review` pass on the job queue: nothing above its bar)

Checked and sound: a job's owner is in its row from the first write, and another user's job or a system job is a 404
on read, cancel and stream; idempotency (key or identical request) never crosses users; job kinds are fixed strings
and a schedule's kind cannot be changed; no request reaches a file path (backup and log names are fixed patterns);
every query is bound; the queue holds JSON only, nothing is unpickled; SSE data is JSON-encoded, so a name cannot
inject an event. `GET /jobs` lists the caller's jobs only (v0's status bar showed everyone's).

| # | Finding | Severity | Fate |
|---|---|---|---|
| 19 | `nvidia-smi` found with `shutil.which`, which on Windows looks in the working folder before the PATH: a planted `nvidia-smi.exe` would run every minute | Low (needs local write access) | the driver's own path only (System32, NVSMI; `/usr/bin` on Linux), test |
| 20 | The `eod` schedule took any `options`, without the bounds of `POST /risk/eod-runs` | Low (admin only) | validated by the same `RiskOptions`, test |
| 21 | The SSE stream checked the session at its start only, then followed the job for up to an hour after a logout or a revocation | Low | the session is checked again at every keepalive (15 s): `event: unauthorized` and the end, test |
| 22 | Running a schedule now dropped the admin's `Idempotency-Key` (the schedule's own context replaced it) | Info | kept through, test |
| 23 | The housekeeping removed every `*.log.*` of the logs folder | Info (the folder is the settings') | our own rotated files only (`api.log.*`, `worker.log.*`) |
| 24 | The external worker's desk did not carry the LAN rule (`desk.shared`) | Info (no job reads it) | set from the settings as in the api |
| 25 | A job's error is the library's message (it may name a local path), kept in the row and listed in `/admin/jobs` | Info | kept as finding 4 accepted it: shown to its submitter and to admins only |
| 26 | The book report reads the newest book valuation of anyone on the market in use | Info (v0 shared it the same way) | kept: the book is the desk's, not a user's |
| 27 | The public `/ready` says whether a worker is alive (503 when none) | Info | kept: a readiness probe must say it |

### Found in A7, the command line (the `security-review` pass: nothing above its bar)

Checked and sound: `VACUUM INTO` takes a bound parameter, the table names of a copy's check are fixed, and a copy is
opened read-only; `optlab check` runs this interpreter with fixed arguments and no shell; the new admin listing of
the lake's copies and the `lake_backup` schedule are admin only, with `keep` bounded (1 to 52) and no path from a
request; jobs submitted from the console are the system's (a 404 to API users); the demo account's password comes
from `secrets` and is printed once, never logged or written; no pickle, `eval` or YAML.

| # | Finding | Severity | Fate |
|---|---|---|---|
| 28 | A lake restoration used the manifest's keys as paths: a planted copy listing `../` keys would make `optlab restore --lake` write outside the lake (a git hook of the checkout, `optlab.env`) | Low today (only Luca's processes write `<data>/backups`); a real control once a container writes that folder | keys must be relative paths inside the copy (no `..`, drive, absolute path or link), the files found must be exactly the manifest's, and the restore checks each target stays inside the lake; test |
| 29 | `optlab serve --demo-data` used `<temp>/optlab-demo`, shared on a multi-user Unix machine: another account could plant a "finished" demo folder with its own admin and settings | Low (Windows' `%TEMP%` is per user) | the folder is `~/.cache/optlab/demo`; on Unix a folder owned by another user, or writable by others, is refused |
| 30 | The demo's paths could follow the environment: an `OPTLAB_DATABASE` or `OPTLAB_LAKE` (or the real folder's `optlab.env` through `OPTLAB_DATA`) would put the `demo` admin and its trades in the real database | Low (environment is trusted, but demo mode must never touch real data) | the demo builds its settings with every path in its folder and no env file, and `serve --demo-data` refuses a database, lake or backups folder outside it; test |
| 31 | A restored database brought back the sessions of its time (revoked or expired since), and left no trace | Low | every session is closed after a restoration, which is in the audit log (`cli_restore_database`); test |
| 32 | `restore latest` takes the copy whose name sorts last, so a planted far-future name wins; a copy's schema is not compared with its revision's (extra triggers or views would come with it) | Info (same condition as 28: write access to the backups) | kept: the backups folder is as trusted as the data; the containers' part keeps it out of reach of what they run |
| 33 | An admin may set the lake copy's `keep` to 1 and run it, which removes the older copies | Info (as `keep_days` of the nightly copy) | kept: admin only, audited, and the newest copy stays complete |

### Found in A7, the containers (the `security-review` pass: nothing above its bar)

Checked and sound:
- **The Host allow-list (finding 17, fixed here).** With the LAN profile on, only a loopback name or one of
  `OPTLAB_LAN_NAMES` passes. A missing `Host`, a suffix, another address or a trailing dot is refused, and the profile
  will not start without its names.
- **The `container` flag** allows the exact string `0.0.0.0` only.
- **Uvicorn's proxy headers** are trusted under the LAN profile only, and from the proxy's fixed address only. Caddy
  replaces any `X-Forwarded-*` a client sends.
- **The images.** The `.dockerignore` is an allow-list, the final stages hold the installed packages only, base
  images are pinned by digest, the lock is installed with its hashes, and the processes run as uid 10001.
- **`images.yml`.** It runs on `pull_request` (never `_target`) with `contents: read`; event fields reach the shell
  through `env:`.

| # | Finding | Severity | Fate |
|---|---|---|---|
| 34 | `OPTLAB_CONTAINER=true` was an ordinary setting: a native process given it with `OPTLAB_HOST=0.0.0.0` would listen on every interface without the LAN profile | Low (the environment is trusted) | refused unless the process runs in a container (`/.dockerenv`, `/run/.containerenv`); test |
| 35 | The `desk` network had no `ip_range`: after recreations a container could take the proxy's address, which the api trusts for `X-Forwarded-For` | Low | dynamic addresses in 172.30.0.128/25, away from the fixed .10 and .20 |
| 36 | The worker did not get the LAN profile's settings, so `desk.shared` stayed False in its jobs | Info (no job reads it today, finding 24) | the same `OPTLAB_LAN` and `OPTLAB_LAN_NAMES` as the api |
| 37 | `seed-volumes.sh` put a file name inside single quotes of a `sh -c` string | Info (the operator's own argument, in a container without network) | passed as an environment variable |
| 38 | The api image sets `OPTLAB_CONTAINER` and `OPTLAB_HOST=0.0.0.0`: `docker run -p 8765:8765` on a Docker whose `daemon.json` lacks `"ip": "127.0.0.1"` would publish the login over plain HTTP on every interface | Low | kept: compose publishes on 127.0.0.1 explicitly and this machine's daemon defaults to it; operations.md says to run the images through compose only |
| 39 | One network, not `internal`: the api can reach the internet and the WSL host's other listeners, which it does not need | Low | kept for now: Docker publishes no port of a container that is only on an internal network; an egress filter is for A9's review |
| 40 | Caddy's image runs as root inside its container (read-only, no capability, no new privileges), the one process facing the LAN | Low (the LAN profile is off) | kept until the profile is turned on: a non-root Caddy then, with its volumes owned by its user |
| 41 | Each LAN device is told to trust Caddy's internal root CA: no name constraints, 10 years, its key in a container that has internet access | Low (off) | kept: operations.md will add the root's fingerprint, and the advice to trust it on the desk's devices only, when the profile is turned on |
| 42 | Behind `netsh portproxy`, every LAN client reaches Caddy from the Windows host's address: the login throttle by address and the audit log's addresses collapse into one | Info (off; the per-account lockout still holds) | kept, written in operations.md's LAN section |
| 43 | Docker's and NVIDIA's signing keys are fetched over HTTPS without a fingerprint check, as their documentation does | Info | kept: the apt repositories then check every package against them |

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
