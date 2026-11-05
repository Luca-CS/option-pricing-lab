# Two products through their lives on real closes

Run on 5 November 2026 with `scripts/lake_replay.py`, stored in `results/replay_*.json` (performances and amounts in % of
notional, never a real level). Each valuation prices on the market of that date only: real closes, Treasury curve, past dividend
yield, and a surface reconstructed from that day's volatility indices. No option quote is read, and one surface layer is kept over
the whole life, so that a change of layer never shows up as P&L. The errors of that surface are in
[surface_reconstruction.md](surface_reconstruction.md).

## Results

| | BRC struck 15/01/2020 | Phoenix struck 14/01/2022 |
|---|---|---|
| Terms | 12 months, daily barrier 70 %, quarterly coupon | 36 months, quarterly call at 100 %, memory coupon barrier 70 %, final barrier 60 % |
| Coupon solved at the strike, local vol | 2.60 % ± 0.07 p.a. | 5.13 % ± 0.21 p.a. |
| What happened | **Knocked in on 23/03/2020**, at 68.0 % of the initial fixing; the close of 20/03 was 70.07 %. Back above 100 % at maturity | **Called on 16/01/2024** after 8 coupons. Lowest close 76.7 %, so no coupon missed |
| Cash received | 102.60 | 110.26 |
| Value at the strike | 98.00 (100 less the 2 % margin) | 98.00 |
| Lowest value | 69.26 on 23/03/2020 | 88.42 on 14/10/2022 |
| Valuations, daily | 259 | 507 |

Both dates were checked against an independent reading of the closes in `tests/test_lake_real.py`. Cash booked in the daily loop equals the
amounts from `lifecycle.events` to within 1e-13, and P&L, value and cash add up exactly.

## P&L explained

Delta and gamma per +1 % spot and vega per +1 vol point are computed the step before, on the same paths. Time is the previous
market moved one day with spot and term structure unchanged. Amounts are per 100 of notional.

| | Delta | Gamma | Vega | Time | Residual | Total |
|---|---|---|---|---|---|---|
| BRC | +17.03 | -8.55 | -7.56 | +6.29 | -2.61 | +4.60 |
| Phoenix | -0.17 | -0.61 | +3.89 | +13.61 | -4.46 | +12.26 |

The residual is small day by day: on average 0.044 for the BRC against a daily P&L of 0.772 and a standard error of 0.062 on
each value, and 0.061 for the Phoenix against 0.33 and 0.125. So its daily size is within Monte Carlo noise.

Its sum is not negligible, at 5.7 % and 18.5 % of the absolute P&L. There is no line for rates and carry, and the Phoenix lived through the rate
rises of 2022, which probably explains part of its residual. This has not been measured. The largest daily residuals fall on large moves
(0.7 on 03/02/2022 and 13/09/2022).

## What it costs, and the choice of frequencies

Measured on this PC with other jobs running, so these are upper bounds.

| Step | Seconds |
|---|---|
| Market of a date, from the lake | 0.34 |
| Local vol valuation with Greeks, 20,000 paths, per valuation including the market | 1.2 (BRC), 2.3 (Phoenix) |
| Leverage recalibration, 1 year, at 50,000 / 100,000 / 200,000 particles | 1.2 / 1.9 / 4.9 |
| SLV valuation, leverage every day, per valuation, no vega | 3.0 |
| SLV valuation, leverage every 5 days, per valuation, no vega | 1.6 |

**Daily revaluation is affordable:** a one-year trade takes 5 to 13 minutes.

**Daily leverage is the default.** On the BRC, a leverage reused for five days moves the value by 0.23 on average and 2.9 at most,
against a standard error of 0.067: beyond 2.8 standard errors on 38 % of the days, mostly in March 2020. Weekly would save half the time at the cost of a
stale model in exactly the weeks that matter.

The other side is noise: daily recalibration has a larger P&L residual than weekly (24 % against 14 % of absolute P&L, with no vega line under SLV), as
each calibration draws its own particles.

## SLV against local vol

Solved at the strike of the BRC, SLV gives 2.93 % ± 0.07 against 2.60 % ± 0.07 for local vol: 33 bp on the coupon, significant. On 02/03/2020 the running BRC
was worth 95.49 ± 0.04 under SLV and 95.86 ± 0.04 under local vol. The default runs use local vol for speed, and the policy's own choice for these products is SLV:
the Replay screen offers both.

## What this does not show

- **The surface in March 2020 was not tested.** The reconstruction was measured on 2022 chains only. The knock-in date does not depend on it, but the values
  around it do.
- **The term structure beyond one year is an assumption,** and the Phoenix priced on it for its first two years.
- **The coupons are biased high:** the reconstruction overstates puts between 70 % and 95 % by about 2 volatility points.
- **Survivorship and period.** These products were picked for their stories. Two trades are a check of the machinery, not a backtest of a strategy.
- **Issuer credit is at zero here.** The fictitious bank's curve exists (`derive/bank.py`) and plugs into `wrapper.issuer_spread`; these runs do not use it.
- **No hedging.** The book is carried unhedged: the P&L is the product's, not a desk's.
