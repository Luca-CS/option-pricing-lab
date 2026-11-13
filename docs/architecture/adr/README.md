# Architecture decision records

One file per decision, numbered, never rewritten: a decision that changes gets a new ADR that supersedes the old one,
with Luca's agreement (rule of the refactor, `HANDOFF_REFACTOR_1_ARCHITECTURE.md` §7). Format: context, decision,
consequences, alternatives. "Safety above all": when a nicer option and a safer one compete, the safer one wins and
the ADR says so.

Status of every ADR below: **Accepted** by Luca at the A0 checkpoint (7 November 2026). ADR-0017 keeps one open
item: the tolerance across platforms.

| ADR | Decision | Handoff §1 row |
|---|---|---|
| [0001](0001-self-hosted-target.md) | A self-hosted server on this PC, a few named users, restart-safe, no high availability | Cible, Priorité, Utilisateurs |
| [0002](0002-never-exposed.md) | The real instance is never exposed to the internet; LAN is an opt-in profile; the public face is a static demo | Exposition, Repo |
| [0003](0003-modular-monolith-uv-workspace.md) | A modular monolith in a `uv` workspace of PEP 420 namespace packages | Architecture |
| [0004](0004-dependency-direction.md) | Dependency direction from the measured import graph; an engine registry breaks the native cycle | Architecture |
| [0005](0005-api-v1-fastapi.md) | API v1 on FastAPI and Pydantic v2, versioned OpenAPI, SSE for jobs | API |
| [0006](0006-job-queue-sqlite-table.md) | A home-made job table on SQLite, not huey; the scheduler in the worker | Jobs |
| [0007](0007-storage-sqlite-alembic.md) | SQLite in WAL with SQLAlchemy Core and Alembic; the lake stays Parquet and DuckDB | Stockage |
| [0008](0008-native-engine-nanobind.md) | The CUDA/C++ engine as a nanobind wheel built by scikit-build-core | Moteur natif |
| [0009](0009-containers-wsl2.md) | Linux containers in WSL2 with Docker Engine and the NVIDIA Container Toolkit | Exécution |
| [0010](0010-one-cli.md) | One `optlab` CLI (Typer) replaces the scripts | CLI |
| [0011](0011-duplicates.md) | `space/` removed, `docs/lab.js` frozen until session 2 | Doublons |
| [0012](0012-settings.md) | One `Settings` object (pydantic-settings), prefix `OPTLAB_`, a file outside the repo | Configuration |
| [0013](0013-languages.md) | Python 3.14 and C++/CUDA; no Rust; TypeScript and React for the front | Langages |
| [0014](0014-tooling.md) | `uv` lock with hashes, ruff, pyright, pre-commit, pytest markers and xdist, import-linter | Outillage |
| [0015](0015-clean-up-policy.md) | Clean-up measured before and after, internal APIs breakable while everything stays recoverable | Clean |
| [0016](0016-calibrations-out-of-repo.md) | The two calibration JSON files leave the repository (superseded by 0023) | Résultats |
| [0017](0017-tests-and-golden-numbers.md) | Golden numbers, a CI under 5 minutes on a synthetic mini-lake, the full suite locally | Tests |
| [0018](0018-ci-hosted-runners.md) | GitHub Actions on hosted runners only, Linux and Windows, hardened workflows | CI |
| [0019](0019-cd-ghcr-manual-deploy.md) | Images on GHCR at each semver tag; a manual `optlab deploy` with rollback | CD |
| [0020](0020-accounts-roles-audit.md) | Local accounts (argon2id), cookie sessions, CSRF, roles checked on the server, audit log | Sécurité |
| [0021](0021-old-front-during-session-1.md) | The v0 front may break during session 1; it is kept, not served, in `legacy/` | Front pendant cette session |
| [0022](0022-lake-in-a-wsl-volume.md) | The containers' lake lives in a WSL volume (9P is 19 to 49 times slower); proposed in A7 | Exécution (ADR-0009's measurement) |
| [0023](0023-calibrations-compact-in-repo.md) | The calibrations stay in the repository, in compact JSON (112,346 lines become 30) | Résultats |

The pace (steps A0 to A9, one pull request each, Luca merges) is a process rule, not an architecture decision: it is in
the handoff, not here.
