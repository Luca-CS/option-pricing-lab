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

## Consequences

- The worktree guess of `config.py` disappears; `OPTLAB_DATA` is set explicitly (the dev default stays the sibling
  `optlab-data` folder, written in the example env file).
- Future secrets go to `optlab-data/config/`, never into git (gitleaks guards it).

## Alternatives considered

- Keep `config.paths()`: scattered reads, no validation, no single place to document.
- TOML config file in the repo: tempting for defaults, but a file in the repo invites local edits with paths and
  secrets. The env file outside the repo is safer.
