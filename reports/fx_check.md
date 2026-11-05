# FX in the lake and the quanto adjustment

Written by `scripts/lake_fx_check.py`, 2026-11-05. Gaps and statistics only: no vendor's levels.

## What is loaded

| Item | Source | Origin |
|---|---|---|
| EURUSD spot | ECB reference rate, 14:15 CET | observed |
| USDJPY, GBPUSD spots | Crosses of two ECB rates of one fix | derived |
| The three spots, again | Fed H.10 noon rates through FRED, kept as a check | observed |
| EURUSD forwards, 1 month to 5 years | Covered parity on the Treasury and AAA curves | derived |
| FX volatility | Realised, 60, 120 and 250 days | derived, method `realized` |
| Name against pair correlation | Realised on overlapping 3-day returns | derived |

**No forward for USDJPY and GBPUSD:** the desk has no yen or sterling curve. The lake holds a `no_curve` quality issue for each, and a term sheet can only pay in USD or EUR.

**No implied FX volatility.** Cboe answers 403 on EVZ and Yahoo no longer serves it. A realised volatility leaves out the FX volatility premium, and the provenance of each quanto market says what it used through `vol_origin` and `vol_method`.

Latest forward date: 2026-11-04. On 2026-11-04: EURUSD volatility 4.9 % (derived, realized), SX5E against EURUSD +0.29 (derived), SX5E surface `reconstructed` (reconstructed).

## Spots: ECB against H.10

| Pair | Days | Median gap (bp) | 99 % (bp) | Largest (bp) | Correlation, 1 day | Correlation, 5 days |
|---|---|---|---|---|---|---|
| EURUSD | 2918 | 16 | 103 | 277 | 0.626 | 0.921 |
| USDJPY | 2918 | 15 | 106 | 358 | 0.702 | 0.936 |
| GBPUSD | 2918 | 18 | 101 | 238 | 0.703 | 0.940 |

The two sources are about four hours apart, which alone decorrelates one-day returns; five-day returns much less. The quality report lists the days above 2 %.

## Forwards: covered parity against the CME future

Each quarterly EUR contract over the spot, against the same carry from our curves at the contract's exact maturity: 9 contracts, 9822 contract-days, 2020-09-01 to 2026-11-04, three months or more from expiry. Yahoo's two closes are hours apart and that noise is divided by the maturity, so the longer buckets are the ones to read.

| Time to expiry | Days | Futures carry (bp/yr) | CIP on our curves (bp/yr) | Median gap (bp/yr) | 90 % of absolute gap (bp/yr) | Correlation |
|---|---|---|---|---|---|---|
| 3 to 6 months | 284 | 180 | 192 | -13 | 180 | 0.33 |
| 6 to 12 months | 744 | 162 | 157 | -4 | 93 | 0.53 |
| 1 to 2 years | 2094 | 156 | 162 | -9 | 46 | 0.64 |
| 2 to 5 years | 6700 | 148 | 177 | -27 | 53 | 0.80 |

| Year, 6 months and more | Days | Futures carry (bp/yr) | CIP on our curves (bp/yr) | Median gap (bp/yr) | 90 % of absolute gap (bp/yr) | Correlation |
|---|---|---|---|---|---|---|
| 2020 | 101 | 106 | 112 | -4 | 16 | 0.62 |
| 2021 | 718 | 130 | 139 | -10 | 25 | 0.77 |
| 2022 | 1410 | 166 | 214 | -44 | 75 | 0.63 |
| 2023 | 2167 | 127 | 160 | -32 | 51 | 0.83 |
| 2024 | 2214 | 158 | 178 | -21 | 43 | 0.67 |
| 2025 | 1810 | 175 | 191 | -10 | 58 | 0.55 |
| 2026 | 1118 | 135 | 142 | -6 | 51 | 0.20 |

From six months out: median gap -22 bp a year, correlation 0.67. A negative gap means our curves overstate the dollar-euro differential the market pays. Our curves are Treasury and AAA, not OIS, and the forwards carry no cross-currency basis.

## Correlation: does the hour of the fix matter?

Rolling 120-day correlation with EURUSD on 3-day returns, on the ECB fix and on H.10.

