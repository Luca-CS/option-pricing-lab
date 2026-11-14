# ADR-0014: Tooling

- Status: Accepted (A0 checkpoint, 7 November 2026)
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

## Implementation notes (A8)

- **ruff** (A8a): `S`, `B`, `UP` and `C901` at 15 are on. Each `noqa` says why the code is safe; external programs run
  by their absolute path (`optlab.core.programs`).
- **pyright** (A8b): 0 errors, measured on 13 November 2026 against a baseline of 1,491 recorded in A1.
  - **Standard mode everywhere:** `packages/*/src`, the tests, `tests/helpers` and `tests/golden`.
  - **Strict mode on the server's security and contract core**, the 16 modules listed in `pyproject.toml`: the guard
    and the problems (`web`), accounts and sessions (`security`), the audit log, schemas and settings, the job queue,
    its tasks and worker, the application and its OpenAPI document, the presenter, logs, backups and the network check.
  - **The rest of `optlab.server` stays in standard mode:** the services (`api`, `risk_api`), the routers, the
    reports, the schedules, the command line and its tools. Strict mode there measured 3,975 errors, almost all of the
    "unknown" kind: the services take and return JSON dicts, so going strict means describing them as TypedDicts.
    That is a decision of its own, not a cleanup.
  - **`pandas-stubs`** (dev group): pandas' inline annotations type a row of `itertuples` as a tuple and a log of a
    Series as an array. With the stubs, `marketdata` went from 262 to 84 errors before the fixes.
  - **`py.typed`** in every package (PEP 561): the packages are typed libraries, and pyright checks what they export.
  - **How the errors were fixed, without changing a computed value:**
    - overloads where the return type depends on an argument (`simulate`, `Book.price`, `native.simulate`);
    - protocols for what a model reads (`LocalVarSurface`, `LeverageFunction`);
    - `cast` where pandas or SciPy type less than they return;
    - a narrowing `assert` (with `noqa: S101` and its reason) where an invariant holds;
    - in the tests, `some(x)` and `desk_of(q)` (`tests/helpers/_quotes.py`) for the optional results a test arranged.
  - **The remaining ignores** have the form `# pyright: ignore[rule] (reason)`, like the `noqa`s. They cover:
    - variables bound with a mixer and read in its branch (`multiasset`);
    - pairs that exist exactly when the base's do (`costs`);
    - a mixin's `super()` (`lifecycle`);
    - `bool` as a `Real` through the registration in `numbers`.
  - **The baseline file** (`pyright-baseline.json`) is now empty: any new error fails CI.

## Consequences

- CI is reproducible from the lock; Dependabot proposes updates as PRs, never merged automatically.
- The formatting commit touches most files once; `git blame --ignore-revs-file` keeps blame useful.

## Alternatives considered

- mypy instead of pyright: slower on this code base, pyright's inference fits NumPy code better.
- Black + isort + flake8: ruff does all three.
