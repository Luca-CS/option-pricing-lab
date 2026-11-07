# Refactor baseline: v0 measured before any change

Measured on 7 November 2026 on commit `4612e78` (`main` `9b15b54` + the risk engine of PR #1), Windows 11, Python
3.14.7, numpy 2.5.4, scipy 1.18.1, RTX 2070 Super (driver 617.42, CUDA 12.9). The same commands run again after A8 for
`reports/refactor.md`. Lines are physical lines of tracked files (`wc -l`), JSON excluded unless said.

## 1. Size

| Part | Lines |
|---|---|
| `optlab/*.py` (the core: models, market, pricers, calibration, `gpu.py`, `native.py`, `config`, `data`, `log`) | 2,470 |
| `optlab/structured` | 5,996 |
| `optlab/marketdata` | 3,864 |
| `optlab/risk` | 3,356 |
| `optlab/app` (Python) | 2,371 |
| `optlab/app/static` (JS, CSS, HTML) | 3,057 |
| `scripts/` | 1,982 |
| `space/` | 373 |
| `tests/` (52 test files, 3 helpers) | 9,278 |
| `cuda/` C++ and CUDA (`.cu`, `.cpp`, `.hpp`, `.cuh`, `.h`) | 3,275 (3,777 with CMake and docs) |
| `docs/` (GitHub Pages demo, `lab.js` 1,155) | 3,157 |
| `reports/*.md` | 861 |
| **JSON** (`results/`, 112,346 lines of it in the two calibrations) | 114,707 |

Python overall: 29,756 lines; without tests, 20,478 (`cuda/bench/bench_numpy.py` included).

## 2. Import graph

Package edges, module level / inside functions (AST over every `import`; `gpu.py` and `native.py` counted as
`native`). Two cycles: `core` <-> `native` and `structured` <-> `marketdata` (ADR-0004).

| From -> to | module level | lazy |
|---|---|---|
| structured -> core | 36 | 4 |
| marketdata -> core | 23 | 4 |
| marketdata -> structured | 10 | 7 |
| structured -> marketdata | 3 | 4 |
| structured -> native | 1 | 0 |
| core -> native | 0 | 2 |
| native -> core | 5 | 1 |
| native -> structured | 0 | 3 |
| risk -> structured / marketdata / core / native | 18 / 1 / 6 / 0 | 8 / 4 / 4 / 1 |
| app -> core / marketdata / native / risk / structured | 10 / 5 / 1 / 5 / 19 | 0 / 5 / 0 / 4 / 0 |

## 3. Complexity

- **ruff C901** (mccabe, threshold 15; nested functions counted apart): 7 functions above 15: `app/server.py
  make_handler` 37, `risk/engine.py run_group` 36, `structured/compiler.py compile_sheet` 30, `risk/greeks.py plan` 29,
  `structured/costs.py desk_price` 25, `tests/_structured_refs.py autocall_reference` 23, `structured/greeks.py
  greeks` 16.
- **radon 6.0.1** (`radon cc`, nested blocks included in their parent, boolean operators counted): 1,300 blocks, mean
  complexity 4.31; ranks A 1,020, B 185, C 70, D 14, E 5, F 6; **44 blocks above 15**, 95 above 10.

| Package | blocks | mean | max | above 15 |
|---|---|---|---|---|
| core (`optlab/*.py`) | 172 | 2.90 | 26 | 4 |
| app | 158 | 4.34 | 26 | 5 |
| marketdata | 264 | 3.53 | 40 | 2 |
| risk | 205 | 5.66 | 78 | 12 |
| structured | 420 | 4.71 | 80 | 18 |
| scripts | 71 | 4.23 | 16 | 3 |
| space | 10 | 4.80 | 14 | 0 |

Largest (radon): `structured/costs.py desk_price` 80, `risk/engine.py run_group` 78, `risk/pnl.py explain_day` 62,
`structured/compiler.py compile_sheet` 47, `risk/greeks.py plan` 45, `structured/portfolio.py price` 41,
`marketdata/replay.py replay` 40, `risk/greeks.py compute` 38, `structured/pricer.py price_many` 38,
`structured/quote.py quote` 35.

## 4. Duplication, dead code, lint, types

- **Duplication** (pylint 4.0.2 `duplicate-code`, at least 8 similar lines, imports and docstrings ignored): 1 block,
  the merge of fixing histories in `risk/engine.py` and `structured/portfolio.py` (9 lines).
- **Dead code** (vulture 2.14): 64 findings at 60 % confidence or more, 2 at 90 % or more (`structured/costs.py:365`
  `base_requests`, `structured/qmc.py:76` `dtype`). Most findings below 90 % are attributes read by the standard
  library (`BaseHTTPRequestHandler` class attributes, `row_factory`); crossed with coverage in A8.
- **ruff 0.14.4**, current configuration (default rules, line length 120): 10 findings (5 unused imports, 3 lambda
  assignments, 2 unused variables). `ruff format --check`: 169 files would be reformatted, 18 already formatted.
- **pyright 1.1.407**, standard mode, `optlab` and `scripts`, 128 files: **766 errors**, 2 warnings. By package: core
  50, app 88, marketdata 263, risk 101, structured 165, scripts 99. By rule: `reportArgumentType` 334,
  `reportAttributeAccessIssue` 212, `reportReturnType` 45, `reportOptionalMemberAccess` 39, `reportOperatorIssue` 30,
  `reportCallIssue` 27. Most come from pandas and NumPy typing (ndarray versus float, DataFrame attribute access).

## 5. Tests and coverage

- Full suite (`pytest`, NumPy engine, the real lake, the GPU library absent in the run's worktree): *pending*.
- Coverage (`pytest-cov` 7.0.0, line coverage of `optlab`): *pending*.
- CI: none in v0.

## 6. Business times (reference, Windows native, GPU warmed)

Run in sequence on an otherwise idle machine, after a GPU warm-up of 90 s (499 Phoenix quotes on the GPU), with the
CUDA library built from this commit (`cmake --build build --config Release`, 65 s). The showcase runs go through the
desk's own routes (`Desk`, `RiskApi`) on a copy of `optlab-data/showcase`, market `SPX-20261104T1600-8b7fa10118`,
engine `gpu`, `--workers 6`. The SM clock sampled between steps read 300 to 765 MHz (maximum 2,145): these workloads
alternate CPU and GPU, so the card never stays at full clock; the numbers are what the desk gets.

| Measure | Time | Result (same as the golden files) |
|---|---|---|
| Six presets quoted on the SPX calibration, GPU, 100,000 paths | 2.1 s in all | `presets_spx_gpu` |
| `structured_examples.py --quick` (NumPy, 20,000 paths) | 29 s | BRC quote 0.94 s, reverse convertible 0.49 s, Phoenix 24M by model 0.0 to 0.7 s |
| Showcase book, value with delta and vega (Standard) | 11.6 s | -56,390,954 ± 10,087; reserves left 571,911; net -56,962,865 |
| Showcase Greeks (`POST /api/risk/run`, Standard, node vegas for SPX) | 147.4 s | `showcase_book` (123 s measured by the risk session on 7 November) |
| Showcase VaR (`POST /api/risk/var`: Taylor-check Greeks, stressed window search, three horizons; 5 processes on the GPU) | 768.8 s | 1 day 573,576 (SE 3,275), ES 643,073, on 27/05/2025; 10 days 1,768,567; stressed 1,508,111 (29/03/2019 - 07/04/2020): equal to the last digit to `reports/risk.md` (620 s in script, 13 min 12 s from the desk button) |
| `bench_accel.py` (1,000,000 paths, batches of 50,000) | 277 s | table below |

`bench_accel.py`, wall time per quote of 1,000,000 paths:

| Case | NumPy, 1 worker | NumPy, 6 workers | CPU engine | GPU engine |
|---|---|---|---|---|
| Phoenix 2y, LV | 34.10 s | 13.23 s | 2.98 s | 0.33 s |
| Phoenix 2y, SLV | 26.23 s | 6.06 s | 3.69 s | 0.35 s |
| BRC 1y daily, LV | 23.52 s | 7.45 s | 1.86 s | 0.24 s |
| Phoenix 2y worst-of 3, LV | 100.09 s | 38.58 s | 5.96 s | 0.54 s |
| SLV leverage 1y, 200,000 particles | 4.45 s | | 1.36 s | 0.24 s |

NumPy and the native engines draw different normals, so their prices differ within the SE (for example 99.0067 against
99.0137 ± 0.0120); the CPU and GPU engines agree to the last digit shown.

## 7. Windows against Linux (golden numbers)

The versioned groups recomputed under Linux (WSL2 Ubuntu 24.04, kernel 6.18, Python 3.14.7, the same numpy 2.5.4,
scipy 1.18.1, pandas 3.1.0 wheels, scipy-openblas on both) and compared with the Windows recording
(`golden.py check --dump`):

| Group | Differing floats | Max relative gap | Max gap of a Monte Carlo value, in its SE |
|---|---|---|---|
| `closed_forms` | 5 (Heston Fourier only) | 2.6e-14 | — |
| `mc_products` (fixed models, including an SLV leverage of 20,000 particles) | 19 | 1.7e-14 | 5.7e-14 |
| `calibration_simulated` | 566 | 1.7e-2 (SLV leverage samples); SVI parameters 1e-8 to 1e-6 | — |
| `presets_simulated` (on that calibration) | 28 | 4.9e-5 | 7.5e-3 |
| `structured_extra` (on that calibration) | 172 | 1.65e-2 (the SLV provision line) | 7.0e-2 |

Reading: the closed forms and every Monte Carlo pricer with fixed inputs differ only in the last bits of libm
(1e-14). The calibration pipeline amplifies those bits: the SVI and Heston least-squares fits move by 1e-8 to 1e-6, and
the SLV leverage of the particle method (40,000 particles in this golden) moves by up to 1.7 % at sampled points; prices
computed on that calibration then differ by up to 0.07 SE (the SLV provision of a Phoenix: 0.326 against 0.321 % of
notional, SE 0.075). This is above the 0.01 SE the handoff expected for Monte Carlo numbers; it comes from the
recalibration on each platform, not from the pricers. The tolerance across platforms is proposed in ADR-0017 and fixed at
the A0 checkpoint.

## 8. How to reproduce

```bash
uvx ruff@0.14.4 check optlab scripts tests space cuda/bench --select C901 --config 'lint.mccabe.max-complexity=15'
uvx --from radon==6.0.1 radon cc -a optlab scripts space -j
uvx vulture@2.14 optlab scripts space --min-confidence 60
uvx --from pylint==4.0.2 pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 --ignore-imports=y --ignore-docstrings=y optlab scripts
uvx pyright@1.1.407 --pythonpath .venv/Scripts/python.exe --outputjson optlab scripts
python tests/golden/golden.py check [--local]
```
