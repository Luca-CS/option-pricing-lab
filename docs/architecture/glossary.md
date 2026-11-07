# Glossary

The words of the desk, as the code, the API and the UI use them. Draft of A0 (7 November 2026), completed in A9.
Session 2 keeps to this vocabulary in the UI.

## Products and quotes

| Term | Meaning |
|---|---|
| **Term sheet** | The structured description of a product: sections (`general`, `coupon`, `downside`, `wrapper`, hedging costs...) of typed fields with dotted paths (`coupon.rate`). `TermSheet` in `structured/schema.py`. |
| **Mask** | A product family that decides which sections and fields exist: `autocall` or `bullet`. |
| **Preset** | A named, filled term sheet of one mask (`phoenix`, `athena`, `barrier_reverse_convertible`, `reverse_convertible`, `airbag`, `capital_protected`). |
| **`visible_if`** | The condition under which a field is shown and validated; the form is re-asked from the server when a choice changes. |
| **Script** | A product written in the payoff script language (`structured/script/`): a hand-written lexer and parser, an AST, a vectorised evaluator. A term sheet compiles to a script. |
| **Compiled product** | The script, its timeline (observation and payment dates on a calendar) and its parameters, ready to price. |
| **Profile** | The class of a script read from its AST: `european`, `path_dependent`, `callable`. |
| **Policy** | The rule that maps a profile to a pricing base and its controls (`structured/policy.py`). |
| **Base** | The price the policy chooses: replication on the SVI smile for a European product, local vol Monte Carlo otherwise. |
| **Provision** | A model reserve added to the base when the gap to a richer model (SLV; local correlation for a worst-of) goes against the desk; floored at zero. |
| **Bump** | A signed move of an input (vol, skew, correlation, dividends, repo, spot) chosen by the trader, priced on the base's random numbers. |
| **Digital** | A discontinuous payoff priced as a call spread on the side against the desk; the width comes from the cost table. |
| **Desk price** | Base + provisions + bumps + digitals (`structured/costs.py`). |
| **Client price** | The price shown to the client: the desk price plus the margin; in coupon mode the coupon is solved on it. |
| **Solve** | `wrapper.solve`: `coupon` (find the coupon that gives the target price) or `upfront` (price at a given coupon). |
| **Cost defaults** | The table of bumps, SLV mix and digital limits by group of underlyings and notional tier, versioned. |
| **Group (of underlyings)** | The row of the cost table a name takes (`index_us`, `index_eu`, `equity_us`...); a worst-of takes its most prudent group. |
| **Reserve** | The lines of a quote frozen when it is booked (`structured/reserves.py`); released over the trade's life. |
| **Quote** | One pricing of a term sheet on a market: its numbers, method, policy reason, snapshot and SE; `quote_id`. |

## Book and lifecycle

| Term | Meaning |
|---|---|
| **Trade** | A booked term sheet with a quantity, a strike date and a counterparty; versioned on every edit; `trade_id`. |
| **Position** | A trade (or a script) with its quantity inside a book valuation. |
| **Book** | The live trades, valued on shared simulations: one **valuation group** per single name, one for the worst-ofs. |
| **Strike date** | The date the product's initial fixing is taken. |
| **Valuation date** | The date of the market a number is computed on. |
| **Fixing** | A close of an underlying on an observation date, in the model's scale (see spot scale). |
| **Event** | What happened or will happen to a trade: coupon paid or missed, call, knock-in, observation, redemption. |
| **Lifecycle** | Fixings in, events out; a running product is priced from its past fixings. |

## Market

| Term | Meaning |
|---|---|
| **Snapshot** | An identified calibrated market: spot (100), curves, SVI surface, Heston parameters, SLV leverage, provenance; `snapshot_id` (content-addressed, for example `SPX-20261104T1600-8b7fa10118`). |
| **Market in use** | The desk-wide snapshot the screens and new jobs price on; a job keeps the one it was submitted with. |
| **Origin** | Where a market part comes from: `observed` (read from a market source), `derived` (computed from observed data), `reconstructed` (rebuilt from indices and an assumed shape), `synthetic` (an assumption). Shown on screen next to the number. |
| **Layer** | One of the three never-mixed surface layers of the lake: observed fits, reconstructed, synthetic. |
| **Point in time (PIT)** | `market_at(name, day, as_of)`: only rows dated at or before `day`, as known at `as_of`. |
| **`as_of`** | The knowledge date of a bitemporal read: what the lake held then. |
| **Spot scale** | Real spot / model spot. The model is always at spot 100; real levels cross the boundary through this ratio and stay on the owner's machine. |
| **Lake** | The historical store: raw files with a manifest, curated Parquet tables (append-only, `origin`, bitemporal), DuckDB views. |
| **Source** | One market data provider with its pure `parse` function (`marketdata/sources/`). |
| **Universe** | The instrument registry: names, currencies, calendars, tickers, groups; only `enabled` names are loaded and offered. |
| **CvC, PvP** | Call-versus-call and put-versus-put pseudo-quotes: an option on a basket against the same option on its members, from which a correlation is read back. |
| **Local correlation** | A correlation that depends on the basket's level (`structured/localcorr.py`), fitted on the correlation skew. |
| **Quanto** | A product paid in another currency than its underlying's; adds a drift from the FX vol and the name-FX correlation. |

