# Risk engine: Greeks, ladders, scenarios, historical VaR and the explained P&L

Run on 6 and 7 November 2026 on the showcase book (`optlab demo seed-showcase`: twelve live trades, plus one quoted on the
desk, on the market `SPX-20261104T1600-8b7fa10118`), on the RTX 2070 Super at Standard precision (100,000 paths) unless
said otherwise. Amounts are in the payment currency (USD) from the desk's side; the notes of the book are sold, so the
desk is short their coupons and long their puts. Every number of a Monte Carlo revaluation carries its standard error, of
the paired difference to the base on the same random numbers. Nothing here is a market level.

## Method

**One engine.** A risk is a list of shocks (`optlab/risk/shocks.py`) revalued by one engine (`engine.run_group`). A shock moves
spots (relative), vols (parallel, relative, per node of the grid, per tenor, skew rotation), discount and forward curves per
tenor, dividend yields and flows, repo, correlations, FX, the quanto drift, and rolls the valuation date; the vol rule says
what the surface does when the spot moves (sticky moneyness, sticky strike, or realistic: at-the-money vols move by
beta_T ln(1 + h) with betas measured on the lake). Every revaluation of a group runs on the group's seed and number of paths,
so differences are paired path by path. Shocks that only move spots, forwards or discounts ride on one simulation: the models
are simulated in x = ln(S / F), so a spot move multiplies the paths and a forward move multiplies them by F'(t) / F(t), which
equals a new simulation on the moved market to 1e-10 (tested). Vol, correlation and the roll resimulate.

**The grid.** Strikes 60 to 140 % of the forward and tenors 1m to 5y. Tenor tents are smootherstep partitions of unity. Strike
tents had to be smoothed: with plain smooth steps between nodes 5 % apart, a one-point bump of the 70 % node at three months
gave butterfly arbitrage (Durrleman g down to -0.15). The strike tents are therefore hats smoothed by a Gaussian of width
c(k) sqrt(T), c = 0.04 at the money and 0.12 in the wings; with them no node bumped by one point, up or down, breaks the
butterfly condition on the SPX surface. Five calendar flags remain, bounded (dw/dT > -0.006) and confined to the right wing
at short tenors, where the SPX surface itself has dw/dT of only 0.003. The price of the smoothing: the vega of a nine-month
at-the-money call sits at 85-90 % on the 95-105 % x 6m-1y nodes, not on one node. The sum of the nodes equals the parallel
bump exactly by construction; against the central parallel vega the one-sided nodes differ by the nonlinearity (0.4 % on a
put).

**Greeks** (`greeks.py`, desk cash conventions): delta per +1 % spot (sticky moneyness, sticky strike and realistic side by
side), gamma at 1 % and at 5 % (same unit), vega per point, per +1 % relative, per tenor, per node, net, gross and weighted
(sqrt(1 y / T), also revalued as one shock), vanna, volga, skew vega, theta (one business day, market frozen, split into
carry, events and decay), rho total, discount and forward, epsilon and repo per bp and per tenor, correlation per pair and
parallel, cross gamma, quanto. Digitals are smoothed by the evaluator's centred ramp of 0.5 % for the Greeks, and the bias
on the price is reported.

**Ladder**: spots -30 to +10 % under the three rules; the rule applies to the point, and the Greeks around it are the
sticky-moneyness Greeks of the shocked market. **Scenarios**: signed, versioned data in the risk store; the defaults are
starting values; every shocked surface goes through `surface_arbitrage`. **VaR**: each day of history becomes a shock on
today's market (spot returns, at-the-money vol changes per tenor and the 90-110 % skew on the reconstructed surface layer,
zero rates per tenor, dividend yields as zero rate less implied carry, 120-day realised correlations on the days both names
trade); 1-day over 500 business days, 10-day on overlapping moves, the stressed window searched over the lake's history by
the delta-gamma-vega approximation, frozen, then revalued in full. **P&L**: EOD runs stored and replayable, and a cascade of
full revaluations from one close to the next on the seeds of the day before: time, spot, vol (at-the-money per tenor), skew
(the rest of the surface), rates, dividends (with repo and quanto drift), correlation, FX.

