# ADR-0010: One `optlab` command line

- Status: Accepted, 7 November 2026

## Context

The first version had nineteen scripts, a benchmark script and a desk module, each with its own `argparse`, its own
`sys.path` tricks and its own defaults.

## Decision

- **One CLI, `optlab`, on Typer**, in `optlab.server.cli` (the console script of `optlab-server`):
  - operation: `serve`, `worker`, `users`, `backup`, `restore`, `migrate`, `jobs`, `version`;
  - domain and data: `lake ...`, `risk ...`, `calib ...`, `structured ...`;
  - tools: `bench ...`, `api export`, `demo ...`.
- The bodies of the commands live in `optlab.server.tools`, one function each with keyword parameters; nothing runs
  at import, so `optlab --help` needs neither matplotlib nor the lake. Their paths come from `Settings`.
- `--data DIR` before any command sets `OPTLAB_DATA` for the process and the ones it starts. A library error ends the
  command with its message and exit code 1 (no trace); a usage error with exit code 2.
- Long commands can run as jobs of the worker (`--as-job`); `optlab jobs list|show|wait|cancel` follows the queue.
- One way in: `optlab ...`, or `python -m optlab.server.cli ...` where the console script is not on the PATH.

## Consequences

- One `--help` tree; one place for paths (`Settings`) and logging.
- Typer is a dependency of `optlab-server` only (it carries its own copy of Click).
- `test_cli.py` runs the commands in fresh processes and checks the command tree against
  [feature-inventory.md](../feature-inventory.md) §3.

## Alternatives considered

- **argparse subcommands**: no dependency, more boilerplate for about 40 subcommands.
- **Click**: Typer is Click with type hints; the same runtime.
