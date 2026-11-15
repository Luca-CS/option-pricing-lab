# ADR-0016: The calibration JSON files leave the repository

- Status: Accepted (A0 checkpoint, 7 November 2026); **superseded by ADR-0023** (A8: the files stay, in compact JSON)
- Handoff §1: Résultats

## Context

`results/spx_calibration.json` and `results/simulated_calibration.json` are 112,346 lines and 1.7 MB: the SLV leverage
at every time step, one number per line. They inflate every line count, every diff of a recalibration, and the clone. The desk loads the SPX
one by default (`structured/context.py: DEFAULT_CALIBRATION`), `scripts/make_report.py` and `build_widget_data.py`
read them.

## Decision

- Both files are archived as assets of a GitHub release (`calibrations-2026-11`) and copied to
  `optlab-data/archive/`, with their sha256 in the release notes and in `reports/calibration.md`.
- `optlab calib run --source simulated|spx` regenerates them into `optlab-data/archive/` (the SPX one needs a snapshot
  from `optlab calib download-spx`).
- `OPTLAB_CALIBRATION` points the desk at a file; the default is the archived SPX calibration. `optlab calib fetch`
  downloads the release asset when the archive folder is empty.
- The small result files stay versioned (`pricing_examples.json`, `replay_*.json`, `surface_*.json`).
- Tests never read the archived files: the CI golden numbers build their market from synthetic inputs
  (`tests/golden/golden.py`); the local ones read the archive.

## Consequences

- The working tree loses 1.7 MB and 112,346 lines; history keeps the old versions.
- A fresh clone needs one command (`optlab calib fetch` or `calib run`) before the desk starts on the SPX market.

## Alternatives considered

- Git LFS: an extra tool and storage quota for two files. Rejected.
- Keep them: rejected by the decision of 7 November.
