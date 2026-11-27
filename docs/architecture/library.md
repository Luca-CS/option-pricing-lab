# The library

What each domain package does and how its parts fit. The overview is in [README.md](README.md); the numbers these
parts produce are in [reports/](../../reports).

## 1. Core (`optlab.core`)

Pure NumPy and SciPy: no I/O, no thread at import.

- **Market** (`market.py`) holds deterministic discount and forward curves. Every pricer takes `(model, market, ...)`.
- **Models**: Black-Scholes, Heston, local vol (Dupire), stochastic local vol. A model is defined by its instantaneous
  variance `inst_var(t, x, v)` (and `has_variance` when it carries a variance process).
- **Simulation** (`models.simulate`): every model is simulated in the log-forward coordinate `x = ln(S_t / F(t))`,
  with a log-Euler step on the variance at the start of the step, so forwards are matched exactly. Heston's variance
  is full-truncation Euler. The time grid is graded (step `dt_min + t/60`, capped at `dt_max`) and always holds the
  observation dates; Black-Scholes is simulated exactly in one step.
- **Monte Carlo pricers** (`products.py`): European, Asian with a geometric control variate, daily barrier,
  Longstaff-Schwartz American. All use antithetic pairs, and the standard error is computed on pair averages.
  Results are `MCResult(price, stderr)`.
- **Calibration chain** (`pipeline.py`): an SSVI surface (`ssvi.py`), then a raw SVI refit per slice (`svi.py`, which
  also gives Dupire's local variance), then Heston (`heston.py`, priced by Lewis's Fourier formula), then the SLV
  leverage by the particle method (`slv.py`).
- **Engines** (`engines.py`): a registry of Monte Carlo engines by name (`numpy`, `cpu`, `gpu`, `auto`). The default is
  NumPy, so the library's numbers do not depend on the machine.

## 2. Native engines (`optlab.native`)

A C++/CUDA engine behind a nanobind module ([ADR-0008](adr/0008-native-engine-nanobind.md); its own documentation:
[packages/native/cuda/README.md](../../packages/native/cuda/README.md)).

