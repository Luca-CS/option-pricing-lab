# ADR-0023: The calibrations stay in the repository, in compact JSON

- Status: Accepted, 13 November 2026

## Context

The two calibrations (`results/spx_calibration.json`, `results/simulated_calibration.json`) took 112,346 lines and
1.7 MB. Taking them out of the repository, to a release asset, was considered; but 28 test files read the SPX
calibration as their realistic market (structured, risk, native, server, marketdata), and so do the golden numbers
`presets_spx_*`. CI would then download an asset before every run, and a fresh clone before the desk starts.

What made them large was the writer, not the data: `json.dumps(..., indent=1)` put every number of the leverage grids
on its own line.

## Decision

- **Both files stay in `results/`, in compact JSON**: one line per top-level key, each value without whitespace
  (`optlab.server.tools.calibration.compact_json`, used by `optlab calib run` and the demo data). Floats are written by
  `repr`, so they read back to the bit; a diff still shows which part changed.
- Measured on the rewrite: 112,346 lines became 30, 1.71 MB became 0.93 MB. `json.loads` gives equal objects, the
  snapshot id of the SPX calibration is unchanged (it hashes the content, not the bytes), and the golden numbers that
  read it hold bit for bit.
- No release asset and no network in CI.

## Consequences

- A recalibration rewrites the same compact layout, so diffs stay small and readable by key.

## Alternatives considered

- **A release asset**, fetched by CI and by fresh clones: a public release to maintain and a network step before every
  test run. Rejected for the simpler option.
- **Rewrite the 28 test files on a synthetic calibration**: loses the realistic market they test against.
