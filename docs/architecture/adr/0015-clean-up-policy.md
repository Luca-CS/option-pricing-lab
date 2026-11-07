# ADR-0015: Clean-up, measured before and after

- Status: Accepted (A0 checkpoint, 7 November 2026)
- Handoff §1: Clean; §4.11

## Context

Feature freeze: nothing new on the business side. The code grew feature by feature; the axes to improve are dead
code, duplication, complexity, coverage and typing. No numeric target is imposed.

## Decision

- Internal APIs may break as long as everything stays recoverable (tag `pre-refactor`, backups, golden numbers).
- Measure in A0 (`reports/refactor-baseline.md`) and after A8 (`reports/refactor.md`), with the same commands: lines
  per package without JSON, complexity (ruff C901, radon), duplication, dead code (vulture crossed with coverage),
  coverage (pytest-cov), pyright errors, test and CI durations, business times (quote, showcase Greeks, VaR).
- Actions in A8: delete dead code, factor real duplications without gratuitous abstraction, split functions above a
  complexity of 15, pyright clean (standard everywhere, strict on `server` and the public APIs), consistent names,
  docstrings on public APIs, structured logs, tests reorganised by package without losing one independent reference.
- No performance regression above 10 % without a written justification.
- A bug found on the way is fixed with its test and written in the journal (handoff) and in the PR, with the number
  before and after.

## Consequences

- Every claim of the final report is a measured number with the command that produced it.

## Alternatives considered

- Coverage or complexity targets: a target invites gaming; the before/after table shows the direction.