- **Fixed products**: the path schemes (exact Black-Scholes steps, Andersen's QE-M for Heston) are `__host__
  __device__` templates on the real type, shared by the double CPU reference and the float and double GPU kernels.
- **Path engine** (`paths.hpp`, `leverage.hpp`): one header for the CPU in double and the GPU in float. `plan()` turns a
  model or a basket into arrays (local variance and squared leverage as uniform rows per step), `run()` simulates a
  range of pairs.
- **Reproducibility**: normals are Philox, a function of (seed, pair, stream, rank of the normal), so prices depend
  on (seed, n_paths) only, and the CPU and the GPU share their draws. GPU reductions use `atomicAdd`, so their last bits
  may vary from run to run; paths do not.
- **Two streams per pair**: stream 0 holds the spots (and Heston's variance residuals and the mixer's common normal,
  interleaved per step); stream 1 holds the variance residuals of a simulation with an SLV name. An SLV name thus reads
  the spot normals of local vol on the same seed, as NumPy's `spot_driven` does, and the SLV reserve is a difference on
  common random numbers: on a 2-year Phoenix at 100,000 paths, the SE of the gap is 0.026 % of notional (NumPy 0.026;
  0.048 with one stream), and 0.002 at a mix of 0 (NumPy 0.0016; 0.047 with one stream).
- **Choosing an engine**: `engine=` on `price_many`, `optlab.core.engines.set_default`, or `$OPTLAB_ENGINE`. Outside
  the engine's cases (or without a GPU) the call falls back to NumPy and says why.

## 3. Structured products (`optlab.structured`)

Three layers.

**Engine.**
- A payoff script language (`script/`): a hand-written lexer and recursive-descent parser, an AST, a printer,
  validation and a timeline. The evaluator is vectorised with float masks: `x = e` under a mask `m` is
  `m*e + (1-m)*x`, `pay` adds `m*alive*e`, `terminate` multiplies `alive` by `1-m`. A NaN condition or a non-finite
  payment on a live path raises `ScriptError` at its line. Windows read the daily closes between two dates through
  `lowest` and `highest`, or in full through `variance` (the realised variance, 252 days a year, optionally in a
  corridor of spot levels); a script observes at most 10,000 dates.
- `Scenario` batches come from `simulate` (one batch is `default_rng(seed)`, several are `SeedSequence(seed).spawn`),
  so scripts reproduce the hand-written pricers bit for bit.
- `engine.py` evaluates on each simulated batch, sequentially or on a persistent process pool, with the same numbers
  bit for bit.

**Builder.**
- `schema.py` describes a term sheet: sections, fields with `visible_if`, the `autocall`, `bullet`, `variance` and
  `accumulator` masks (a mask may restrict choices, hide fields and set its own defaults), and `describe()` for a front end.
  `compiler.py` turns a term sheet into a script; `presets.py` holds the presets.
- **Autocall mechanisms**, fields of the `autocall` section: an asynchronous call (a worst-of locks each name the
  first time it closes at or above its trigger on a call date, and calls once every name is locked), and a lizard (on
  one call date, an early redemption at par plus a bonus when no close breached the lizard barrier since the strike;
  priced like a daily barrier, its digital overhedge moved by the minimum spread).
- **Accumulators and decumulators** (the `accumulator` mask): an event per business day buys (sells) one unit at the
  strike, `gearing` units beyond it, until the first close at or beyond the knock-out (none in the guaranteed
  period); each period settles at its end, outright as a swap (the units' value against the strike) or fully funded as
  a note (the shares bought and the cash not spent, or the cash received and the shares kept). A knock-out settles its
  period, the rest at once, and ends the product.
- **Variance and volatility swaps** (`varswap.py`), plain or in a corridor: the swap pays its vega notional times
  (sigma^2 - K^2) / 2K (capped) or (sigma - K). The quote solves the fair strike (in variance for a var swap) on the
  base's paths and shows it in vol, beside the log-contract replication on the smile (model-free, continuous
  monitoring). A vol swap's convexity, the gap to the variance strike, is the vol of vol that SLV carries as a reserve.
  Booking writes the solved strike into the sheet.

**Analysis and pricing.**
- `profile.py` classifies a script: european, path-dependent or callable.
- `policy.py` maps the class to a base method and its models of provision. A European product is replicated on the SVI
  smile (`replication.py`); anything else is priced by local vol Monte Carlo, with SLV as control and model reserve
  (and local correlation on a basket).
- `solve.py` solves a coupon (the price is affine in it, on common random numbers); `quote.py`, `greeks.py`,
  `report.py` build the quote.
- **The desk price** (`costs.py`, [reports/pricing.md](../../reports/pricing.md)): the base, then
  - model reserves, floored at zero (the gap to SLV or local correlation when it goes against the desk);
  - the market overs signed by the trader: overvol, overskew, overcorrel, overdiv, overrepo, the delta bid-ask;
  - digital overhedges, each digital priced as a call spread (`digitals.py`).

  Every line is computed on the base's random numbers, and the coupon is solved on the client price. `defaults.py`
  holds the table of overs by group of underlyings and size, `reserves.py` what a trade freezes at issue.
- **Book** (`portfolio.py`): positions on one shared simulation, with the book's Greeks. Also baskets of correlated
  names (`multiasset.py`, SLV with one leverage per name), funded swaps (`cashflows.py`), past fixings for running
  products, lifecycle events (`lifecycle.py`), the NYSE calendar, vol and skew bumps with exact derivatives
  (`surfaces.py`).
- **Hedges** (`hedges/`, [ADR-0025](adr/0025-hedges-are-trades.md)): the instruments a desk hedges with, booked as
  trades of the same book. Linear ones (`instruments.py`: futures, cash, interest rate swaps, deposits, funding swaps,
  dividend swaps and futures) are valued in closed form on a `Valuation`: a context's market in the model's scale, the
  real spot over the model's, the issuer's funding curve (`SpreadCurve`) and the swap spread. The value is that of the
  flows to come (a future: the variation margin, multiplier × (F(T) − price)); dividends are what the forward carries.
  A `Book` holds them after its simulated positions (`Book.linear`: their `LinearEnv`), adds their exact values to
  every result and never simulates them; `home()` names the group an instrument joins (its name's, or its currency's
  reference index for a rates instrument). A `vanilla` (European call or put, listed or OTC) is not linear: the book
  turns it into a payoff script (its strike and size in the model's scale, a leg discounted on the government curve),
  simulated with the notes of its group or replicated on the smile when its group holds Europeans only.
  `contracts.py` holds the listed contracts (multipliers, quarterly expiries), `costs.py` their half-spreads in the
  cost table.

## 4. Market data (`optlab.marketdata`)

- **The lake** (`lake.py`): a raw manifest, append-only Parquet with an `origin` on every row (observed, derived,
  reconstructed, synthetic), idempotent and bitemporal loads, DuckDB reads.
- **Sources** (`sources/`): one pure `parse` per source, tested offline. `ingest.py` loads them.
- **Derived tables** (`derive/`): discount curves, carry, a fictitious bank, surfaces in three layers, FX forwards,
  realised volatility and correlation, and the pseudo-quotes of CvC and PvP (an option on a basket against the same
  option on its members).
- **Option chains** (`quotes.py`): one cleaning chain for every source.
- **Point in time** (`pit.py`): `market_at(day)` reads only rows dated at or before the day. The forward repeats last
  year's dividends; `payment` gives the quanto market.
- **Replay** (`replay.py`): a trade's life on real closes, with its explained P&L.
- **Vol lab** (`vollab.py`): SSVI, SVI and Heston fitted to a day's chain, stored as replayable runs.
- **Synthetic data** (`synthetic.py`): a whole lake from fixed seeds, for the tests and the demo.

The model stays at spot 100; real levels cross at the boundary through `spot_scale`.

## 5. Risk (`optlab.risk`)

One block, with JSON-ready dataclasses in and out, reading `structured` and `marketdata` only
([reports/risk.md](../../reports/risk.md)).

- **Principle**: a risk is a list of `Shock`s (`shocks.py`: spots, vols parallel, relative, per node or per tenor,
  skew, curves per tenor, dividends in total or by calendar year, repo, the issuer's funding spread per tenor,
  correlation, FX, quanto, the roll, and the vol rule: sticky moneyness, sticky strike or realistic), revalued by one
  engine (`engine.run_group`) on the base's seed. Linear instruments are valued in closed form on each shock's moved
  market, beside the simulated positions of their group (SE 0); a group of instruments only runs no simulation.
- Shocks that only move spots, forwards or discounts ride on one simulation (an exact factor on the paths); vol,
  correlation and the roll resimulate on the same seed. `Measure`s combine revaluations into Greeks with paired
  standard errors.
- **The grid** (`grid.py`): moneyness × tenors, with smooth tents (hats smoothed in strike, wider in the wings and with
  √T), so that a one-point node bump stays free of butterfly arbitrage.
- **Measures**: Greeks of every variant (`greeks.py`), aggregation, the spot ladder and spot-vol betas from the lake
  (`ladder.py`), signed and versioned scenarios (`scenarios.py`), historical VaR by full revaluation with a stressed
  window (`var.py`), and the explained P&L (`pnl.py`): end-of-day runs and a cascade (time, spot, vol, skew, rates,
  dividends, correlation, FX) on the seeds of the day before, against the Greeks' prediction.
- **The vol move opened** (`explain.py`): a day's spot, vol and skew steps under each spot-vol rule (the same total, split
  differently), and under one rule the vol move node by node or tenor by tenor, each step a full revaluation, read by
  maturity (level and shape) and by delta bucket; the nodes and a residual add up to the vol and skew steps exactly.
- **The hedge optimizer** (`hedge.py`): candidates (futures, vanillas by expiry and delta bucket, swaps, dividend swaps,
  deposits, funding swaps) priced as positions of one unit in the same risk run as the book, so their hedge ratios are
  measured on the book's paths; then a mixed-integer linear program (HiGHS): the least half-spread cost such that each
  target Greek is within its tolerance, listed lots as integers, a priced slack saying which targets the universe or
  the limits cannot reach; the proposal checked under the spot × vol grid and the VaR's 1-day moves, before and after.
- **Storage** (`store.py`): versioned records and runs keyed by what determines them.

## 6. The desk (`optlab.server`)

The API, the job queue, the worker and the CLI over the packages above: [api.md](api.md) for the API's conventions,
[operations.md](operations.md) to run it.

- `api.py` holds a `Desk` whose methods are the services (JSON-ready dicts in and out); `app.py` builds the FastAPI
  application; `routers/` holds one router per domain, each route declaring its roles.
- Jobs ([ADR-0006](adr/0006-job-queue-sqlite-table.md)): `jobs.py` is the persistent queue, `tasks.py` what a job
  freezes when submitted (the market by snapshot id, the cost table's version, trades by version), `worker.py` the loop.
  A restart forgets no job; a job a crash interrupted ends in error, never resumed.
- `persistence/` keeps everything in one SQLite database (WAL, SQLAlchemy Core, Alembic migrations).
- Every name is valued on its own market: the one in use for its name, else the lake's at the same date, else flat
  inputs. The book is valued in independent groups (one per single name, one per worst-of).
