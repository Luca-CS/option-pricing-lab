# Security

How the desk protects the machine it runs on, its data and its users. Vulnerability reports: [SECURITY.md](../../SECURITY.md).

## 1. Exposure

The desk is a local application. It is never reachable from the internet ([ADR-0002](adr/0002-never-exposed.md)), and
its public face is a static demo on GitHub Pages that calls no server.

| Entry | Exposure | Main controls |
|---|---|---|
| HTTP API and front | `127.0.0.1:8765` only. The LAN only through the optional `lan` profile (Caddy with TLS), which is off by default | accounts and sessions, CSRF token, roles on every route, `Host` and `Origin` checks, bounds on every costly field, strict CSP |
| The worker's outbound calls | HTTPS to the named market-data sources and to GHCR | fixed URLs per source, timeouts, responses parsed as data and tested offline |
| The public repository and its CI | public | hosted runners only, no secret in pull-request workflows, actions pinned by SHA |
| Dependencies and images | supply chain | locks with hashes, reviewed updates, scanners, images by digest with provenance |
| The static demo | public | recorded answers only, synthetic numbers, `connect-src 'self'` |

Nothing calls into the machine from outside: no port forwarding, no tunnel, no webhook.

## 2. Accounts, sessions and roles

- **Accounts** are local ([ADR-0020](adr/0020-accounts-roles-audit.md)). Passwords are hashed with argon2id. An account
  locks after repeated failures (5 by default), for 15 minutes; an address that fails too often is throttled. Each
  attempt is counted before its hash is checked, so parallel guesses get no more tries than sequential ones.
- **Sessions** live on the server. The cookie `optlab_session` is `HttpOnly`, `SameSite=Strict` and `Secure` under
  TLS. Sessions expire, rotate at sign-in, end at sign-out, and all end when a user's roles change or the database is
  restored.
- **Roles**: viewer, structurer, trader, risk, quant, admin. Every route declares the roles it needs; the server
  refuses the rest, whatever the front shows. `test_api_matrix.py` checks every route against every role.
- **Audit**: every write (trades, fixings, scenarios, cost tables, accounts, the market in use) goes to an
  append-only audit log with its user. A signed scenario carries the authenticated user, never a typed name.

## 3. Requests

A guard in front of every route (`optlab.server.web.Guard`):

- **Host**: an allow-list (`127.0.0.1`, `localhost`, and the LAN names when the profile is on). A `Host` must be a plain
  `name[:port]`. This stops DNS rebinding.
- **Cross-site writes**: a write needs the session's `X-CSRF-Token`, an `Origin` of the desk itself, and a JSON body.
  Other content types get 415, a body over the limit 413, a request with both a length and a chunked encoding 400.
- **Validation**: every request model refuses unknown fields, NaN and infinity. Costly fields (paths, days, script
  length) are bounded, so every job is finite. A payoff script is parsed by a hand-written parser with bounded size,
  nesting and tree height: no `eval`, no `exec`. What a script observes is bounded too (10,000 dates in all, a window's
  fixings and span included), checked before its job is queued: a batch of paths holds a column per date.
- **No path from a request**: ids are checked against a pattern and looked up among stored records; no file name
  comes from a client.
- **Errors** are `problem+json` without a trace or a local path. A job's error message reaches its submitter and
  admins only. Another user's job is a 404.
- **External programs** run by their absolute path with fixed arguments (`optlab.core.programs`), never found in the
  working folder.

## 4. Headers

- The front: `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
  connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`. The build has no inline script and
  no inline style: Mantine's CSS variables are generated at build time, and nothing writes a `<style>` at run time.
- Standalone HTML reports (inline CSS and SVG, no script): `default-src 'none'; style-src 'unsafe-inline';
  img-src data:; frame-ancestors 'none'`.
- The API's interactive page (`/api/v1/docs`) is off by default (`OPTLAB_DOCS=true` turns it on), because Swagger UI
  runs a third-party script on the desk's origin. When on, the script is pinned (swagger-ui-dist 5.34.0), loaded with
  its integrity hash, under a CSP that allows that script and the page's own init only.
- Everywhere: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, a `Permissions-Policy` that denies
  camera, microphone and geolocation, `Cache-Control: no-store` on the API, HSTS under TLS.

## 5. Data

- Market data from Yahoo is for personal use. The lake, `data/` and the stores never enter the repository, an image or
  the demo; `.gitignore`, the images' allow-list `.dockerignore` and a test keep it so.
- Published results hold performances and calibrated parameters, never market levels.
- With the LAN profile on, the API's answers hold no local path of the machine, and raw option quotes and index levels
  are refused, unless the owner serves them on the LAN (`OPTLAB_LAN_DATA=true`): the LAN is then the owner's own
  devices, and the data's terms (personal use) stay the owner's to keep.
- The demo's data folder is built from fixed seeds and marked `synthetic` on every row. Its recorder refuses to write a
  file that holds a local path, and never records a login or a session token.

## 6. Containers and the host

The desk runs in Docker Engine inside WSL2 ([ADR-0009](adr/0009-containers-wsl2.md)):

- every published port is bound to `127.0.0.1`; the daemon listens on its unix socket only, and no container mounts it;
- the processes run as uid 10001, with a read-only root filesystem, every capability dropped, `no-new-privileges`, and
  memory and process limits;
- the api mounts the lake, the backups and the configuration read-only; only the worker sees the GPU;
- the images are built from base images pinned by digest and the lock installed with its hashes.

## 7. Supply chain and CI

- `uv.lock` and `package-lock.json` pin every dependency with its hash; installs are `--locked` (`npm ci`).
- Updates arrive as Dependabot pull requests, reviewed and merged by hand.
- CI runs on GitHub's hosted runners only. Workflows default to `contents: read`; only `release.yml`, on a tag, may
  push images. No workflow uses `pull_request_target`, and every action is pinned by its SHA.
- Each pull request is scanned: CodeQL (Python, TypeScript, C++), gitleaks over the whole history, `pip-audit`, Trivy
  on the tree and the images, and GitHub's dependency review.
- A release publishes its images on GHCR with an SBOM and a signed provenance attestation, and the deploy pulls them by
  digest ([ADR-0019](adr/0019-cd-ghcr-manual-deploy.md)).

## 8. Secrets

The repository holds none, and gitleaks checks every commit. Configuration lives in `<data>/config/optlab.env`, outside
the repository, with restricted rights. Test accounts get generated passwords that are never written into the
repository.
