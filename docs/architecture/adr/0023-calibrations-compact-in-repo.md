# ADR-0023: The calibrations stay in the repository, in compact JSON

- Status: Accepted (Luca, in the chat, 13 November 2026); supersedes ADR-0016
- Handoff §1: Résultats

## Context

ADR-0016 planned to take the two calibrations (`results/spx_calibration.json`, `results/simulated_calibration.json`:
112,346 lines, 1.7 MB) out of the repository, to a GitHub release and `optlab-data/archive/`, on the premise that no
test reads them. A8 found the opposite: 28 test files read the SPX calibration as their realistic market (structured,
risk, native, server, marketdata), and the golden numbers `presets_spx_*` too. Taking the files out would make the CI
download a public release asset before every run and a fresh clone fetch it before the desk starts.

What made them large is the writer, not the data: `json.dumps(..., indent=1)` puts every number of the leverage grids
on its own line.

## Decision

- **Both files stay in `results/`, written in compact JSON**: one line per top-level key, each value without
  whitespace (`optlab.server.tools.calibration.compact_json`, used by `optlab calib run` and the demo data). The
  floats are written by `repr`, so they read back to the bit; a diff still shows which part changed.
- Measured on the rewrite: 112,346 lines become 30, 1.71 MB become 0.93 MB. `json.loads` gives equal objects, the
  snapshot id of the SPX calibration is unchanged (`SPX-20261027T1337-4ad233c20f`: it hashes the content, not the
  bytes), and the golden numbers that read it (`presets_spx_numpy`, `presets_spx_gpu`) hold bit for bit. Only the
  `file_sha256` of a snapshot's provenance changes, as it should.
- No GitHub release, no `optlab calib fetch`, no network in the CI.

## Consequences

- The repository loses the 112,346 lines that dominated its line count; the history keeps the old layout.
- A recalibration rewrites the same compact layout, so diffs stay small and readable by key.

## Alternatives considered

- **ADR-0016 as written** (a release asset, fetched by the CI and by fresh clones): a public release to maintain, a
  network step before every test run, and the 28 test files depending on it. Rejected by Luca for the simpler option.
- **Rewrite the 28 test files on a synthetic calibration**: loses the realistic market they test against.
