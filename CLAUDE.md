# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt          # Windows; .venv/bin/pip elsewhere
.venv/Scripts/python -m pytest -q tests/test_closed_forms.py   # fast sanity check (seconds)
.venv/Scripts/python -m pytest tests/test_monte_carlo.py::test_heston_mc_matches_fourier   # single test
.venv/Scripts/python -m pytest                          # full suite, ~10 min (mostly SLV in test_calibration.py)
```

- Structured products: `.venv/Scripts/python -m pytest -q tests/test_structured_*.py` (about a minute);
  `scripts/structured_examples.py [--quick]` prints the autocall model-risk table, timings and example quotes.
- Desk (web UI): `.venv/Scripts/python -m optlab.app [--open] [--workers 6] [--host 0.0.0.0]` serves
  `http://localhost:8765` (a token is generated beyond localhost; `--tunnel` gives a public link through
  localhost.run); `--store` and `--calibration` change the SQLite file and the calibration. `tests/test_app_api.py`
  (about 40 s) covers the API and the HTTP server; `tests/test_app_js.py` runs the page's table sorting under
  `node` (skipped without it, like `test_widget_js.py`).
- `tests/test_widget_js.py` runs `docs/lab.js` under `node` and compares it to the Python library; it is
  skipped when `node` is not on PATH.
- Calibration pipeline: `scripts/run_calibration.py --source simulated|spx` writes `results/*.json`;
  `scripts/make_report.py` writes `reports/calibration.md` and figures; `scripts/build_widget_data.py`
  regenerates `docs/pricer-data.js` from `results/spx_calibration.json`. `--source spx` needs a snapshot
  from `scripts/download_spx.py` in `data/raw/`.
- CUDA engine (`cuda/`, needs CUDA 12.x and VS 2022 Build Tools; RTX 2070 Super, `sm_75`):
  `cmake -S cuda -B build -G "Visual Studio 17 2022" -A x64`, `cmake --build build --config Release`,
  `ctest --test-dir build -C Release`. `build\Release\test_prices.exe cpu|gpu|double|closed|qe-bias` runs one suite.
  Benchmarks, from the repo root: `cuda/bench/bench_numpy.py` (venv) then `build\Release\bench.exe`;
  `build\Release\ablation.exe` (plus `ablation_ieee`, `ablation_fastmath`: same library, other nvcc math flags).
  Time the GPU only after a sustained warm-up (seconds of CPU work drop it to the 300 MHz idle clock).
  Profiling: `ncu --set full -k regex:mc_kernel -o build/ncu_profile build\Release\profile.exe` from an
  administrator shell (GPU counters are admin-only on this machine); read it with
  `ncu -i build/ncu_profile.ncu-rep --page raw --csv --metrics ...` (`--page details` fails under the French locale).
- Data lake (`optlab/marketdata/`, needs `duckdb` and `pyarrow`; data in `OPTLAB_DATA`, default `optlab-data` beside the main checkout):
  `scripts/lake_update.py` (public sources; `--import-sample zip`, `--dolt default`, `--yahoo-chains` during US hours only: Yahoo blanks
  bid and ask otherwise), `scripts/lake_derive.py` (curves, carry, bank, surfaces, correlation, pairs, observed fits),
  `scripts/lake_surface_test.py` (reconstruction against real chains), `scripts/lake_regime_test.py` (the same by level of the VIX, crash held out),
  `scripts/lake_replay.py cost|run`, `scripts/seed_showcase.py` (a dozen trades in their own store, `optlab-data/showcase`; guide in `reports/showcase.md`).
  `tests/test_lake_real.py` checks the exit criteria on the real lake and skips without it.
- Lint config: ruff, line length 120 (`pyproject.toml`). No build step for `docs/` (static, dependency-free JS).

## Architecture

- **`optlab/`** is pure NumPy/SciPy. `Market` (`market.py`) holds deterministic discount and forward curves;
  every pricer takes `(model, market, ...)`.
