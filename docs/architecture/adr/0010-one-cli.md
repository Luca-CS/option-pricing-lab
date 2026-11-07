# ADR-0010: One `optlab` command line

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: CLI; §4.6

## Context

v0 has 19 scripts in `scripts/` plus `cuda/bench/bench_numpy.py` and `python -m optlab.app`, each with its own
`argparse`, `sys.path` manipulation and defaults (inventory §3).

## Decision

- **One CLI, `optlab`, on Typer**, in `optlab.server.cli` (entry point of `optlab-server`):
  - operation: `serve`, `worker`, `users`, `backup`, `restore`, `deploy`, `migrate`, `check fast|full|gpu|golden`;
  - domain and data: `lake ...`, `risk ...`, `calib ...`, `structured ...`;
  - tools: `bench ...`, `api export`, `demo ...` (the last one for session 2).
- Every script maps to a subcommand in `feature-inventory.md` §3, with its options kept or their removal noted, and
  is deleted once the subcommand exists (A7).
- Long commands report progress and can run as jobs of the worker (`--as-job`) when the server is up.

## Consequences

- One `--help` tree; one place for paths (`Settings`) and logging.
- Typer adds a dependency to `optlab-server` only.

## Alternatives considered

- **argparse subcommands**: no dependency, more boilerplate for 40 subcommands.
- **Click**: Typer is Click with type hints; same runtime.
