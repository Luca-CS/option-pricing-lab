# The architecture refactor, measured before and after

**Before** is [refactor-baseline.md](refactor-baseline.md): v0 on 7 November 2026 (commit `4612e78`).

**After** is measured on 15 November 2026, on the A8 branches (`refactor/a8-types` and the docs of A8c), with:

- the same machine: Windows 11, RTX 2070 Super;
- the same Python 3.14.7, numpy 2.5.4 and scipy 1.18.1;
- the same tool versions as the baseline.

The refactor ran in ten steps, A0 to A9, one pull request each. It kept the numbers: every golden group recorded at the start is
recomputed bit for bit at the end.

| | Before (v0) | After (A8) |
|---|---|---|
| Layout | one package `optlab` with `app/`, `scripts/`, `space/`, `cuda/` | a uv workspace of six distributions sharing the namespace `optlab` |
| Import cycles between packages | 2 (`core` <-> `native`, `structured` <-> `marketdata`) | 0, checked by `lint-imports` |
| Desk | a `http.server` handler and a page | FastAPI API v1, accounts and roles, CSRF, audit log, job queue and worker, one SQLite database, OpenAPI contract |
| Functions above complexity 15 (ruff) | 7 | 0, checked by ruff |
| pyright errors (standard) | 766 (v0's files) | 0, strict on the server's security core |
| Tests | 831 | 941 |
| Line coverage | 92.6 % | 93.0 % |
| CI | none | about 4 minutes a pull request, Windows and Linux |
| Golden numbers | | 14 groups recomputed bit for bit (the GPU's within its stated tolerance) |

## 1. Size

Physical lines of tracked files. A1 ran `ruff format` once over everything, which splits long lines: lines grew without any
code being added, so sizes are comparable in order of magnitude only.

| Part | Before | After |
|---|---|---|
| core (`optlab/*.py` before; `optlab.core` and the Python of `optlab.native` after) | 2,470 | 2,265 + 773 |
| `optlab.structured` | 5,996 | 7,741 |
| `optlab.marketdata` | 3,864 | 4,875 |
| `optlab.risk` | 3,356 | 4,207 |
| `optlab/app` (Python) and `scripts/` before; `optlab.server` after | 2,371 + 1,982 | 11,967 |
| The v0 page (`app/static` before, kept in `legacy/desk-static/` after) | 3,057 | 3,061 |
| `space/` | 373 | 0 (left the repository) |
| Tests (`tests/` before; `packages/*/tests` and `tests/` after) | 9,278 in 52 files | 14,725 in 71 files |
| C++ and CUDA | 3,275 | 3,050 |
| `docs/` demo (JavaScript, HTML, CSS) | 3,157 | 4,644 |
| JSON | 114,707 | 16,612 (the calibrations in compact JSON, ADR-0023) |

`optlab.server` holds the API v1 that v0 did not have: 7 routers, the schemas, the security, the audit log, the job queue and
its worker, the schedules, the persistence with its migrations, the backups, the command line, and the bodies of v0's
scripts. Python overall goes from 29,756 lines to 46,598, of which 31,788 are the library.

## 2. Import graph

| From -> to | module level | inside functions | `TYPE_CHECKING` only |
|---|---|---|---|
| native -> core | 6 | 1 | 0 |
| native -> structured (the basket types) | 0 | 7 | 1 |
| structured -> core | 38 | 7 | 0 |
| marketdata -> core | 34 | 4 | 0 |
| marketdata -> structured | 12 | 7 | 0 |
| risk -> core / structured / marketdata | 8 / 19 / 5 | 6 / 9 / 3 | 0 |
| server -> core / structured / marketdata / risk | 28 / 38 / 42 / 6 | 9 / 0 / 17 / 7 | 0 |

Every edge goes down the layers of ADR-0004 (`server > risk > marketdata > structured > core`, `native` beside them). The two
cycles of v0 are gone, and `lint-imports` keeps them out: `core` no longer imports the engine, `structured` no longer
imports `marketdata`. Only the server imports `optlab.native`; the others ask `optlab.core.engines` for an engine by name.

## 3. Complexity

- **ruff C901** (threshold 15): 7 functions above it before, none after. The rule is on in CI.
  - The largest, `app/server.py make_handler` (37), left with the v0 server (A5).
  - The others were split into named steps: `run_group` 36, `compile_sheet` 30, the Greeks' `plan` 29, `desk_price` 25,
    the reference `autocall_reference` 23, `greeks` 16, `quote` 16.
  - Each split keeps the same operations in the same order. It was checked by comparing outputs before and after:
    - 30 preset scripts: identical text;
    - `plan`: identical shocks and measures over 768 combinations;
    - `run_group`: identical values, P&L, measures, SEs, cash and pairs, on four groups;
    - and the golden numbers.
- **radon 6.0.1** (nested blocks in their parent, boolean operators counted), library only:

| | Before | After |
|---|---|---|
| Blocks | 1,300 | 1,475 |
| Mean complexity | 4.31 | 3.90 |
| Ranks A / B / C / D / E / F | 1,020 / 185 / 70 / 14 / 5 / 6 | 1,176 / 201 / 75 / 17 / 6 / 0 |
| Blocks above 15 | 44 | 39 |
| Largest | 80 (`desk_price`) | 40 (`replay`) |

| Package (after) | blocks | mean | max | above 15 |
|---|---|---|---|---|
| core | 147 | 2.41 | 26 | 1 |
| native | 32 | 4.38 | 20 | 1 |
| structured | 370 | 4.79 | 39 | 19 |
| marketdata | 216 | 3.75 | 40 | 2 |
| risk | 170 | 5.20 | 38 | 7 |
| server | 540 | 3.31 | 26 | 9 |

Largest after (radon): `marketdata/replay.py replay` 40, `structured/costs.py desk_price` 39, `structured/pricer.py
price_many` 39, `risk/greeks.py compute` 38, `structured/portfolio.py price` 36, `risk/scenarios.py run` 34. radon counts
each `and` and `or`, and the nested functions in their parent. Its 39 blocks above 15 are below 15 for ruff; they are long
sequences of options rather than deep branching.

## 4. Duplication, dead code, lint, types

- **Duplication** (pylint 4.0.2 `duplicate-code`, 8 lines).
  - Before, one block: the merge of fixing histories in the risk engine and the book.
  - After, that merge is shared (`structured.portfolio.merged_history`), and the copied helpers of the tests are in
    `tests/helpers`: a job's wait, a sheet's body, the SPX lake fixture.
  - One similarity remains: the OptionsDX column list that a test writes to describe the CSV format. It is kept on purpose.
- **Dead code** (vulture 2.14). Before, 64 findings at 60 % confidence or more, 2 at 90 % or more.
  - A8a removed what vulture and the coverage agreed on: seven functions, three constants, a parameter.
  - After, one finding at 90 % or more: the `dtype` parameter that `Generator.standard_normal`'s signature requires.
  - At 60 %, 165 findings. Nearly all are names that FastAPI and pydantic call: the routes, the fields of the schemas.
- **ruff**: 10 findings with the default rules before.
  - After: bandit (S), bugbear (B), pyupgrade (UP) and C901 are on, with no finding.
  - Each `noqa` says why the code is safe.
  - External programs run by their absolute path (`optlab.core.programs`).
  - `ruff format`: 169 files would have been reformatted before; all 251 are formatted after.
- **pyright** (1.1.414, standard mode, ADR-0014):

| | Before (v0) | A1 baseline (the workspace, tests included) | After |
|---|---|---|---|
| Errors | 766 | 1,491 | 0 |

- **What that 0 covers:**
  - standard mode everywhere, tests included;
  - strict mode on the 16 modules of the server's security and contract core;
  - the rest of the server stays in standard mode, by Luca's decision. Strict mode there measured 3,975 errors.
- **What reaching it took:**
  - `pandas-stubs` in the dev group, `py.typed` in every package;
  - overloads and protocols where the types depended on an argument;
  - narrowing asserts and a few reasoned ignores, listed in ADR-0014.
- **Security**: none of this was measured in v0. A5 to A7 added:
  - argon2id accounts, sessions with CSRF, a Host allow-list and security headers;
  - an audit log and a roles matrix tested route by route;
  - gitleaks, pip-audit, CodeQL and Trivy in CI.

  The security reviews of each step are in `docs/architecture/security.md`.

## 5. Tests and coverage

- **941 tests** after, against 831 in v0. Every reference of v0 is kept, and the tests are organised by package.
- **Duration:**
  - The full suite (the real lake, the GPU, node) passes **941 of 941 in 12 min 42 s** on six workers
    (`pytest -n 6 --dist loadfile`), under coverage.
  - v0's run took 1,941 s on one process, under coverage too. The two durations are not comparable: one process against six.
  - CI runs the offline part in about four minutes a pull request, on Windows and Linux.
- **Slowest tests** (coverage run):

| Test | Time |
|---|---|
| `test_cli.py::test_the_demo_data_serves_a_book_made_of_made_up_numbers` | 158 s |
| `test_risk_pnl.py::test_eod_is_stored_replayed_bit_for_bit_and_the_backfill_is_idempotent` | 143 s |
| `test_structured_localcorr.py::test_a_worst_of_has_both_provisions_...` | 82 s |
| `test_lake_desk.py::test_showcase_seed_...` | 78 s |
| `test_structured_basket_slv.py::test_the_worst_of_has_its_slv_provision_on_common_numbers` | 76 s |

- **Line coverage of `optlab`** (`pytest-cov`, in-process only; the spawned VaR, batch and worker processes are not
  measured): **93.0 %** (14,567 of 15,661 statements), against 92.6 % before.
  - By package: core 97.5 %, structured 97.0 %, native 93.0 %, marketdata 91.7 %, risk 91.7 %, server 89.7 %.
  - Lowest files of 30 statements or more: `marketdata/sources/__init__.py` 26.7 %, `server/logs.py` 33.3 %,
    `sources/yahoo.py` 54.3 %, `sources/dolthub.py` 58.7 %, `server/tools/lake_derive.py` 60.0 %, `server/cli.py` 62.8 %.
  - They are the network sources, the log files of the api and worker processes (both run in subprocesses in the tests),
    and the command line whose commands call those sources.

## 6. Business times

The A0 protocol, on 15 November 2026:

- the steps in sequence, on an otherwise idle machine;
- a GPU warm-up of 90 s first (1,268 Phoenix quotes);
- the showcase through the desk's own routes (`Desk`, `RiskApi`), on a copy of `optlab-data/showcase`, market
  `SPX-20261104T1600-8b7fa10118`, engine `gpu`.

| Measure | Before | After | Gap | Result |
|---|---|---|---|---|
| Six presets on the SPX calibration, GPU, 100,000 paths | 2.1 s | 1.02 s | -51 % | `presets_spx_gpu`, bit for bit |
| `structured examples --quick` (NumPy, 20,000 paths) | 29 s | 28.7 s | -1 % | |
| Showcase book, value with delta and vega (Standard) | 11.6 s | 11.6 s | 0 % | `showcase_book`, bit for bit |
| Showcase Greeks (Standard, node vegas for SPX) | 147.4 s | 138.6 s | -6 % | `showcase_book`, bit for bit |
| Showcase VaR (Taylor-check Greeks, stressed window, three horizons; 5 processes on the GPU) | 768.8 s | 743.6 s | -3 % | `showcase_var`, bit for bit |
| `bench accel` (1,000,000 paths, batches of 50,000), whole run | 277 s | 285.2 s | +3 % | table below |

`bench accel`, wall time per quote of 1,000,000 paths, before -> after:

| Case | NumPy, 1 worker | NumPy, 6 workers | CPU engine | GPU engine |
|---|---|---|---|---|
| Phoenix 2y, LV | 34.10 -> 31.70 s | 13.23 -> 12.41 s | 2.98 -> 2.82 s | 0.33 -> 0.34 s |
| Phoenix 2y, SLV | 26.23 -> 24.90 s | 6.06 -> 5.99 s | 3.69 -> 3.63 s | 0.35 -> 0.39 s |
| BRC 1y daily, LV | 23.52 -> 23.40 s | 7.45 -> 7.53 s | 1.86 -> 1.84 s | 0.24 -> 0.32 s |
| Phoenix 2y worst-of 3, LV | 100.09 -> 113.50 s | 38.58 -> 36.63 s | 5.96 -> 6.33 s | 0.54 -> 0.54 s |
| SLV leverage 1y, 200,000 particles | 4.45 -> 4.41 s | | 1.36 -> 1.35 s | 0.24 -> 0.15 s |

**Three cells moved by more than 10 %.** The handoff asks for a written reason past 10 %, and each was rerun on its own:

- **The GPU cells** (BRC 0.24 -> 0.32 s, SLV Phoenix 0.35 -> 0.39 s) depend on the card's clock. In the full run, the card
  falls back towards its idle clock during the NumPy cases of tens of seconds, as the baseline already noted. Warmed up and
  rerun back to back three times, the BRC takes 0.09 s and the SLV Phoenix 0.19 to 0.21 s, both below the baseline.
- **The NumPy worst-of on one worker** (100.09 -> 113.50 s) was timed against v0 itself, on the same machine and the same
  day: v0 (`4612e78`, checked out apart, in an isolated environment with the same numpy 2.5.4, scipy 1.18.1 and Python
  3.14.7) alternated with A8.

  | Run | v0 | A8 |
  |---|---|---|
  | 1 | 113.75 s | 113.04 s |
  | 2 | 106.13 s | 109.41 s |
  | Mean | 109.9 s | 111.2 s |

  The two agree within the run-to-run spread (about ±4 %), and give the same price to the last digit (96.5119 ± 0.0174).
  The baseline's 100.09 s was a faster day of the machine, not a faster code.

**No regression of the refactor past 10 %.**

NumPy and the native engines draw different normals, so their prices differ within the SE (99.0067 against 99.0137 ±
0.0120). The CPU and GPU engines agree to the last digit shown, as before.

## 7. Golden numbers

The 14 groups recorded at the start of the refactor (A1, ADR-0017) were recomputed at the end of A8, on this machine:

| Group | Result |
|---|---|
| `closed_forms`, `mc_products`, `calibration_simulated`, `presets_simulated`, `structured_extra`, `labjs` (CI) | bit for bit |
| `native_cpu`, `presets_spx_numpy`, `presets_spx_gpu`, `lake_derived`, `pnl_cascade`, `showcase_book`, `showcase_var` (local) | bit for bit |
| `native_gpu` | within its stated tolerance: the GPU's reductions use atomics, so their last bits vary from run to run |

CI recomputes the six CI groups on Windows and Linux. Across machines they are compared within the tolerance of ADR-0017,
because the calibration amplifies the last bits of libm (section 7 of the baseline).

## 8. How to reproduce

```bash
uv run ruff check . && uv run ruff format --check .
uv run lint-imports
uv run python tools/pyright_baseline.py
uvx --from radon==6.0.1 radon cc -a packages/*/src -j
uvx vulture@2.14 packages/*/src --min-confidence 60
uvx --from pylint==4.0.2 pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 --ignore-imports=y --ignore-docstrings=y packages tests
uv run pytest -n 6 --dist loadfile --cov=optlab --durations=8
uv run python tests/golden/golden.py check --local
uv run optlab bench accel
```
