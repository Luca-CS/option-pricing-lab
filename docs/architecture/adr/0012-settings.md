# ADR-0012: One Settings object

- Status: Accepted (A0 checkpoint, 7 November 2026)
- Handoff §1: Configuration; §4.4

## Context

v0 reads paths in `optlab/config.py` (`OPTLAB_HOME`, `OPTLAB_DATA`, `OPTLAB_STORE`, `OPTLAB_LAKE`, and a guess of
`optlab-data` beside the main checkout when the code runs in a worktree), `OPTLAB_ENGINE` and `OPTLAB_CUDA_LIB`
elsewhere, and command-line options in each script. `REPO_ROOT` is used to find `results/`.

## Decision

- **One `Settings` class (pydantic-settings) in `optlab.server.settings`**, prefix `OPTLAB_`: paths (`DATA`, store,
  lake, front build, logs, backups), network (`HOST` = `127.0.0.1` by default, `PORT`), engine, workers, precision
  presets, security (session lifetime, lockout, the LAN profile flag).
- Read from the environment, then from `optlab-data/config/optlab.env` (outside the repository, restricted rights).
  No hard-coded path anywhere else: domain packages receive paths as arguments.
- Validation at start: an unknown key, a host other than `127.0.0.1` without the `lan` flag, or a missing folder stops
  the process with a clear message.

## Implementation (A4)

- `optlab.server.settings.Settings` (pydantic-settings): paths (`data`, `store`, `database`, `lake`, `results`,
  `calibration`, `raw`, `logs`, `backups`), network (`host`, `port`, `lan`), computing (`engine`, `workers`). Each path
  defaults from the fields validated before it; `data` is `OPTLAB_DATA`, else the development default kept from v0
  (`optlab-data` beside the main checkout), else the repository's `data/`. Security knobs join with A5.
- Order: the environment, then `<data>/config/optlab.env`, then the command line (options left at their default
  change nothing). An unknown key in the env file stops the start; so does a host beyond this machine without
  `OPTLAB_LAN=true`.
- The domain packages lost every default path and default market: `Lake(root)`, `PricingContext.from_calibration(path)`,
  `static_prior(path)`, the stores, `Dolt.from_environment(data)`; `quote`, `Book.price` and `BasketContext.with_spx`
  need a context. `optlab.core.config` keeps only `DEFAULT_SEED`. Tests take their paths from `tests/helpers/_paths.py`.

## Consequences

- The worktree guess of `config.py` disappears; `OPTLAB_DATA` is set explicitly (the dev default stays the sibling
  `optlab-data` folder, written in the example env file).
- Future secrets go to `optlab-data/config/`, never into git (gitleaks guards it).

## Alternatives considered

- Keep `config.paths()`: scattered reads, no validation, no single place to document.
- TOML config file in the repo: tempting for defaults, but a file in the repo invites local edits with paths and
  secrets. The env file outside the repo is safer.
