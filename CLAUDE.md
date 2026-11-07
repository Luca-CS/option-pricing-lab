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
  `-DOPTLAB_WITH_CUDA=OFF` builds the CPU engines only; `-DOPTLAB_CUDA_ARCHS=portable` targets sm_61 to sm_120.
  `build\Release\test_paths.exe` checks the path engine; `scripts/bench_accel.py [--desk standard]` times NumPy against
  the CPU and GPU engines; `tests/test_native.py` validates the engine from Python (skipped without the library).
  Profiling: `ncu --set full -k regex:mc_kernel -o build/ncu_profile build\Release\profile.exe` from an
  administrator shell (GPU counters are admin-only on this machine); read it with
  `ncu -i build/ncu_profile.ncu-rep --page raw --csv --metrics ...` (`--page details` fails under the French locale).
- Risk engine (`optlab/risk/`): `tests/test_risk_*.py` (about 20 minutes; `test_risk_var`, `test_risk_pnl` and the betas test need the
  lake and skip without it). `scripts/risk_eod.py eod --date D | backfill --from A --to B | pnl --from A --to B | var | ladder`
  works on a desk store (default the showcase's) and keeps every run in `risk.sqlite` beside it; method and measured numbers in
  `reports/risk.md`. The desk serves it on `#/risk` and `#/pnl` (`app/risk_api.py`, routes `/api/risk/*`, `/api/pnl*`, report
  `/report/risk`).
- Data lake (`optlab/marketdata/`, needs `duckdb` and `pyarrow`; data in `OPTLAB_DATA`, default `optlab-data` beside the main checkout):
  `scripts/lake_update.py` (public sources, yen and sterling rates in the steps `mof`, `boj`, `boe`; `--import-sample zip`, `--dolt default`,
  `--yahoo-chains` during US hours only: Yahoo blanks bid and ask otherwise), `scripts/lake_derive.py` (curves, carry, bank, surfaces, correlation, pairs, observed fits),
  `scripts/lake_fx_check.py` (FX against H.10 and CME futures, size of the quanto adjustment: `reports/fx_check.md`),
  `scripts/lake_surface_test.py` (reconstruction against real chains), `scripts/lake_regime_test.py` (the same by level of the VIX, crash held out),
  `scripts/lake_replay.py cost|run [--costs]`, `scripts/seed_showcase.py` (a dozen trades in their own store, `optlab-data/showcase`; guide in `reports/showcase.md`).
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
- **`optlab/native.py`** drives the path engine of `cuda/` (`paths.hpp`: one header for the CPU in double and the GPU in
  float; `leverage.hpp`: the SLV particle method): `plan()` turns a model or a `Basket` into arrays (local variance and
  squared leverage as uniform rows per step), `run()` simulates a range of pairs. Normals are Philox, a function of
  (seed, pair, rank of the normal), so prices depend on (seed, n_paths) only and the CPU and GPU share their draws.
  `engine=` (numpy, cpu, gpu, auto) on `price_many`, `native.set_default`, or `$OPTLAB_ENGINE` picks the source; the
  default is numpy, so the library's numbers and its bit-for-bit tests are unchanged; `structured/engine.py` falls
  back to NumPy with the reason in `timings["engine_note"]`. On the engine, batches are sized by memory, evaluated on
  several threads while the next one simulates, and daily windows come back as their lowest and highest spot.
  `slv.calibrate_leverage(engine=...)` uses the same switch. The desk starts with `--engine auto`. Keep the
  `_PathsRequest` and `_LeverageRequest` structures field by field equal to `capi.h`.
- **`optlab/structured/`** prices structured products in three layers. Engine: a payoff script language
  (`script/`: hand-written lexer and recursive-descent parser, AST in `nodes.py`, printer, `validate`, `timeline`,
  vectorised `evaluator` with float masks: `x = e` under a mask m is `m*e + (1-m)*x`, `pay` adds `m*alive*e`,
  `terminate` multiplies `alive` by `1-m`), `Scenario` batches from `simulate` (one batch = `default_rng(seed)`,
  so scripts reproduce `products.py` bit for bit; several = `SeedSequence(seed).spawn`), `Note`/`Swap` wrappers,
  `dates.py` (Mon-Fri calendar). Builder: `schema.py` (sections, fields with `visible_if`, masks `autocall` and
  `bullet`, `TermSheet`, `describe()` for a front end), `compiler.py` (term sheet → script), `presets.py`.
  Analysis: `profile.py` classifies the AST (european, path_dependent, callable), `policy.py` maps the class to a base
  (european → `replication.py` on the SVI smile; otherwise local vol Monte Carlo) and to its models of provision (SLV, and local correlation on a basket),
  `solve.py` (coupon affine on common random numbers), `quote.py`, `greeks.py`, `report.py`. `costs.py` is the desk price (results in `reports/pricing.md`): base + provisions
  floored at zero + bumps signed by the trader + digitals as call spreads (`digitals.py`), every line on the base's random numbers, the coupon solved on the client price. `defaults.py`
  holds the table by group of underlyings and size (versioned in the store, edited at `#/costs`), `reserves.py` what a trade freezes at issue, `localcorr.py` the correlation skew. Independent
  references for the tests are in `tests/_structured_refs.py` (closed forms, autocall backward quadrature).
  On top: `portfolio.py` (`Book` of `Position`s on one shared simulation, book Greeks), `cashflows.FundedSwap`
  (floating funding leg until redemption), `multiasset.py` (`Basket` of correlated names, `BasketContext`, SLV with a leverage per name), and past `fixings` for running products.
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
  `derive/` (curves, carry, fictitious bank, surfaces in three layers, FX forwards, volatility and correlation, correlation and pseudo CvC/PvP: option on a basket against the same option on its members), `quotes.py` (one cleaning chain,
  `optlab.data.clean_chain`, for every source), `pit.py` (`market_at`: only rows dated at or before the date; the forward repeats last year's dividends, `carry.dividend_stream`; `payment` gives the quanto market, `structured/quanto.py`), `replay.py` (a trade's life on
  real closes), `vollab.py` (fits as stored, replayable runs). The model stays at spot 100: real levels cross at the boundary through
  `spot_scale`. Pricing a sheet on another name's market is an error.
- **`optlab/risk/`** is the risk engine, one block with JSON-ready dataclasses in and out, reading `structured/` and `marketdata/`
  only (the desk reaches it through the thin `app/risk_api.py`). Principle: a risk is a list of `Shock`s (`shocks.py`: spots, vols
  parallel, relative, per node or per tenor, skew, curves per tenor, dividends, repo, correlation, FX, quanto, roll, and the vol rule
  sticky moneyness / sticky strike / realistic) revalued by one engine (`engine.run_group`) on the base's seed. Shocks that only move
  spots, forwards or discounts ride on one simulation (a factor on the sticky-moneyness paths, exact); vol, correlation and the roll
  resimulate on the same seed; `Measure`s combine revaluations into Greeks with paired SEs. `grid.py` (moneyness x tenors, smooth
  tents: smoothed hats in strike, wider in the wings and with sqrt(T), so that a one-point node bump stays free of butterfly
  arbitrage), `surfaces.TentBumpedSurface` (exact derivatives for Dupire; `local_var_rows` builds the engine's table at once, a
  hook in `native._local_var_rows`), `curves.py`, `greeks.py` (every variant, keys "greek|name|bucket"), `aggregate.py`,
  `ladder.py` (and spot-vol betas from the lake), `scenarios.py` (signed, versioned in the store), `var.py` (historical full
  reval on processes, stressed window searched by delta-gamma-vega then frozen), `pnl.py` (EOD runs, backfill, the cascade
  time, spot, vol, skew, rates, dividends, corr, fx on the seeds of the day before, the Greeks' prediction), `store.py`
  (SQLite: versioned records and runs keyed by what determines them), `report.py`. Tests compare with closed forms and
  `tests/_risk_refs.py`.
- **`space/app.py`** is a local Gradio front-end (needs `pip install gradio`), not deployed.

## Conventions

- Tests validate every stochastic pricer against an independent reference within 4 standard errors
  (`within` helper in `tests/test_monte_carlo.py`); keep fixed seeds.
- Market data from Yahoo is personal-use only: `data/` and the lake are never committed; only
  calibrated parameters, outputs and charts are versioned. Results hold performances, not levels; screens show the `origin`. `.gitignore` also ignores `*.csv` and `build/`.
- README and code are in English; commit messages are short English sentences in the style
  `"Demo page: ..."` (see `git log`).
