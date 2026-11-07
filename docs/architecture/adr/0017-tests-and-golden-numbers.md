# ADR-0017: Golden numbers, a fast CI, the full suite on the machine

- Status: Accepted (A0 checkpoint, 7 November 2026); the tolerance across machines below is still open
- Handoff §1: Tests; §5

## Context

v0 has 831 tests (52 files), 32 to 33 minutes in full (`reports/refactor-baseline.md` §5), many of which read the
real lake or need the GPU and skip otherwise. Every stochastic pricer is validated against an independent reference
within 4 standard errors. The refactor moves every file: something must prove that no number changed.

## Decision

- **Golden numbers, recorded in A0 before any change** (`tests/golden/golden.py record`, later `optlab check golden
  --record`). Groups:
  - versioned in `tests/golden/` (synthetic inputs only, runnable in CI): closed forms, CRR, Heston Fourier; the
    Monte Carlo pricers of `products.py` under BS, Heston, LV and SLV on fixed seeds; the calibration pipeline on the
    simulated market; the presets quoted on that market (NumPy), forced models, the desk's cost lines, Greeks and a
    small book;
  - local, in `optlab-data/golden/`, never in git (they derive from personal-use data): presets on the SPX
    calibration (NumPy and GPU), objects derived from the lake at fixed dates, the explained P&L cascades of
    13/03/2020 and 04/11/2026, the showcase book's value and Greeks (GPU, Standard), its 1-day VaR (A0 and A9 only).
  - Large arrays are kept as a digest (sha256 of the float64 bytes, sum, 32 samples) so a golden file stays small.
- **Tolerances**: same platform and engine, equality to the last bit. Windows against Linux: measured in A0 and A7,
  then a relative tolerance is fixed (expected near 1e-12 for deterministic numbers, well under 0.01 SE for Monte
  Carlo), written in this ADR's follow-up, never widened silently. A golden difference blocks a PR unless it is a
  documented bug fix (number before and after).
- **Measured in A0 across platforms** (`reports/refactor-baseline.md` §7): closed forms and the Monte Carlo pricers
  with fixed inputs differ by at most 2.6e-14 relative (5.7e-14 SE) between Windows and Linux; the calibration pipeline
  amplifies those last bits (SLV leverage samples up to 1.7 %), and prices computed on a recalibrated market differ by up
  to 0.07 SE. Proposed tolerance across platforms: `rtol = 1e-12` for every group that takes fixed inputs
  (`closed_forms`, `mc_products`, `labjs`, and later the native engine's CPU groups); for the groups that recalibrate
  (`calibration_simulated`, `presets_simulated`, `structured_extra`), Monte Carlo values within 0.1 SE and fitted
  parameters within `rtol = 2e-2`, checked by a dedicated comparison, not bit for bit. Within one platform the rule
  stays equality to the last bit. To be confirmed at the A0 checkpoint.
- **Measured in A2 across machines of one platform**: the hosted Windows runner gave the versioned goldens bit for bit
  on PR #3 (Azure northcentralus) and not on PR #4 (westus2), with the same image (`windows-2025-vs2026` 20261103.298.1),
  the same lock and code that is bit for bit on this machine. The gaps have the size of the Windows-Linux ones:
  `mc_products` 2.2e-14 relative, the calibration's parameters up to 6.5e-3, the presets' control variate 3.7e-4
  relative on an SE, the forced SLV's termination probabilities up to 0.6 %. NumPy picks its SIMD kernels at start-up
  and the last bit of `exp` and `log` follows the kernel; the step now prints each runner's CPU and SIMD level. On the
  next run, a Windows runner on an AMD Zen 3 at X86_V3 (this machine, a Zen 2, runs X86_V3 too) gave all six groups bit
  for bit, recalibrations included, while the Linux runner (EPYC 7763, also X86_V3) differed as in A0 (another build of
  NumPy and of the C library). The failing Windows run did not record its CPU, so the CPU is the likely cause there, not
  a proven one. Either way "same platform" means **same machine**: bit for bit is the rule on this machine (each step's
  local check), and every hosted runner is treated as another platform. Until the tolerance above is confirmed, the CI
  blocks on the fixed-input groups at `rtol = 1e-12` on both systems and runs the recalibrating groups without
  blocking, the machine's CPU and SIMD level printed beside them.
- **CI under 5 minutes**, measured: `pytest -m "not slow and not gpu and not lake"` with xdist on a synthetic
  mini-lake generated deterministically by the tests (no binary fixture), plus the CI golden groups.
- **The full suite and the GPU on the machine**: `optlab check full` (the real lake, about 30 minutes) and `optlab check
  gpu`, at the end of every step, their results in the PR.
- The 817 tests stay green or are replaced by a named equivalent in the PR; no independent reference is lost; the
  stochastic ones stay at 4 SE.

## Consequences

- Golden files are part of the review: a changed file in a PR is a changed number.
- Personal-use data never reaches CI: the mini-lake is synthetic, and the local goldens stay local.

## Alternatives considered

- Snapshot testing of whole API responses: brittle on timings and ids; the golden recorder drops clocks and ids.
- Run the lake tests in CI on a sanitized lake extract: still derived from Yahoo data. Rejected (safety).
