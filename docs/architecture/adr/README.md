# Architecture decision records

One file per decision: its context, the decision, its consequences and the alternatives considered. A decision that
changes gets a new record that supersedes the old one; numbers are never reused. When a nicer option and a safer one
compete, the safer one wins and the record says so.

| ADR | Decision |
|---|---|
| [0001](0001-self-hosted-target.md) | A self-hosted server on one workstation, a few named users, restart-safe, no high availability |
| [0002](0002-never-exposed.md) | The desk is never exposed to the internet; the LAN is an opt-in profile; the public face is a static demo |
| [0003](0003-modular-monolith-uv-workspace.md) | A modular monolith in a `uv` workspace of PEP 420 namespace packages |
| [0004](0004-dependency-direction.md) | Dependency direction from the measured import graph; an engine registry breaks the native cycle |
| [0005](0005-api-v1-fastapi.md) | API v1 on FastAPI and Pydantic v2, a versioned OpenAPI document, SSE for jobs |
| [0006](0006-job-queue-sqlite-table.md) | A home-made job table on SQLite, not huey; the scheduler in the worker |
| [0007](0007-storage-sqlite-alembic.md) | SQLite in WAL with SQLAlchemy Core and Alembic; the lake stays Parquet and DuckDB |
| [0008](0008-native-engine-nanobind.md) | The C++/CUDA engine as a nanobind wheel built by scikit-build-core |
| [0009](0009-containers-wsl2.md) | Linux containers in WSL2 with Docker Engine and the NVIDIA Container Toolkit |
| [0010](0010-one-cli.md) | One `optlab` command line (Typer) |
| [0011](0011-duplicates.md) | One front; the pricing lab stays in JavaScript |
| [0012](0012-settings.md) | One `Settings` object (pydantic-settings), prefix `OPTLAB_`, a file outside the repository |
| [0013](0013-languages.md) | Python 3.14 and C++/CUDA; no Rust; TypeScript and React for the front |
| [0014](0014-tooling.md) | `uv` lock with hashes, ruff, pyright, pre-commit, pytest markers and xdist, import-linter |
| [0017](0017-tests-and-golden-numbers.md) | Golden numbers, a fast CI on a synthetic mini-lake, the full suite on the machine |
| [0018](0018-ci-hosted-runners.md) | GitHub Actions on hosted runners only, Linux and Windows, hardened workflows |
| [0019](0019-cd-ghcr-manual-deploy.md) | Images on GHCR at each version tag; a manual deploy with rollback |
| [0020](0020-accounts-roles-audit.md) | Local accounts (argon2id), cookie sessions, CSRF, roles checked on the server, an audit log |
| [0022](0022-lake-in-a-wsl-volume.md) | The containers' lake lives in a WSL volume (9P is 19 to 49 times slower) |
| [0023](0023-calibrations-compact-in-repo.md) | The calibrations stay in the repository, in compact JSON |
| [0024](0024-front-end-stack.md) | The front's stack (Vite, React, Mantine, TanStack), and how it keeps the strict CSP |
| [0025](0025-hedges-are-trades.md) | Hedges are trades: instruments in the same table and book, linear ones in closed form |
| [0026](0026-issuer-calls-by-lsm.md) | Issuer calls in the payoff language: a policy fitted by regression on a pilot, applied out of sample, a dual bound |
| [0027](0027-hybrid-models-as-provisions.md) | Hybrid models as provisions against local vol: stochastic rates, local correlation as a lambda shift |