- **Simulation core** is `models.simulate`: every model is simulated in the log-forward coordinate
  `x = ln(S_t / F(t))` with a log-Euler step using the instantaneous variance `model.inst_var(t, x, v)` at the
  start of the step, so forwards are matched exactly. Heston variance is full-truncation Euler (not QE).
  The time grid is graded (`time_grid`: step `dt_min + t/60`, capped at `dt_max`) and always contains the
  observation dates; Black-Scholes collapses to exact one-step simulation. Adding a model means implementing
  `inst_var` (and `has_variance` if it carries a variance process).
- **`products.py`** builds the Monte Carlo pricers (European, Asian with geometric control variate, daily
  barrier, Longstaff-Schwartz American) on top of `simulate`. All use antithetic pairs and the standard error
  is computed on pair averages; results are `MCResult(price, stderr)`.
- **Calibration chain** (`pipeline.py`): SSVI surface (`ssvi.py`) → per-slice raw SVI refit (`svi.py`, which
  also provides the Dupire local variance) → Heston (`heston.py`, Lewis Fourier pricing) → SLV leverage by the
  particle method (`slv.py`).
- **`docs/`** is the GitHub Pages demo. `docs/lab.js` is an independent JavaScript re-implementation (closed
  forms, CRR, American PDE, Heston Fourier, Monte Carlo with Andersen's QE scheme for Heston in `simulate`,
  LSM) exposed as `window.PricerWidget`; changes to its pricers should keep `tests/test_widget_js.py` passing.
- **`cuda/`** is a C++/CUDA Monte Carlo engine. `include/optlab_cuda/schemes.hpp` holds the path schemes (exact BS
  steps, Andersen QE-M for Heston, in the log-return y = ln(S/S0)) as `__host__ __device__` templates on the real
  type, shared by the double CPU reference (`cpu_mc.cpp`) and the float/double GPU kernels (`gpu_mc.cu`). Philox
  subsequence = pair index, so prices depend only on (seed, N), not on grid, block size or launch chunking.
  Keep path math free of double literals (`R(0.5)`), as FP64 runs at 1/32 rate on this card. Production defaults
  (float, Kahan float sums, `-prec-div=false -prec-sqrt=false -ftz=true`, block 512) come from the measured
  ablation; `__expf`/`__logf` stay off because they bias the Heston barrier by 0.13 SE.
- **`optlab/gpu.py`** calls the CUDA engine from Python through `ctypes` (`build/Release/optlab_cuda_c.dll`, C API in
  `cuda/src/capi.cpp`; the `_Request`/`_Result` structures mirror `capi.h` field by field) and falls back to the
  NumPy pricers without the library, without a GPU, or outside the engine's cases. Engine errors are C++
  exceptions turned into an error code, never `exit`: the engine runs inside the Python process.
  `tests/test_gpu.py` skips the engine tests when the library or the GPU is missing.
- **`optlab/structured/`** prices structured products in three layers. Engine: a payoff script language
  (`script/`: hand-written lexer and recursive-descent parser, AST in `nodes.py`, printer, `validate`, `timeline`,
  vectorised `evaluator` with float masks: `x = e` under a mask m is `m*e + (1-m)*x`, `pay` adds `m*alive*e`,
  `terminate` multiplies `alive` by `1-m`), `Scenario` batches from `simulate` (one batch = `default_rng(seed)`,
  so scripts reproduce `products.py` bit for bit; several = `SeedSequence(seed).spawn`), `Note`/`Swap` wrappers,
  `dates.py` (Mon-Fri calendar). Builder: `schema.py` (sections, fields with `visible_if`, masks `autocall` and
  `bullet`, `TermSheet`, `describe()` for a front end), `compiler.py` (term sheet → script), `presets.py`.
  Analysis: `profile.py` classifies the AST (european, path_dependent, callable), `policy.py` maps the class to a
  model (european → `replication.py` on the SVI smile; otherwise SLV Monte Carlo with an LV control),
  `solve.py` (coupon affine on common random numbers), `quote.py`, `greeks.py`, `report.py`. Independent
  references for the tests are in `tests/_structured_refs.py` (closed forms, autocall backward quadrature).
  On top: `portfolio.py` (`Book` of `Position`s on one shared simulation, book Greeks), `cashflows.FundedSwap`
  (floating funding leg until redemption), `multiasset.py` (`Basket` of correlated names, `BasketContext`; the
  `basket` policy rule is LV per name with a BS control), and past `fixings` for running products.
  Batches run through `engine.py` (`Evaluation`s on each simulated batch, sequential or `workers=` on a persistent
  spawn process pool, same numbers bit for bit; spot factors scale the paths for the Greeks). Also `trade.py`,
  `calendars.py` (NYSE), `lifecycle.py`, `surfaces.py` (`FastSVISurface`, bit-identical), `fastmodels.py` and
  `qmc.py` (opt-in, validated within the Monte Carlo error), `units.py`; `optlab/config.py`, `optlab/log.py` and
  `optlab/marketdata/` (snapshots and their local store under `data/store`). A script with a NaN condition or a
  non-finite payment on a live path raises `ScriptError` with its line.