## Models and numbers

| Term | Meaning |
|---|---|
| **BS, Heston, LV, SLV** | Black-Scholes, Heston (Lewis Fourier for vanillas), Dupire local volatility from the SVI surface, Heston stochastic local volatility with a leverage calibrated by the particle method. |
| **Mix** | The SLV mixing fraction: the share of the vol of vol kept from Heston; not calibrated, a cost-table input, with a band repriced at several values. |
| **SE** | Standard error of a Monte Carlo number, computed on antithetic pair averages. Every Monte Carlo value is `{value, se}`. |
| **Method** | How a number was computed (`replication`, `monte_carlo`, `closed_form`, `fourier`...), with its model. |
| **Engine** | Where Monte Carlo paths come from: `numpy` (the library's default), `cpu` or `gpu` (the native engine), `auto`. Prices depend only on `(seed, n_paths)`, whatever the engine's batches. |
| **Precision** | A preset of paths and batch: `quick` (20,000), `standard` (100,000), `precise` (200,000), `max` (2,000,000, native engine only). |

## Risk

| Term | Meaning |
|---|---|
| **Shock** | A move of the market (spots, vols per node or tenor, curves, dividends, repo, correlations, FX, roll of the date) applied before a revaluation (`risk/shocks.py`). |
| **Measure** | One named output of a revaluation (a Greek, a ladder point, a scenario P&L). |
| **Risk group** | A book part revalued on one seed and number of paths, so differences are paired path by path. |
| **Vol rule** | What the surface does when the spot moves: `sticky moneyness`, `sticky strike`, or `realistic` (ATM vols move by measured spot-vol betas). |
| **Bucket** | A tenor or a node (strike x tenor) of a Greek; node vegas sum to the parallel vega exactly. |
| **Ladder** | P&L of the book on a grid of spot moves under each vol rule. |
| **Scenario** | A hypothetical shock stored as signed, versioned data; `signed_by` is the authenticated user. |
| **VaR, ES** | Historical value at risk (99 %) and expected shortfall (97.5 %) by full revaluation: 1 day, 10 days (overlapping), stressed (a searched window). |
| **VaR day** | The historical day whose scenario gives the VaR. |
| **EOD run** | The end-of-day valuation and Greeks of the book on the lake's closes, stored and replayable. |
| **Run, run key** | A stored computation with its parameters and code version; the key makes a backfill idempotent (asked again, it is read back). |
| **Cascade** | The explained P&L from one close to the next by full revaluations, one factor at a time (time, spot, vol, skew, rates, dividends, correlation, FX). |
| **Unexplained** | The cascade's P&L minus the Greeks' prediction, per step. |

## Platform

| Term | Meaning |
|---|---|
| **Job** | A long computation run by the worker: kind, parameters, frozen market context, user, state, progress, step, engine, result or error; `job_id`. |
| **Worker** | The process that owns the GPU, runs the jobs and the scheduler. |
| **Schedule** | A planned job (EOD, lake update, backup) editable by the admin role and audited. |
| **Role, permission** | `admin`, `trader`, `structurer`, `risk`, `quant`, `viewer`; each route declares the permission it needs. |
| **Audit log** | The append-only record of every write: who, when, what. |
| **Golden number** | An output frozen in A0 and compared bit for bit after every step (`tests/golden/`). |
| **Mini-lake** | The synthetic lake the tests generate deterministically, for CI and the demo data. |
| **Showcase** | The demo store: a dozen trades dealt on past dates on real history (`optlab demo seed-showcase`), local only. |
| **Vol lab** | Fits of the models against real option quotes of a day, stored and replayable. |
| **Replay** | A product valued along its life on real closes, with its events and explained P&L. |
