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

*Pending*: quote (`structured_examples.py --quick`), showcase book valuation and Greeks (GPU, Standard), 1-day VaR of
the showcase, `bench_accel.py`.

## 7. How to reproduce

```bash
uvx ruff@0.14.4 check optlab scripts tests space cuda/bench --select C901 --config 'lint.mccabe.max-complexity=15'
uvx --from radon==6.0.1 radon cc -a optlab scripts space -j
uvx vulture@2.14 optlab scripts space --min-confidence 60
uvx --from pylint==4.0.2 pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 --ignore-imports=y --ignore-docstrings=y optlab scripts
uvx pyright@1.1.407 --pythonpath .venv/Scripts/python.exe --outputjson optlab scripts
python tests/golden/golden.py check [--local]
```