- **`optlab/app/`** is the local web desk (design and status in `optlab/app/DESIGN.md`). `api.py` holds a `Desk`
  whose methods are the API routes (JSON in and out, levels as fractions; the page shows percent), `server.py` maps
  routes to them on a stdlib `ThreadingHTTPServer` that also serves `static/` (same origin; one buffered write per
  response and no Nagle, or Windows browsers wait ~250 ms per call), `jobs.py` runs quotes and book valuations in a
  thread pool that the page polls, `repository.py` keeps trades (versioned), fixings and quotes in SQLite under
  `data/store` with the basket settings (market snapshots go to `data/store/market`), `report.py` writes standalone
  HTML reports, `tunnel.py` opens the `--tunnel` SSH link, `fixings.py` parses pasted or CSV fixings (the page sends
  the raw text), `netcheck.py` reads what blocks LAN access on Windows (firewall, Public profile; read-only).
  Every name is on its own market: the one in use for its name, else the lake's at the same date (`marketdata/names.py`), else flat
  inputs (what is typed in the Market screen wins). The book is valued in independent groups (one per single name, the worst-ofs).
  Each job captures the market context when submitted, so a snapshot switch never changes
  a run in progress. The page is plain ES modules
  without a build: `js/main.js` routes `#/screen/arg` to `js/views/*.js`, each exporting `render(page, arg)`, and
  makes every `table.data` sortable.
- **`optlab/marketdata/`**: `lake.py` (raw manifest, append-only Parquet with `origin` on every row, idempotent and bitemporal loads, DuckDB
  reads), `sources/` (one pure `parse` per source, tested offline), `universe.py` (the instrument registry, which `schema.py` reads),
  `derive/` (curves, carry, fictitious bank, surfaces in three layers, correlation and pseudo CvC/PvP: option on a basket against the same option on its members), `quotes.py` (one cleaning chain,
  `optlab.data.clean_chain`, for every source), `pit.py` (`market_at`: only rows dated at or before the date; the forward repeats last year's dividends, `carry.dividend_stream`), `replay.py` (a trade's life on
  real closes), `vollab.py` (fits as stored, replayable runs). The model stays at spot 100: real levels cross at the boundary through
  `spot_scale`. Pricing a sheet on another name's market is an error.
- **`space/app.py`** is a local Gradio front-end (needs `pip install gradio`), not deployed.

## Conventions

- Tests validate every stochastic pricer against an independent reference within 4 standard errors
  (`within` helper in `tests/test_monte_carlo.py`); keep fixed seeds.
- Market data from Yahoo is personal-use only: `data/` and the lake are never committed; only
  calibrated parameters, outputs and charts are versioned. Results hold performances, not levels; screens show the `origin`. `.gitignore` also ignores `*.csv` and `build/`.
- README and code are in English; commit messages are short English sentences in the style
  `"Demo page: ..."` (see `git log`).
