# Can a free surface history stand in for an archive?

**Test, run on 5 November 2026.** SPX surfaces were rebuilt from volatility indices only, on 26 days between 1 July and 29
December 2022, without reading a single option quote. They were then compared with real end-of-day SPX and SPXW chains
for those days (the free historicaldata.net sample): 157,016 quotes, with maturities up to 5 years.

Figures come from `scripts/lake_surface_test.py` and are stored in `results/surface_reconstruction.json`. Only statistics
are published here, never a quote.

## Result

**At maturities of 6 months and more, the reconstruction is within about 0.5 volatility point near the money and 1 to 2
points in the far wings.** One-month options are about 3.8 points off in RMSE. It is not a substitute for the chains in
the sense of fitting inside the bid-ask (4 % of the quotes), which an SVI fit does on 41 % of the same quotes.

RMSE in volatility points, reconstructed minus observed mid, static prior:

| Maturity | < 70 % | 70-80 % | 80-90 % | 90-95 % | 95-100 % | 100-105 % | 105-110 % | 110-120 % | > 120 % | All (bias) |
|---|---|---|---|---|---|---|---|---|---|---|
| Up to 1 month | | 4.29 | 5.04 | 4.45 | 2.79 | 4.12 | 3.66 | 2.52 | 1.57 | 3.76 (-0.22) |
| 1-3 months | 1.50 | 2.65 | 2.63 | 1.32 | 1.00 | 2.09 | 2.05 | 1.43 | 1.13 | 1.88 (-0.01) |
| 3-6 months | 1.81 | 2.28 | 1.67 | 0.62 | 0.60 | 1.07 | 1.21 | 0.91 | 1.45 | 1.32 (+0.41) |
| 6-12 months | 1.76 | 1.77 | 1.11 | 0.49 | 0.43 | 0.57 | 0.64 | 0.82 | 1.78 | 1.30 (+0.88) |
| 1-2 years | 2.00 | 1.60 | 0.95 | 0.56 | 0.49 | 0.53 | 0.60 | 0.71 | 1.33 | 1.27 (+0.96) |
| 2-3 years | 1.67 | 1.08 | 0.66 | 0.55 | 0.59 | 0.61 | 0.66 | 0.65 | 0.91 | 1.00 (+0.64) |
| 3-5 years | 1.11 | 0.53 | 0.57 | 0.70 | 0.77 | 0.88 | 0.86 | 0.84 | 0.61 | 0.79 (+0.06) |

Moneyness is strike over forward. All quotes: RMSE 2.48, bias +0.20, mean absolute error 1.78.

The wing errors are biased: from 70 % to 95 % of the forward the reconstruction is 1.9 to 2.1 points too high on average.
On a knock-in put at 60-70 % this overstates the value of the put, so a coupon solved on it comes out too high. For
structured products this bias matters more than the headline RMSE.

## Variants

| Shape of the smile | RMSE | Bias | Within bid-ask | Static arbitrage |
|---|---|---|---|---|
| Static prior (calibration of 27/10/2026) | 2.48 | +0.20 | 4.4 % | none on 26 of 26 days |
| Static prior, slope from the SKEW index | 2.52 | +0.46 | 4.2 % | none on 26 of 26 days |
| Shape fitted on the chain of 1 July 2022, then frozen | 2.35 | +1.34 | 2.7 % | none on 26 of 26 days |

SKEW does not earn its place: it improves the put wing and the first month, and worsens everything beyond 3 months and
above the money (4.1 points beyond 120 %). The history in the lake therefore uses the **static prior without SKEW**.

Freezing a shape from a chain of five months earlier is not better overall. Its low RMSE hides a bias of over 1 point at
every maturity. This cuts against the plan's idea of always taking the shape from the last observed chain: a fresh chain
helps, an old one does not obviously.

An SVI fit on the same days, for scale: 21 bp RMSE, butterfly-free on every day, calendar-free on 25 of 26 days.

## Limits of this test

- **One regime.** The second half of 2022 was stressed but orderly. March 2020 has no chain in this sample, so the replay
  through the knock-ins of 2020 rests on a reconstruction that was not tested in that kind of market.
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

1. **Pricing levels in stress.** A chain history through March 2020 tells whether the reconstruction holds when it matters
   most. That requires the 10-year window (699 $). The 1-year window (199 $) does not reach 2020.
2. **Fitting real chains in the Vol Fitting Lab over time.** The free data already gives 127 days of 2022, one day of 2026, and
   short-dated chains from DoltHub.

The free OptionsDX files (2010-2023, to be confirmed) may answer the first point at no cost, so they come first.
