# ADR-0010: One `optlab` command line

- Status: Accepted (A0 checkpoint, 7 November 2026)
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

## Implementation (A7)

- `optlab.server.cli`, Typer 0.27.3 (it carries its own copy of Click: no Click in the runtime closure; `shellingham`
  is the only new package of the lock), console script `optlab` of `optlab-server`. `python -m optlab.server`,
  `python -m optlab.server.worker`, `.users` and `.openapi` stay, as aliases of `serve`, `worker`, `users` and `api`:
  one parser per command, never two.
- The bodies of the v0 scripts moved (`git mv`, history kept) to `optlab.server.tools`, each a function with keyword
  parameters that the command calls; nothing runs at import, so `optlab --help` needs neither matplotlib nor the lake.
  Their paths come from `Settings` (new fields `reports`, the folder the report commands write, and `market`, the
  snapshot `serve` starts on). The tests that ran a script by its path run the command in a fresh process instead
  (`test_cli.py`), and the table of `feature-inventory.md` §3 is checked against the command tree.
- `--data DIR` before any command sets `OPTLAB_DATA` for the process and the ones it starts. A library error ends
  the command with its message and exit code 1 (no trace); a usage error with exit code 2.
- `--as-job` (lake update, risk eod, backfill, var, ladder) submits to the database's queue for the worker that
  serves it; `optlab jobs list|show|wait|cancel` follows the queue. Commands without it run in their own process.
- Kept out: `deploy` (A9, with the releases), `demo build-front` and the rest of `demo` (session 2). Found on the way:
  since A4, `lake update`, `derive`, `fx-check` and `surface-test` failed without `--lake` (`Lake(None)`); the
  commands take the settings' lake.
