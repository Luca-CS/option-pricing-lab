# Can a free surface history stand in for an archive?

**Test, run on 5 November 2026, then again after the fix of the regime section below.** SPX surfaces were rebuilt from volatility indices only, on 26 days between 1 July and 29
December 2022, without reading a single option quote. They were then compared with real end-of-day SPX and SPXW chains
for those days (the free historicaldata.net sample): 157,016 quotes, with maturities up to 5 years.

Figures come from `scripts/lake_surface_test.py` and are stored in `results/surface_reconstruction.json`. Only statistics
are published here, never a quote.

## Result

**At maturities of 6 months and more, the reconstruction is within about 0.7 to 0.9 volatility point near the money and 1 to 2
points in the far wings.** One-month options are about 3.5 points off in RMSE. It is not a substitute for the chains in
the sense of fitting inside the bid-ask (4 % of the quotes), which an SVI fit does on 41 % of the same quotes.

RMSE in volatility points, reconstructed minus observed mid, static prior:

| Maturity | < 70 % | 70-80 % | 80-90 % | 90-95 % | 95-100 % | 100-105 % | 105-110 % | 110-120 % | > 120 % | All (bias) |
|---|---|---|---|---|---|---|---|---|---|---|
| Up to 1 month |  | 3.11 | 4.43 | 4.16 | 2.71 | 3.65 | 3.38 | 2.51 | 1.74 | 3.46 (-0.06) |
| 1-3 months | 0.69 | 2.09 | 2.31 | 1.29 | 0.77 | 1.54 | 1.54 | 1.24 | 1.15 | 1.56 (+0.17) |
| 3-6 months | 1.28 | 1.92 | 1.60 | 0.82 | 0.55 | 0.67 | 0.78 | 0.94 | 1.63 | 1.21 (+0.71) |
| 6-12 months | 1.39 | 1.63 | 1.24 | 0.83 | 0.70 | 0.68 | 0.81 | 1.30 | 2.15 | 1.41 (+1.19) |
| 1-2 years | 1.70 | 1.61 | 1.20 | 0.97 | 0.93 | 0.93 | 1.04 | 1.32 | 1.74 | 1.42 (+1.28) |
| 2-3 years | 1.34 | 1.15 | 0.90 | 0.83 | 0.82 | 0.83 | 0.88 | 1.02 | 1.27 | 1.16 (+0.96) |
| 3-5 years | 0.73 | 0.56 | 0.61 | 0.67 | 0.70 | 0.72 | 0.69 | 0.69 | 0.70 | 0.69 (+0.26) |

Moneyness is strike over forward. All quotes: RMSE 2.28, bias +0.43, mean absolute error 1.63.

The wing errors are biased: from 70 % to 95 % of the forward the reconstruction is 1.7 to 2.0 points too high on average.
On a knock-in put at 60-70 % this overstates the value of the put, so a coupon solved on it comes out too high. For
structured products this bias matters more than the headline RMSE.

## Variants

| Shape of the smile | RMSE | Bias | Within bid-ask | Static arbitrage |
|---|---|---|---|---|
| Static prior (calibration of 27/10/2026), slope falling with the level | 2.28 | +0.43 | 4.2 % | none on 26 of 26 days |
| Same, slope from the SKEW index | 2.48 | +0.62 | 3.9 % | none on 26 of 26 days |
| Shape fitted on the chain of 1 July 2022, then frozen | 2.35 | +1.34 | 2.7 % | none on 26 of 26 days |

SKEW does not earn its place: it improves the put wing and the first month, and worsens everything beyond 3 months and
above the money (4.1 points beyond 120 %). The history in the lake therefore uses the **static prior without SKEW**.

Freezing a shape from a chain of five months earlier is not better overall. Its low RMSE hides a bias of over 1 point at
every maturity. This cuts against the plan's idea of always taking the shape from the last observed chain: a fresh chain
helps, an old one does not obviously.

An SVI fit on the same days, for scale: 21 bp RMSE, butterfly-free on every day, calendar-free on 25 of 26 days.

## By level of the VIX, and the crash of 2020

The test above covers one orderly period, so `scripts/lake_regime_test.py` adds real SPY chains from DoltHub (2019-2026, up to about 2
months) and one SPX day in three of 2022: 333 days, 35 of them between 20 February and 15 May 2020.

