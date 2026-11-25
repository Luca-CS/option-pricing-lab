# ADR-0014: Tooling

- Status: Accepted, 7 November 2026

## Context

The first version had no lockfile, an unpinned `requirements.txt`, ruff configured but not run in CI, no type checker
and no pre-commit hook.

## Decision

- **uv**: the workspace, `uv.lock` with hashes, `uv sync --locked` in CI and in the images.
- **ruff** for lint and format (line length 120), with `C901` (complexity 15), `S` (bandit), `B` (bugbear), `UP`
  (pyupgrade) and `I`. The formatting commit is listed in `.git-blame-ignore-revs`.
- **pyright**: standard mode everywhere (the tests included), strict on the server's security and contract core (the
  modules listed in `pyproject.toml`: the guard, accounts and sessions, the audit log, schemas and settings, the job
  queue and worker, the application and its OpenAPI document, the presenter, logs, backups, the network check). Zero
  errors; any error fails CI.
- **pre-commit**: ruff, ruff-format, gitleaks, end-of-file and large-file checks.
- **pytest** with the markers `slow`, `gpu`, `lake`, `native`, `node`, and `pytest-xdist`.
- **import-linter** for the contracts of [ADR-0004](0004-dependency-direction.md).
- **Conventions**: a `noqa` or a `# pyright: ignore[rule]` says why, in parentheses after it; a narrowing `assert` only
  for an invariant that already holds; external programs run by their absolute path (`optlab.core.programs`);
  `py.typed` in every package; `pandas-stubs` in the dev group.

## Consequences

- CI is reproducible from the lock. Dependabot proposes updates as pull requests, never merged automatically.
- The rest of `optlab.server` (the services, the routers, the reports, the CLI) stays in standard mode: strict mode
  there would mean describing every JSON payload as a TypedDict, a decision of its own.

## Alternatives considered

- mypy: slower on this code base; pyright's inference fits NumPy code better.
- Black, isort and flake8: ruff does all three.