| Name | Median, ECB | Median, H.10 | Median difference | 95 % of difference | Range, ECB |
|---|---|---|---|---|---|
| SPX | +0.18 | +0.20 | 0.04 | 0.12 | -0.45 to +0.61 |
| SX5E | +0.12 | +0.12 | 0.06 | 0.15 | -0.59 to +0.66 |

## The size of the adjustment

EURUSD realised volatility, 120 days: median 7.2 %, range 4.6 to 12.7 %.

| Date | Case | rho | FX vol | ATM 1y | Forward shift 1y (bp) | 2y (bp) | 5y (bp) |
|---|---|---|---|---|---|---|---|
| 2020-03-23 | SX5E paid in USD | -0.11 | 6.8 % | 31.8 % | +21 | +35 | +75 |
| 2020-03-23 | SPX paid in EUR | -0.07 | 6.8 % | 30.4 % | +14 | +23 | +50 |
| 2022-07-01 | SX5E paid in USD | +0.46 | 8.7 % | 26.8 % | -108 | -207 | -453 |
| 2022-07-01 | SPX paid in EUR | -0.36 | 8.7 % | 23.6 % | +74 | +139 | +323 |
| 2026-11-04 | SX5E paid in USD | +0.29 | 4.9 % | 20.0 % | -29 | -57 | -138 |
| 2026-11-04 | SPX paid in EUR | -0.23 | 4.9 % | 17.9 % | +20 | +41 | +104 |

A positive shift means a negative correlation between the name and X, the domestic price of its currency.

## A Phoenix on SPX and SX5E, 2026-11-04

Local vol, 100,000 paths for each of 5 seeds, each name on its own surface. SE is that of the mean.

| Paid in | Coupon (bp p.a.) | SE (bp) |
|---|---|---|
| USD, no adjustment (the old mix) | 1094 | 4.3 |
| USD, SX5E quanto | 1100 | 4.3 |
| EUR, SPX quanto | 921 | 4.2 |

The adjustment alone moves the coupon by +5.9 bp a year, SE 0.3, from 5 seeds on common draws.

## What the deterministic adjustment leaves out

The forward is adjusted with the at-the-money forward volatility. Under local vol the true quanto drift depends on the state, as the local volatility is higher below the forward. SX5E paid in USD, on common draws:

2022-07-01: rho +0.46, FX volatility 8.7 %.

| Payoff, 2 years, % of notional | With the deterministic drift | The adjustment itself (bp) | State-dependent minus deterministic (bp) | SE (bp) |
|---|---|---|---|---|
| Discounted forward | 88.02 | -184.2 | -1.0 | 0.06 |
| Put 100 % | 16.02 | +72.1 | +16.1 | 0.07 |
| Put 80 % | 8.00 | +32.2 | +16.3 | 0.07 |
| Digital put 70 % | 22.26 | +94.0 | +25.0 | 1.21 |

2026-11-04: rho +0.29, FX volatility 4.9 %.

| Payoff, 2 years, % of notional | With the deterministic drift | The adjustment itself (bp) | State-dependent minus deterministic (bp) | SE (bp) |
|---|---|---|---|---|
| Discounted forward | 90.77 | -52.2 | -1.0 | 0.02 |
| Put 100 % | 10.51 | +17.0 | +5.5 | 0.03 |
| Put 80 % | 4.69 | +6.5 | +4.7 | 0.02 |
| Digital put 70 % | 14.02 | +20.6 | +9.9 | 0.69 |

Read the two middle columns together. In absolute terms the term left out is small, but on a put away from the money it is not small against the adjustment itself. The closed-form validation holds under Black-Scholes, where this term is zero.

## Limits

- rho and the FX volatility are constants over the life of a product, read on one date, with no term structure.
- A vol bump keeps the adjustment of the base market: the vega misses about 2.9 bp of 2-year forward per vol point on SX5E in USD, on 2026-11-04.
- The pseudo CvC and PvP of `basket_spreads` stay in each name's own currency, as their docstring says.
- The Pricer and the Book still build worst-ofs on flat inputs (`Desk._basket_ctx`): a quote says the performances are not adjusted, and an explicit payment currency is refused until they read the lake's market.