## Validation (tests/test_risk_*.py)

| Test | Reference | Result |
|---|---|---|
| Vanilla call and put, replicated on a flat surface | Black-Scholes closed forms | delta, gamma, vega, rho, epsilon within 0.2 %; vanna and volga within 2 % (central differences); theta = analytic x 1/365 within 1 % |
| Same call by local vol Monte Carlo on the engine | closed forms | delta, vega, rho within 4 SE |
| Smoothed digital, Monte Carlo | the centred call spread's closed forms | delta, vega, gamma within 4 SE; smoothing bias within 4 SE of its closed form |
| Sticky strike and sticky moneyness deltas on the SPX smile | Black-Scholes at the fixed or moved implied vol | 1e-4 |
| Node vegas | partition of unity, parallel bump | sum of nodes = parallel to 1e-12 on the surface; non-bracketing tenors exactly zero |
| Rho and epsilon per tenor, 0.8-year vanilla | parallel bump, closed forms | only the 6m and 1y tenors move; sum = parallel within 0.1 % |
| Roll of an autocall the day before its observation | the call at a frozen spot | the event falls into cash exactly |
| Worst-of put on two names, Black-Scholes | bivariate integration | value, delta, cross gamma, correlation within 4 SE |
| Ladder of a replicated vanilla | closed form at the shocked spot (and vol, for the realistic rule) | 1e-6 at every point, all rules |
| Ladder point 0 | the base | the same number |
| Six default scenarios on a replicated vanilla | closed forms | 1e-6 (1e-5 for the crash) |
| VaR of a forward | quantile of the historical returns x the position | 1e-12 |
| VaR of a vanilla | delta-gamma-vega | within 10 % |
| Cascade on 12-13/03/2020, the replay's BRC | the total P&L, d's EOD value | steps add up to 1e-9; reconciliation gap under 0.01 |
| EOD replayed, backfill twice | the stored run | the same numbers; the second backfill reuses every run |

## Measured on the showcase book

**Cost of a shock (R0).** A revaluation of the whole book under a vol shock took 1.67 s, most of it the local variance table of
the shocked surface (0.21-0.32 s per name). `TentBumpedSurface.local_var_rows` now builds the table on the base's grid in one go,
with the base's derivatives kept: 0.13 s for a VaR shock (term structure and skew), 0.25 s for a node, 0.08 s for a parallel
bump, identical to the per-step table to 2e-12.

**Greeks.** Book delta on SPX -194,792 ± 871 (realistic -233,571 ± 1,422) per +1 %, sticky strike -265,834 ± 2,099; vega on SPX +42,565 ± 1,203 per point;
theta -21,021 ± 2,639 per business day; correlation -4,477 ± 281 per point; rho +2,681 ± 13 per bp. The 1 % gamma of the
book is within its noise (1,364 ± 1,542): barrier books read gamma at 5 %. The first full run (node vegas for SPX, tenor
buckets for the worst-of) took 248 s before the table speedups.

**Spot-vol betas** (489 days to 04/11/2026, reconstructed layer, vol per unit of log return, 1m to 5y): SPX -1.51, -0.96, -0.69,
-0.46, -0.37, -0.31, -0.24, -0.16 (SE 0.04 to 0.006, R² 0.6-0.8); SX5E -1.22 to -0.31; NDX and RUT close to zero beyond a year,
where their reconstructed surfaces are carried by the prior.

**VaR.** Five processes on the GPU, one evaluation thread each, every horizon in one pool and the worst-of's chunks first:
the 1,196 scenarios of the three horizons are revalued in 584 s, and the whole VaR, with the search of the stressed window
over 2018-2026, takes 620 s (10.3 minutes: just above the 10-minute target; 11.5 with the Greeks of the Taylor check). The
first run took 1,012 s, 85 % of it in local variance tables; 3 processes take 271 s for the 1-day horizon against 254 s for 5
or 6, so the shared GPU is now the limit, and the CPU engine is slower. No path was taken off.