It found a real error. The shape comes from a calm day, and its slope grew too fast with the level: above a VIX of 60 it
overstated the skew by more than 6 points. The index fixes the variance, so a smile too steep leaves too little at the money, and
the at-the-money level came out 7 points too low. On 16 March 2020, 11.5 points at 2 months. The term structure of that period did
fall steeply from a very high start, as the VIX9D, VIX, VIX3M, VIX6M and VIX1Y of those days say; its level was wrong, not its shape.

The fix is one parameter: the slope falls as (30-day level / 12 %) to the power -0.12. The power is fitted without the crash (0.118)
and rounded, so the lines of the crash below are out of sample.

Whole sample. RMSE, at-the-money gap and skew gap (model minus chain, at one standard deviation below the money), in volatility points:

| VIX | Slices | RMSE, before | RMSE, now | ATM gap, before | ATM gap, now | Skew gap, before | Skew gap, now |
|---|---|---|---|---|---|---|---|
| Below 20 | 409 | 1.19 | 1.20 | +0.41 | +0.58 | +0.63 | +0.45 |
| 20-30 | 1625 | 1.99 | 1.86 | -0.85 | -0.33 | +2.30 | +1.76 |
| 30-40 | 397 | 2.30 | 2.23 | -0.82 | +0.04 | +2.62 | +1.71 |
| 40-60 | 57 | 3.09 | 3.20 | -2.43 | -0.69 | +2.17 | +0.30 |
| Above 60 | 18 | 6.13 | 4.12 | -7.05 | -3.55 | +6.35 | +2.61 |

Crash only, out of sample:

| VIX | Slices | RMSE, before | RMSE, now | ATM gap, before | ATM gap, now | Skew gap, before | Skew gap, now |
|---|---|---|---|---|---|---|---|
| Below 20 | 3 | 1.17 | 1.25 | -0.29 | -0.13 | -0.17 | -0.34 |
| 20-30 | 12 | 1.45 | 1.62 | -0.16 | +0.42 | +0.61 | -0.01 |
| 30-40 | 30 | 1.82 | 2.10 | -1.14 | -0.16 | +0.55 | -0.49 |
| 40-60 | 42 | 3.23 | 3.07 | -3.29 | -1.51 | +2.45 | +0.52 |
| Above 60 | 18 | 6.13 | 4.12 | -7.05 | -3.55 | +6.35 | +2.61 |

So half of the error is gone above a VIX of 60, and the calm days do not move. **Above 60 the at-the-money level is still about 3.5 points
too low.** These are SPY options of at most about 2 months, so nothing here tests a long maturity in a crash.

## Limits of this test

- **Long maturities in a crash are not tested.** The chains of 2020 stop at about 2 months. Beyond that, the surfaces of the replay through
  March 2020 rest on the indices up to one year and on an assumption after it.
- **The prior comes from the future.** The static shape was calibrated on 27 October 2026, and is applied to 2022 as a fixed
  assumption. No level of 2022 depends on it, but it is not what a desk could have known then. The frozen-shape variant is
  the honest comparison, and it is no better.
- **Chain forwards were used to place the quotes.** The test compares volatilities at the chain's own log-moneyness, so errors
  in the forward (carry, discount) are not in these numbers.
- **Beyond one year the term structure is extrapolated.** Its parameters, a long-run forward variance and a speed, come from
  the same prior. The good score at 3 to 5 years is measured on this one period only.
- **SPX only.** Other names are the same machinery scaled by their 30-day index, with an assumed single-name skew. No
  equivalent test exists for them, as the sample has no chain for them beyond SPY, TSLA and KO.

## Decision on the archive (199 to 799 $)

Luca's call. The figure says the purchase is **not needed to run the replay**: near the money at the maturities of these
products, the error is about what a bid-ask costs at long maturities.

It would be needed for two things this test cannot deliver:

1. **Long maturities in stress.** A full chain history through March 2020 would test beyond the 2 months of DoltHub. That requires the
   10-year window (699 $). The 1-year window (199 $) does not reach 2020.
2. **Fitting real chains in the Vol Fitting Lab over time.** The free data already gives 127 days of 2022, one day of 2026, and
   short-dated chains from DoltHub.

The free OptionsDX files (2010-2023, to be confirmed) may answer the first point at no cost, so they come first.
