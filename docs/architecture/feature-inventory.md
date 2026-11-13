# Feature inventory: every function of v0, its home in v1

The parity list of the refactor. Session 1 (architecture) keeps every function behind API v1 or the `optlab` CLI;
session 2 (front) checks every screen line against its new screen. Measured on `main` + the risk engine (PR #1) on
7 November 2026.

Status: **kept** (same function, new place), **merged** (folded into another line, named), **dropped** (only with
Luca's agreement, given at the A0 checkpoint on 7 November 2026).

Routes are written without the `/api/v1` prefix. `202` marks a route that answers with a job (`POST` returns
`{job_id}`, progress on `GET /jobs/{id}` and `GET /jobs/{id}/events`). Roles are the minimum to call the route
(`viewer` reads everything; see `security.md` for the matrix).

## 1. HTTP routes (57: 46 in `app/server.py`, 10 in `app/risk_api.py`, 4 reports, plus static files)

### Status and market

| v0 route | Does | v1 route | Package | Role | Status |
|---|---|---|---|---|---|
| `GET /api/health` | version, snapshot, workers, GPU | `GET /health` (alive) and `GET /ready` (db, lake, worker); version, workers and GPU move to `GET /admin/system` | server | none / admin | kept, split |
| `GET /api/context` | valuation date, spot, curve summary, ATM vols, last calibrated maturity | `GET /market/context` | marketdata | viewer | kept |
| `GET /api/market/snapshots` | snapshots of the store | `GET /market/snapshots` (cursor) | marketdata | viewer | kept |
| `POST /api/market/use` | switch the market in use | `PUT /market/in-use` `{snapshot_id}` (desk-wide, audited) | server | trader | kept |
| `GET /api/market/smile?T=` | implied vols of the surface at T | `GET /market/snapshots/{snapshot_id}/smile?T=` | marketdata | viewer | kept |
| `GET /api/market/term` | ATM term structure, forward and discount | `GET /market/snapshots/{snapshot_id}/term` | marketdata | viewer | kept |
| `GET /api/market/risk` | model-risk table of the calibration (BS, LV, Heston, SLV) | `GET /quant/model-risk?snapshot_id=` | quant (core) | viewer | kept |
| `GET /api/basket`, `PUT /api/basket` | other underlyings typed for worst-ofs (vol, dividend, correlations) | `GET`, `PUT /market/other-underlyings` | server | trader | kept |

### Lake ("Data" screen)

| v0 route | Does | v1 route | Package | Role | Status |
|---|---|---|---|---|---|
| `GET /api/data/overview` | coverage by table, origin and source, universe, quality issues | `GET /market/lake/coverage`, `GET /market/universe`, `GET /market/lake/quality`; the same tables feed `GET /admin/pipelines` | marketdata | viewer | kept, split |
| `GET /api/data/vol?name=&start=` | a volatility index since a date | `GET /market/lake/vol-indices/{name}?start=` | marketdata | viewer | kept |
| `GET /api/data/baskets` | CvC and PvP pseudo-quotes, 1 year | `GET /market/lake/basket-quotes?day=` | marketdata | viewer | kept |
| `POST /api/data/market` | build the market of a past date from the lake (optional SLV leverage) | `POST /market/snapshots/from-lake` 202 | marketdata | trader | kept |
| `POST /api/data/fixings` | load real closes as fixings | `POST /trading/fixings/from-lake` | trading | trader | kept |

### Structuring

| v0 route | Does | v1 route | Package | Role | Status |
|---|---|---|---|---|---|
| `GET /api/presets` | presets and their sheets | `GET /structuring/presets` | structured | viewer | kept |
| `GET /api/schema` | sections, fields, masks (`describe`, JSON Schema) | `GET /structuring/schema` | structured | viewer | kept |
| `POST /api/termsheet/form` | partial sheet -> validated sheet, visible form, field errors | `POST /structuring/termsheet/form` (errors as `problem+json` with field paths) | structured | viewer | kept |
| `POST /api/quote` | quote a sheet (policy model, costs, Greeks, mix band) | `POST /structuring/quotes` 202 | structured | structurer | kept |
| `GET /api/quotes` | recent quotes | `GET /structuring/quotes` (cursor) | server | viewer | kept |
| `GET /api/quotes/{id}` | a saved quote | `GET /structuring/quotes/{quote_id}` | server | viewer | kept |
| `POST /api/script/check` | parse and validate a payoff script, errors at line and column | `POST /structuring/script/check` (length bounded) | structured | viewer | kept |
| `POST /api/script/price` | price a script under a chosen model | `POST /structuring/script/price` 202 (`n_paths` bounded) | structured | structurer | kept |

### Trading

| v0 route | Does | v1 route | Package | Role | Status |
|---|---|---|---|---|---|
| `GET /api/trades` | trades of the book | `GET /trading/trades` (cursor, filters) | server | viewer | kept |
| `GET /api/trades/{id}` | a trade, its versions and original quote | `GET /trading/trades/{trade_id}` | server | viewer | kept |
| `POST /api/trades` | book a trade (from a quote or a sheet) | `POST /trading/trades` (audited) | server | trader | kept |
| `PUT /api/trades/{id}` | edit: a new version | `PUT /trading/trades/{trade_id}` (audited) | server | trader | kept |
| `DELETE /api/trades/{id}` | delete (soft) | `DELETE /trading/trades/{trade_id}` (audited) | server | trader | kept |
| `POST /api/trades/{id}/restore` | undo a delete | `POST /trading/trades/{trade_id}/restore` | server | trader | kept |
| `POST /api/trades/{id}/value` | value one trade at today's market | `POST /trading/trades/{trade_id}/valuations` 202 | structured | viewer | kept |
| `GET /api/trades/{id}/events` | lifecycle events from the fixings | `GET /trading/trades/{trade_id}/events` | structured | viewer | kept |
| `POST /api/book/price` | value the book in groups, delta and vega, reserves | `POST /trading/book/valuations` 202 | structured | viewer | kept |
| `GET /api/upcoming?days=` | observations and payments to come | `GET /trading/events/upcoming?days=` | structured | viewer | kept |
| `GET /api/fixings?underlying=` | fixings | `GET /trading/fixings?underlying=` | server | viewer | kept |
| `POST /api/fixings` | add fixings (raw pasted text or CSV, real-scale conversion, parsed on the server) | `POST /trading/fixings` (audited) | server | trader | kept |
| `DELETE /api/fixings/{u}/{d}` | delete a fixing | `DELETE /trading/fixings/{underlying}/{date}` (audited) | server | trader | kept |
| `GET /api/costs/defaults` | the cost table by group and size, its versions | `GET /trading/cost-defaults` | structured | viewer | kept |
| `PUT /api/costs/defaults` | save a new version of the table | `PUT /trading/cost-defaults` (audited) | structured | trader | kept |

### Risk

| v0 route | Does | v1 route | Package | Role | Status |
|---|---|---|---|---|---|
| `POST /api/risk/run` | Greeks of the book, buckets, betas | `POST /risk/greeks` 202 | risk | trader, risk | kept |
| `GET /api/risk/runs?kind=` | stored runs | `GET /risk/runs?kind=` (cursor) | risk | viewer | kept |
| `GET /api/risk/runs/{id}` | a stored run | `GET /risk/runs/{run_id}` | risk | viewer | kept |
| `POST /api/risk/ladder` | spot ladder under three vol rules | `POST /risk/ladder` 202 | risk | trader, risk | kept |
| `GET /api/risk/scenarios` | scenario library (versions, signatures) | `GET /risk/scenarios` | risk | viewer | kept |
| `PUT /api/risk/scenarios` | edit and sign scenarios | `PUT /risk/scenarios` (`signed_by` = the authenticated user, audited) | risk | risk | kept |
| `POST /api/risk/scenarios/run` | run the scenarios on the book | `POST /risk/scenarios/runs` 202 | risk | trader, risk | kept |
| `POST /api/risk/var` | historical VaR (1 day, 10 days, stressed), contributions | `POST /risk/var` 202 | risk | risk | kept |
| `GET /api/pnl?from=&to=` | cumulated explained P&L from EOD runs | `GET /risk/pnl?from=&to=` | risk | viewer | kept |
| `POST /api/pnl/eod` | EOD runs for a period (idempotent backfill) | `POST /risk/eod-runs` 202 (idempotency key = the run key) | risk | risk | kept |

### Quant labs

| v0 route | Does | v1 route | Package | Role | Status |
|---|---|---|---|---|---|
| `GET /api/vollab/days` | days with option quotes | `GET /quant/vollab/days` | marketdata | viewer | kept |
| `GET /api/vollab/quotes` | quotes of a day (bid, ask) | `GET /quant/vollab/quotes?underlying=&day=&source=` (403 for raw quotes and levels when the request comes from beyond this machine, as v0's `shared` mode) | marketdata | quant | kept |
| `POST /api/vollab/fit` | fit SSVI, SVI, Heston against the quotes | `POST /quant/vollab/fits` 202 | marketdata | quant | kept |
| `GET /api/vollab/runs` | stored fits | `GET /quant/vollab/runs` | marketdata | viewer | kept |
| `POST /api/vollab/runs/{id}/reproduce` | replay a stored fit | `POST /quant/vollab/runs/{run_id}/reproduce` 202 | marketdata | quant | kept |
| `GET /api/replay/runs` | replay runs (desk and versioned) | `GET /quant/replay/runs` | marketdata | viewer | kept |
| `POST /api/replay/run` | replay a preset over its life on real closes | `POST /quant/replay/runs` 202 | marketdata | quant | kept |
| `GET /api/replay/runs/{id}` | a replay run | `GET /quant/replay/runs/{run_id}` (id checked against the registry, never a path) | marketdata | viewer | kept |
| — | engine benchmarks and precisions (CLI only in v0) | `GET /quant/engine` (engines available, last benchmark) | native | viewer | new route for an existing function (session 2, Quant > Engine) |

### Jobs

| v0 route | Does | v1 route | Package | Role | Status |
|---|---|---|---|---|---|
| `GET /api/jobs/{id}` | state, progress, elapsed, result or error | `GET /jobs/{job_id}`, plus `GET /jobs/{job_id}/events` (SSE) | server | owner or admin | kept |
| `DELETE /api/jobs/{id}` | cancel | `POST /jobs/{job_id}/cancel` | server | owner or admin | kept |
| — | running jobs in the status bar (`health`) | `GET /jobs?state=running` | server | viewer | kept, own route |

### Standalone HTML reports

| v0 route | Does | v1 route | Role | Status |
|---|---|---|---|---|
| `GET /report/quote/{id}` | one-file report of a quote | `GET /structuring/quotes/{quote_id}/report` | viewer | kept |
| `GET /report/book` | the book with its reserves | `GET /trading/book/report` | viewer | kept |
| `GET /report/desk` | the whole desk on one page (snapshot) | `GET /trading/desk-snapshot` | viewer | kept |
| `GET /report/risk` | the last Greeks, ladder, scenarios, VaR | `GET /risk/report` | viewer | kept |

Reports carry inline CSS and SVG and no script: they are served with their own CSP (`default-src 'none'; style-src
'unsafe-inline'; img-src data:`), see `security.md`.

### Access and sharing

| v0 | Does | v1 | Status |
|---|---|---|---|
| shared token (`--token`, `?token=`, cookie `optlab_token`) | access beyond localhost | local accounts, sessions, roles (`POST /auth/login`, `POST /auth/logout`, `GET /auth/me`) | **dropped** (decided 07/11, §1 of the handoff) |
| `--tunnel` (localhost.run over SSH, `tunnel.py`) | a public link to the desk | none: the real instance is never exposed; the public showcase is the static demo on GitHub Pages | **dropped** (decided 07/11) |
| `desk.shared` (403 on raw quotes and levels beyond this machine) | personal-use data never leaves the PC | kept as a rule of the market routes when the LAN profile is on | kept |
| `netcheck.py` (Windows firewall, network profile; read-only) | says why the LAN cannot connect | `GET /admin/network` (fixed arguments only) | kept |

## 2. Screens of the desk (14, `optlab/app/static/js/views/`)

The old front stays in `legacy/desk-static/` (not served) as the reference until session 2 reaches parity. Spaces are
those of `HANDOFF_REFACTOR_2_UX.md` §3, to be confirmed at its F0.

| Screen (`#/...`) | What it holds | v1 routes | Session 2 space | Status |
|---|---|---|---|---|
| Overview (`overview`) | market card (snapshot, valuation, spot model scale, ATM vols, 1y zero rate, calibrated up to), book card (value, delta, vega, trades by status), next 60 days, recent quotes, desk snapshot link | `market/context`, `trading/trades`, `trading/events/upcoming`, `structuring/quotes` | home pages by role | kept |
| Pricer (`pricer`, `pricer/quote/{id}`) | preset and mask cards, form from the schema by section (`visible_if`, computed fields, percent input), advanced options (control model, fast models, forward control variates, delta convention), quote job with progress and cancel, results: coupon or price ± SE, method and policy reason, control gap, desk price lines (base, provisions, bumps, digitals date by date), SLV mix band, flows by date, termination probabilities, expected life, Greeks, markets used, generated script, copy sheet, book it, export report | `structuring/*`, `jobs/*`, `trading/trades` | Structuring > Builder | kept |
| Book (`book`) | trades table (sortable, status filter), totals, price the book (precision, delta and vega), positions with unit price, reserve left, risk by underlying, simulations (groups), delete with undo, book report | `trading/trades`, `trading/book/valuations`, `trading/book/report` | Trading > Book | kept |
| Trade (`trade/{id}`) | term sheet, value at today's market, events, reserve frozen at issue, versions, original quote, edit, quote a copy | `trading/trades/{id}`, `.../valuations`, `.../events` | Trading > Trade | kept |
| Lifecycle (`lifecycle[/{id}]`) | fixings editor (paste a column, CSV file, real levels converted with the real spot), per-trade timeline (paid, missed, knocked in, next observation) | `trading/fixings`, `trading/trades/{id}/events` | Trading > Lifecycle | kept |
| Cost defaults (`costs`) | the cost table by group and notional tier, starting values in italics, versions | `trading/cost-defaults` | Trading > Pricing policy | kept |
| Market (`market`) | snapshots and switch, SVI smile per maturity, ATM term, forward and discount, calibration fit, model-risk table, other underlyings (worst-of inputs and correlations) | `market/*`, `quant/model-risk` | Quant > Market explorer, and the Market drawer | kept |
| Risk (`risk`) | Greeks run (delta three rules, gamma 1 % and 5 %, vega, buckets, theta split, rho, epsilon, correlation), ladder chart, scenarios (edit and sign), VaR (distribution, contributions, VaR day), risk report | `risk/*` | Trading > Book risk, and Risk > VaR, Stress | kept |
| P&L explained (`pnl`) | EOD run for a period, cumulated P&L against the Greeks' prediction, cascade by step, by trade, new trades, reserve released, unexplained | `risk/pnl`, `risk/eod-runs` | Trading > P&L explained, Risk > EOD runs | kept |
| Script lab (`script`) | editor with line numbers, errors at line and column, language reference, price under a model | `structuring/script/*` | Quant > Script lab | kept |
| Vol lab (`vollab`) | day and source picker, smile with bid and ask, error against the spread by model and maturity (RMSE bp, share within the spread), stored runs, reproduce | `quant/vollab/*` | Quant > Vol lab | kept |
| Replay (`replay`) | preset, strike date, frequency, model; value and cash over the life, events, performance since strike, P&L explained with residual, reserve released | `quant/replay/*` | Quant > Replay lab, Structuring > product backtest | kept |
| Data (`data`) | coverage and origin, universe, quality issues, volatility indices since 2018, CvC and PvP, a market at a past date (build, price on it, load closes as fixings) | `market/lake/*`, `market/snapshots/from-lake`, `trading/fixings/from-lake` | Quant > Market explorer (content), Admin > Data pipelines (operation) | kept |
| Settings (`settings`) | display (theme), default precision, Monte Carlo workers, valuation and snapshot, server state, sharing (LAN state from `netcheck`, public address), clear local preferences | `admin/system`, `admin/network` | user menu (preferences), Admin > System | kept; the public address part dropped with the tunnel |

Shell functions of the old front, for parity: status bar (connection, snapshot, running jobs), keyboard shortcuts
(`g` + letter, `?`, `t` for the theme), light and dark themes, sortable `table.data`, form state and last results kept in
`localStorage`, delete with undo, confirmation dialogs.

## 3. Scripts (19 in `scripts/`, 1 in `cuda/bench/`) -> `optlab` CLI

Every option is kept unless the row says otherwise. **Done in A7**: every script below is a command of `optlab`
(`optlab.server.cli`, its body in `optlab.server.tools`), `scripts/` is gone, and `test_cli.py` checks this table
against the command tree. One change for all: a list option (`--only`) is comma-separated or repeated
(`--only curves,fx`), where v0 took space-separated values.

| Script | Does | Subcommand | Options | Status |
|---|---|---|---|---|
| `run_calibration.py` | SSVI -> SVI -> Heston -> SLV on the simulated market or the latest SPX snapshot, writes `results/<source>_calibration.json` and figures | `optlab calib run` | `--source simulated\|spx`, `--mix`, `--particles`, `--paths`, `--skip-sweep`, `--replot`; the output goes to the settings' `results` (`results/` of a checkout, `optlab-data/archive/` for an installed wheel; the versioned JSON stays, in compact JSON: ADR-0023) and the charts to `<reports>/figures` | kept |
| `make_report.py` | `reports/calibration.md` from the calibrations | `optlab calib report` | none | kept |
| `plots.py` | report charts (matplotlib, site palette) | library module of `calib report` (extra `[reports]`) | — | merged |
| `build_widget_data.py` | `docs/pricer-data.js` from the SPX calibration | `optlab demo widget-data` | none | kept until session 2 replaces `docs/lab.js` |
| `download_spx.py` | SPX chain snapshot to `data/raw/` | `optlab calib download-spx` | none | kept |
| `fix_asian_exotic.py` | one-off recomputation of the Asian cell in `results/*.json` | — | `--source` | **dropped** (agreed at A0): a migration already applied to the versioned results |
| `rewrite_leverage.py` | one-off rewrite of the stored SLV leverage at every time step | — | `--source`, `--dry-run` | **dropped** (agreed at A0): already applied |
| `lake_update.py` | update the lake from public sources, quality report | `optlab lake update` | `--only`, `--lake`, `--yahoo-chains`, `--import-sample`, `--dolt`; new `--as-job` (the public sources, left to the worker) | kept (and the `lake` schedule of the worker, off by default) |
| `lake_derive.py` | derived tables: curves, carry, bank, FX, surfaces, correlation, pairs, observed fits | `optlab lake derive` | `--only`, `--lake` | kept |
| `lake_fx_check.py` | FX against H.10 and CME, quanto size: `reports/fx_check.md` | `optlab lake fx-check` | `--lake`, `--n-paths` | kept |
| `lake_surface_test.py` | reconstruction against real chains: `reports/surface_reconstruction.md` | `optlab lake surface-test` | `--step`, `--lake` | kept |
| `lake_regime_test.py` | the same by level of the VIX, crash held out | `optlab lake regime-test` | `--step` | kept |
| `lake_replay.py` | replay products on real closes: `reports/replay.md` | `optlab lake replay cost\|run` | `--every`, `--paths`, `--model`, `--only`, `--leverage-every`, `--out`, `--costs` | kept |
| `pricing_examples.py` | the desk price on the real lake: `reports/pricing.md` | `optlab structured pricing-report` | `--day`, `--paths`, `--workers` | kept |
| `structured_examples.py` | example quotes, autocall model-risk table, timings | `optlab structured examples` | `--paths`, `--batch`, `--quick` | kept |
| `seed_showcase.py` | the showcase store (a dozen trades on real history) | `optlab demo seed-showcase` | `--store`, `--day`, `--n-paths` | kept |
| `risk_eod.py` | EOD, backfill, P&L, VaR, ladder from the console | `optlab risk eod\|backfill\|pnl\|var\|ladder` | `--store`, `--date`, `--from`, `--to`, `--precision`, `--engine`, `--workers`, `--days`, `--no-greeks`, `--market`, each on the subcommands it concerns; new `--as-job` (all but `pnl`) | kept |
| `bench_accel.py` | NumPy against the CPU and GPU engines | `optlab bench accel` | `--paths`, `--batch`, `--workers`, `--cases`, `--engines`, `--no-leverage`, `--desk`, `--out` | kept |
| `cuda/bench/bench_numpy.py` | NumPy timings for the CUDA benchmark table | `optlab bench numpy` | new `--out` (default: beside `bench.exe`'s sources) | kept |

Commands with no v0 script (A6, A7):

| Command | Does |
|---|---|
| `optlab serve [--open] [--demo-data]` | the API (was `python -m optlab.app`, then `python -m optlab.server`, still an alias) |
| `optlab worker` | the job queue's worker alone (`OPTLAB_WORKER=external`; was `python -m optlab.server.worker`) |
| `optlab users create\|list\|roles\|password\|disable\|enable` | accounts (was `python -m optlab.server.users`) |
| `optlab backup [--lake]`, `optlab restore PATH\|latest [--lake]` | a copy of the database (and of the lake) now; a checked restoration that keeps what it replaces |
| `optlab migrate [--v0 FOLDER]` | the schema to this code's revision; a v0 store imported |
| `optlab jobs list\|show\|wait\|cancel` | the job queue from the console |
| `optlab api export\|check` | the OpenAPI document (was `python -m optlab.server.openapi`) |
| `optlab check fast\|full\|gpu\|golden` | the test suites and the golden numbers, from a checkout |
| `optlab demo data` | the demo folder of made-up numbers (handoff §7.7) |
| `optlab version` | the packages' versions and the engines of the machine |

## 4. Desk command line (`python -m optlab.app`)

| Option | v1 | Status |
|---|---|---|
| `--host` (default `127.0.0.1`) | `OPTLAB_HOST`, `optlab serve --host`; beyond `127.0.0.1` only through the `lan` profile (Caddy, TLS) with Luca's agreement | kept |
| `--port` (8765) | `OPTLAB_PORT`, `optlab serve --port` | kept |
| `--token` | replaced by accounts | **dropped** (decided) |
| `--workers` | `OPTLAB_WORKERS`, `optlab serve --workers`, `optlab worker --workers` | kept |
| `--engine auto\|gpu\|cpu\|numpy` | `OPTLAB_ENGINE`, `optlab serve --engine`, `optlab worker --engine` | kept |
| `--store` | `OPTLAB_DATABASE` (the SQLite database), `optlab serve --store` | kept |
| `--calibration` | `OPTLAB_CALIBRATION` (default: the SPX calibration in `optlab-data/archive/`) | kept |
| `--market` | `OPTLAB_MARKET`, `optlab serve --market` (initial market in use) | kept |
| `--tunnel` | none | **dropped** (decided) |
| `--open` | `optlab serve --open` | kept |
| `--log` | `optlab serve --log`, `optlab worker --log` | kept |

## 5. Reports and published outputs

| Output | Produced by | Status |
|---|---|---|
| `reports/calibration.md`, `reports/figures/*.png` | `optlab calib run` then `calib report` | kept |
| `reports/pricing.md` | `optlab structured pricing-report` | kept |
| `reports/replay.md` | `optlab lake replay run` | kept |
| `reports/risk.md` | measured runs of `optlab risk ...` (written by hand from them) | kept |
| `reports/fx_check.md` | `optlab lake fx-check` | kept |
| `reports/surface_reconstruction.md` | `optlab lake surface-test`, `regime-test` | kept |
| `reports/showcase.md` | guide of the showcase (its commands are `optlab` ones since A7) | kept |
| `results/spx_calibration.json`, `results/simulated_calibration.json` | `optlab calib run` | kept in the repo, in compact JSON (ADR-0023, superseding the move out of ADR-0016) |
| `results/pricing_examples.json`, `replay_*.json`, `surface_*.json` (small) | their subcommands | kept in the repo |
| `docs/` (GitHub Pages: `index.html`, `lab.js`, `pricer-data.js`) | by hand, `demo widget-data` | frozen; replaced by the demo build in session 2 |
| `space/app.py` (Gradio, not deployed) | — | **dropped** (decided) |

## 6. Library functions without a route today

Kept in the packages, reachable from the CLI or Python only: `optlab.core` closed forms, CRR, Heston Fourier, the
Monte Carlo pricers of `products.py`, calibration steps; `structured.qmc` (opt-in RQMC), `fastmodels`; the risk engine's
lower-level functions. The JS re-implementation in `docs/lab.js` (American PDE with PSOR, Levy moment matching, Heston QE)
stays as is until session 2.