| | VaR 99 % | SE of the VaR scenario | ES 97.5 % | VaR day | Delta-gamma-vega VaR |
|---|---|---|---|---|---|
| 1 day, 478 scenarios | 573,576 | 3,275 | 643,073 | 27/05/2025 | 574,791 |
| 10 days, overlapping | 1,768,567 | 5,366 | 1,742,404 | 16/04/2026 | 1,772,189 |
| Stressed, 29/03/2019 - 07/04/2020 | 1,508,111 | 4,243 | 1,493,891 | 06/04/2020 | 1,520,758 |

On the 1-day VaR day, one factor at a time: spot -433,190, vol -135,348, skew +6,535, dividends -12,466, rates -6,618,
correlation -742, interaction +8,253. A first run read the correlation at zero: a holiday of one market broke every 120-day
window; the correlation is now computed per pair on the days both names trade. The 478 scenarios are the days of the last
500 on which every name of the book has a close and a surface. The SE of the VaR scenario is 0.6 % of the VaR, and the numbers
do not depend on the number of processes (the same to the last digit with 3, 5 or 6).

**On the desk.** The Risk screen ran the Greeks of the book in 123 s (179 simulations, node vegas for SPX), the ladder, and
the six starting scenarios in 24 s; the VaR button took 13 min 12 s from the click to the result (the Greeks of the Taylor
check, the search of the stressed window, the three horizons), with the same numbers as the script to the last digit. A first
try from the desk ran for over an hour: the desk sets the engine in its own process (`native.set_default`), which spawned
processes do not inherit, so the VaR's processes simulated with NumPy. They are now told the engine (`var.worker_settings`,
tested). The P&L page was checked on a backfill of 8-12 June 2026 started from the page: over the week the book lost 94,095,
the Greeks of each day before predicted -98,113, so they explain 96 % of it; the largest unexplained step is the vol
(57,735 against 64,023). The "Equity crash" starting value is flagged: its realistic vols lift the one-month vol by
34 points and the bump fades too fast between the 1m and 3m tenors, so the shocked surfaces fail the calendar check (dw/dT
down to -0.028 on SPX), not the butterfly one; the scenario is valued (local variance floored) and the flag shows next to it.

**Explained P&L.** On 04/11/2026 (SPX +1.6 %), a BRC sold in June: P&L -0.590 per 100, the Greeks of the day before predict
-0.584, so they explain 99 % of it; by step, time -0.025 ± 0.021 (Greeks -0.028), spot -0.213 ± 0.012 (-0.214), vol
-0.415 ± 0.018 (-0.371), skew +0.071 ± 0.013 (+0.036), rates -0.004, dividends -0.003; the cascade ends on the EOD value of the
day exactly. On 13/03/2020 the cascade of the replay's BRC adds up to the total to 1e-9, where the replay's Taylor
expansion left a residual of 2.0.

## What is not done, or not measured

- Quanto Greeks (name-FX correlation, FX vol) need the quanto links in `RiskEnv.quanto`; the desk does not pass them yet, so the
  quanto names of the worst-of show none. FX is a currency conversion only, zero in a one-currency book.
- SLV is an option of the settings (`model="slv"`): a vol shock then recalibrates the leverage. It is not tested beyond the code path.
- The Monte Carlo theta is noisy (0.003 of SE on a call's 0.014 at 100,000 paths): the roll moves the time grid, so the paths
  of the two dates do not pair exactly. It is exact in replication.
- The light backtest (VaR exceptions against the EOD P&L, Kupiec) needs a backfill of a year or more; `var.kupiec` is in place
  and tested, the backtest of the showcase is not run.
- Correlation shocks move the realised correlation, not an implied one.
