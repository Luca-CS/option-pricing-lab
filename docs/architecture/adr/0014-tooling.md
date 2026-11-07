# ADR-0014: Tooling

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: Outillage

## Context

v0 has no lockfile, an unpinned `requirements.txt`, a minimal `pyproject.toml`, ruff configured for line length 120
but not run in CI, no type checker, no pre-commit hook.

## Decision

- **uv**: workspace, `uv.lock` with hashes, `uv sync --locked` in CI and in images.
- **ruff** for lint and format (line length 120 kept). The formatting is applied once in a mechanical commit,
  listed in `.git-blame-ignore-revs`. Rules: the current set plus `C901` (complexity 15), `S` (bandit), `B`, `UP`, `I`.
- **pyright** in standard mode everywhere, strict on `optlab.server` and on the public APIs; a baseline of the
  existing errors is recorded in A1 and only goes down.
- **pre-commit**: ruff, ruff-format, gitleaks, end-of-file and large-file checks.
- **pytest** with markers `slow`, `gpu`, `lake`, `native`, `node` and `pytest-xdist`; `--import-mode=importlib`.
- **import-linter** for the contracts of ADR-0004.

## Consequences

- CI is reproducible from the lock; Dependabot proposes updates as PRs, never merged automatically.
- The formatting commit touches most files once; `git blame --ignore-revs-file` keeps blame useful.

## Alternatives considered

- mypy instead of pyright: slower on this code base, pyright's inference fits NumPy code better.
- Black + isort + flake8: ruff does all three.
