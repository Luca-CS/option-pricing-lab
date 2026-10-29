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

- `tests/test_widget_js.py` runs `docs/lab.js` under `node` and compares it to the Python library; it is
  skipped when `node` is not on PATH.
- Calibration pipeline: `scripts/run_calibration.py --source simulated|spx` writes `results/*.json`;
  `scripts/make_report.py` writes `reports/calibration.md` and figures; `scripts/build_widget_data.py`
  regenerates `docs/pricer-data.js` from `results/spx_calibration.json`. `--source spx` needs a snapshot
  from `scripts/download_spx.py` in `data/raw/`.
- CUDA engine (`cuda/`, needs CUDA 12.x and VS 2022 Build Tools; RTX 2070 Super, `sm_75`):
  `cmake -S cuda -B build -G "Visual Studio 17 2022" -A x64`, `cmake --build build --config Release`,
  `ctest --test-dir build -C Release`. `build\Release\test_prices.exe cpu|gpu|double|closed|qe-bias` runs one suite.
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
  Keep path math free of double literals (`R(0.5)`), as FP64 runs at 1/32 rate on this card.
- **`space/app.py`** is a local Gradio front-end (needs `pip install gradio`), not deployed.

## Conventions

- Tests validate every stochastic pricer against an independent reference within 4 standard errors
  (`within` helper in `tests/test_monte_carlo.py`); keep fixed seeds.
- Market data from Yahoo is personal-use only: `data/` is git-ignored and must never be committed; only
  calibrated parameters, outputs and charts are versioned. `.gitignore` also ignores `*.csv` and `build/`.
- README and code are in English; commit messages are short English sentences in the style
  `"Demo page: ..."` (see `git log`).
