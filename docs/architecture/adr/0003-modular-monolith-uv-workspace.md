# ADR-0003: A modular monolith in a uv workspace of namespace packages

- Status: Accepted, 7 November 2026

## Context

The code was one package of about 30,000 lines of Python, with a CUDA engine beside it, a desk inside it, boundaries
that were only conventions, and two import cycles ([ADR-0004](0004-dependency-direction.md)). One person maintains
it on one machine: microservices would multiply deployments, network calls and failure modes for no gain.

## Decision

- **One repository, one `uv` workspace**, six installable distributions: `optlab-core`, `optlab-native`,
  `optlab-structured`, `optlab-marketdata`, `optlab-risk`, `optlab-server`, under `packages/<name>/src/optlab/<name>`,
  built by hatchling (scikit-build-core for the native one).
- **PEP 420 namespace `optlab`**: no `optlab/__init__.py`; each distribution owns `optlab.<name>`. Imports are
  absolute (`from optlab.core import Market`). `optlab.core`'s public API is listed in its `__all__`.
- Each package has its own `pyproject.toml`, minimal dependencies and extras (for example `marketdata[yahoo]`), and its
  tests in `packages/<name>/tests`, placed by the highest package they import. Fixtures shared across packages live in
  `tests/helpers`, on pytest's `pythonpath`; no test imports another test module.
- Processes are separated only where their nature differs: `api`, `worker`, `proxy` ([ADR-0009](0009-containers-wsl2.md)).

## Consequences

- Package boundaries are real: separate dependency lists, and import-linter contracts in CI.
- `optlab-core` installs and imports alone (NumPy and SciPy only).
- CI type-checks the native package without compiling it, thanks to a versioned stub (`_ext.pyi`); a test fails when
  the stub drifts from the module.
- On Windows, MSBuild's file tracker fails on paths above 260 characters, so scikit-build-core's `build-dir` stays
  short.

## Alternatives considered

- One distribution with subpackages: no enforced boundary, and every user installs DuckDB and FastAPI to use the
  closed forms.
- Separate repositories: versioning across repositories for one maintainer. Rejected.
- Microservices: rejected (context).
