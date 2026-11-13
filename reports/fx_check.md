# FX in the lake and the quanto adjustment

Written by `optlab lake fx-check` (then `scripts/lake_fx_check.py`), 2026-11-05. Gaps and statistics only: no vendor's levels.

## What is loaded

| Item | Source | Origin |
|---|---|---|
| EURUSD spot | ECB reference rate, 14:15 CET | observed |
| USDJPY, GBPUSD spots | Crosses of two ECB rates of one fix | derived |
| The three spots, again | Fed H.10 noon rates through FRED, kept as a check | observed |
| Forwards of the three pairs, 1 month to 5 years | Covered parity on the desk's government curves | derived |
| Yen curve | Ministry of Finance JGB par yields, bootstrapped, with the Bank of Japan's overnight call rate as the shortest pillar | derived |
| Sterling curve | Bank of England fitted gilt spot rates (GLC), with SONIA as the shortest pillar | derived |
| FX volatility | Realised, 60, 120 and 250 days | derived, method `realized` |
| Name against pair correlation | Realised on overlapping 3-day returns | derived |

**Payment currencies.** A term sheet can pay in USD, EUR, JPY or GBP, and 0 `no_curve` issues are open. A name and its payment currency need a pair between them, and the registry holds EURUSD, USDJPY and GBPUSD. So a dollar name pays in any of the four and a euro name in EUR or USD: SX5E in yen or sterling is refused (`no enabled pair`), not computed through a cross.

**No implied FX volatility.** Cboe answers 403 on EVZ and Yahoo no longer serves it. A realised volatility leaves out the FX volatility premium, and the provenance of each quanto market says what it used through `vol_origin` and `vol_method`.

Latest forward dates: EURUSD 2026-11-04; GBPUSD 2026-11-04; USDJPY 2026-11-04. On 2026-11-04: EURUSD volatility 4.9 % (derived, realized), SX5E against EURUSD +0.29 (derived), SX5E surface `reconstructed` (reconstructed).

## Spots: ECB against H.10

| Pair | Days | Median gap (bp) | 99 % (bp) | Largest (bp) | Correlation, 1 day | Correlation, 5 days |
|---|---|---|---|---|---|---|
| EURUSD | 2918 | 16 | 103 | 277 | 0.626 | 0.921 |
| USDJPY | 2918 | 15 | 106 | 358 | 0.702 | 0.936 |
| GBPUSD | 2918 | 18 | 101 | 238 | 0.703 | 0.940 |

The two sources are about four hours apart, which alone decorrelates one-day returns; five-day returns much less. The quality report lists the days above 2 %.

## Yen and sterling curves

| Curve | Period | Days | Days with an overnight rate | Next pillar, median | 95 % | Months against the OECD | Median gap at 10 years (bp) | Largest (bp) |
|---|---|---|---|---|---|---|---|---|
| Yen | 2015-01-05 to 2026-11-04 | 2891 | 100.0 % | 12 months | 12 months | 141 | +0 | 15 |
| Sterling | 2015-01-02 to 2026-11-04 | 2993 | 100.0 % | 6 months | 9 months | 141 | -1 | 12 |

Below a year the yen curve is a straight line from the call rate to the one-year zero rate, as the Ministry's file starts at one year. The sterling short end only holds the maturities gilts reach that day. The ten-year comparison is a par yield for yen and a zero rate for sterling, against a monthly average of benchmark yields: it checks levels and units, not the bootstrap, which the tests check by repricing par bonds. The Bank of England's spot rates are continuous: integrating the forward sheet of its own workbook gives them to 0.0001 bp, against 8 bp if they were semi-annual.

## Forwards: covered parity against the CME future

Each quarterly contract over the spot, against the same carry from our curves at the contract's exact maturity, three months or more from expiry and in the pair's own quotation (a 6J contract is the inverse of USDJPY). Yahoo's two closes are hours apart and that noise is divided by the maturity, so the longer buckets are the ones to read.

- EURUSD: 9 contracts, 9822 contract-days, 2020-09-01 to 2026-11-04; from six months out, median gap -22 bp a year, correlation 0.67.
- GBPUSD: 8 contracts, 8846 contract-days, 2020-09-01 to 2026-11-04; from six months out, median gap -20 bp a year, correlation 0.69.
- USDJPY: 9 contracts, 9350 contract-days, 2020-09-01 to 2026-11-04; from six months out, median gap -20 bp a year, correlation 0.94.

| Pair | Time to expiry | Days | Futures carry (bp/yr) | CIP on our curves (bp/yr) | Median gap (bp/yr) | 90 % of absolute gap (bp/yr) | Correlation |
|---|---|---|---|---|---|---|---|
| EURUSD | 3 to 6 months | 284 | 180 | 192 | -13 | 180 | 0.33 |
| EURUSD | 6 to 12 months | 744 | 162 | 157 | -4 | 93 | 0.53 |
| EURUSD | 1 to 2 years | 2094 | 156 | 162 | -9 | 46 | 0.64 |
| EURUSD | 2 to 5 years | 6700 | 148 | 177 | -27 | 53 | 0.80 |
| GBPUSD | 3 to 6 months | 282 | 7 | 8 | -6 | 168 | 0.08 |
| GBPUSD | 6 to 12 months | 743 | -9 | -4 | -6 | 94 | 0.28 |
| GBPUSD | 1 to 2 years | 1928 | -10 | -2 | -12 | 54 | 0.51 |
| GBPUSD | 2 to 5 years | 5893 | 8 | 32 | -24 | 63 | 0.80 |
| USDJPY | 3 to 6 months | 269 | -309 | -325 | +6 | 241 | 0.21 |
| USDJPY | 6 to 12 months | 703 | -315 | -289 | -12 | 139 | 0.51 |
| USDJPY | 1 to 2 years | 1988 | -313 | -298 | -12 | 67 | 0.90 |
| USDJPY | 2 to 5 years | 6390 | -383 | -361 | -23 | 50 | 0.98 |

