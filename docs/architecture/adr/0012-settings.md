# ADR-0012: One Settings object

- Status: Accepted, 7 November 2026

## Context

Paths, engine choices and network options were read in several places: a configuration module that guessed a data
folder, environment variables read where needed, and command-line options in each script.

## Decision

- **One `Settings` class (pydantic-settings) in `optlab.server.settings`**, prefix `OPTLAB_`: paths (`data`, `store`,
  `database`, `lake`, `results`, `calibration`, `raw`, `logs`, `backups`, `frontend`), network (`host`, `port`, `lan`),
  computing (`engine`, `workers`), security (session lifetime, lockout).
- The order: the environment, then `<data>/config/optlab.env` (outside the repository, restricted rights), then the
  command line. `data` is `OPTLAB_DATA`, else `optlab-data` beside the checkout.
- Validation at start: an unknown key in the env file, or a host beyond this machine without `OPTLAB_LAN=true`, stops
  the process with a clear message.
- No default path anywhere else: the domain packages receive their paths as arguments (`Lake(root)`,
  `PricingContext.from_calibration(path)`, the stores). `optlab.core.config` keeps only `DEFAULT_SEED`.

## Consequences

- One place to document and validate the configuration. Tests take their paths from `tests/helpers/_paths.py`.
- Secrets, if any, go to `<data>/config/`, never into git (gitleaks guards it).

## Alternatives considered

- Scattered reads: no validation, no single place to document.
- A TOML file in the repository: tempting for defaults, but a file in the repository invites local edits with paths
  and secrets. The env file outside it is safer.