| Pair | Year, 6 months and more | Days | Futures carry (bp/yr) | CIP on our curves (bp/yr) | Median gap (bp/yr) | 90 % of absolute gap (bp/yr) | Correlation |
|---|---|---|---|---|---|---|---|
| EURUSD | 2020 | 101 | 106 | 112 | -4 | 16 | 0.62 |
| EURUSD | 2021 | 718 | 130 | 139 | -10 | 25 | 0.77 |
| EURUSD | 2022 | 1410 | 166 | 214 | -44 | 75 | 0.63 |
| EURUSD | 2023 | 2167 | 127 | 160 | -32 | 51 | 0.83 |
| EURUSD | 2024 | 2214 | 158 | 178 | -21 | 43 | 0.67 |
| EURUSD | 2025 | 1810 | 175 | 191 | -10 | 58 | 0.55 |
| EURUSD | 2026 | 1118 | 135 | 142 | -6 | 51 | 0.20 |
| GBPUSD | 2020 | 103 | 32 | 37 | -5 | 22 | 0.18 |
| GBPUSD | 2021 | 708 | 28 | 41 | -13 | 32 | 0.36 |
| GBPUSD | 2022 | 1370 | 28 | 82 | -55 | 90 | 0.82 |
| GBPUSD | 2023 | 1952 | -12 | 23 | -28 | 54 | 0.88 |
| GBPUSD | 2024 | 1968 | 6 | 19 | -13 | 45 | 0.66 |
| GBPUSD | 2025 | 1558 | -11 | 2 | -11 | 63 | 0.39 |
| GBPUSD | 2026 | 905 | -17 | -13 | -1 | 63 | 0.36 |
| USDJPY | 2020 | 99 | -90 | -47 | -45 | 54 | 0.12 |
| USDJPY | 2021 | 682 | -122 | -86 | -36 | 48 | 0.89 |
| USDJPY | 2022 | 1334 | -356 | -318 | -31 | 60 | 0.96 |
| USDJPY | 2023 | 2098 | -434 | -416 | -20 | 50 | 0.87 |
| USDJPY | 2024 | 2115 | -409 | -394 | -10 | 57 | 0.77 |
| USDJPY | 2025 | 1703 | -316 | -309 | -14 | 73 | 0.66 |
| USDJPY | 2026 | 1050 | -270 | -263 | -9 | 66 | 0.34 |

A negative gap means the market's forward sits below ours, in the pair's quotation. Our curves are government curves, not OIS, and the forwards carry no cross-currency basis. The three pairs share these limits, and from a year out the yen and sterling gaps have the size of the euro's. Below a year this check says little for any pair: at 3 to 6 months the noise of the closes swamps the carry (correlation 0.08 to 0.33), so the straight line of the yen short end is not validated by it.

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
| 2020-03-23 | SPX paid in JPY | +0.13 | 10.0 % | 30.4 % | -35 | -57 | -126 |
| 2020-03-23 | SPX paid in GBP | -0.35 | 11.2 % | 30.4 % | +108 | +179 | +395 |
| 2022-07-01 | SX5E paid in USD | +0.46 | 8.7 % | 26.8 % | -108 | -207 | -453 |
| 2022-07-01 | SPX paid in EUR | -0.36 | 8.7 % | 23.6 % | +74 | +139 | +323 |
| 2022-07-01 | SPX paid in JPY | +0.15 | 9.2 % | 23.6 % | -33 | -62 | -144 |
| 2022-07-01 | SPX paid in GBP | -0.43 | 8.7 % | 23.6 % | +88 | +166 | +386 |
| 2026-11-04 | SX5E paid in USD | +0.29 | 4.9 % | 20.0 % | -29 | -57 | -138 |
| 2026-11-04 | SPX paid in EUR | -0.23 | 4.9 % | 17.9 % | +20 | +41 | +104 |
| 2026-11-04 | SPX paid in JPY | -0.21 | 7.2 % | 17.9 % | +27 | +55 | +139 |
| 2026-11-04 | SPX paid in GBP | -0.19 | 5.1 % | 17.9 % | +17 | +35 | +88 |

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
- Gilts and JGBs discount a yen or sterling payment at the government's rate, not at a funding rate. The Bank of England does publish an OIS curve, unused here as the dollar side has none.
- A vol bump keeps the adjustment of the base market: the vega misses about 2.9 bp of 2-year forward per vol point on SX5E in USD, on 2026-11-04.
- The pseudo CvC and PvP of `basket_spreads` stay in each name's own currency, as their docstring says.
- The Pricer and the Book still build worst-ofs on flat inputs (`Desk._basket_ctx`): a quote says the performances are not adjusted, and an explicit payment currency is refused until they read the lake's market.
