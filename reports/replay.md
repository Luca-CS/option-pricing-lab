# Two products through their lives on real closes

Run on 5 November 2026 with `scripts/lake_replay.py`, stored in `results/replay_*.json` (performances and amounts in % of
notional, never a real level). Each valuation prices on the market of that date only: real closes, Treasury curve, the dividends of the
past year repeated on the same dates, and a surface reconstructed from that day's volatility indices. No option quote is read, and one surface layer is kept over
the whole life, so that a change of layer never shows up as P&L. The errors of that surface are in
[surface_reconstruction.md](surface_reconstruction.md).

## Results

| | BRC struck 15/01/2020 | Phoenix struck 14/01/2022 |
|---|---|---|
| Terms | 12 months, daily barrier 70 %, quarterly coupon | 36 months, quarterly call at 100 %, memory coupon barrier 70 %, final barrier 60 % |
| Coupon solved at the strike, local vol | 2.57 % ± 0.07 p.a. | 5.06 % ± 0.21 p.a. |
| What happened | **Knocked in on 23/03/2020**, at 68.0 % of the initial fixing; the close of 20/03 was 70.07 %. Back above 100 % at maturity | **Called on 16/01/2024** after 8 coupons. Lowest close 76.7 %, so no coupon missed |
| Cash received | 102.57 | 110.12 |
| Value at the strike | 98.00 (100 less the 2 % margin) | 98.00 |
| Lowest value | 68.88 on 23/03/2020 | 87.51 on 12/10/2022 |
| Valuations, daily | 259 | 507 |

Both dates were checked against an independent reading of the closes in `tests/test_lake_real.py`. Cash booked in the daily loop equals the
amounts from `lifecycle.events` to within 1e-13, and P&L, value and cash add up exactly.

## P&L explained

Delta and gamma per +1 % spot and vega per +1 vol point are computed the step before, on the same paths. Time is the previous
market moved one day with spot and term structure unchanged. Amounts are per 100 of notional.

| | Delta | Gamma | Vega | Time | Residual | Total |
|---|---|---|---|---|---|---|
| BRC | +15.91 | -11.65 | -8.21 | +5.84 | +2.68 | +4.57 |
| Phoenix | +0.19 | -0.76 | +4.25 | +13.22 | -4.78 | +12.12 |

The residual is small day by day: on average 0.045 for the BRC against a daily P&L of 0.789 and a standard error of 0.060 on
each value, and 0.063 for the Phoenix against 0.361 and 0.124. So its daily size is within Monte Carlo noise. That is 5.7 % and 17.5 % of the
daily P&L.

Its sum is not negligible next to the total: +2.68 out of +4.57 and -4.78 out of +12.12. There is no line for rates and carry, and the Phoenix lived
through the rate rises of 2022, which probably explains part of its residual. This has not been measured. The largest daily residuals fall on
large moves, where a second-order expansion stops being enough: 2.0 on 13/03/2020 and 1.1 on 10/11/2022.

## What it costs, and the choice of frequencies

Measured on this PC with other jobs running, so these are upper bounds.

| Step | Seconds |
|---|---|
| Market of a date, from the lake | 0.34 |
| Local vol valuation with Greeks, 20,000 paths, per valuation including the market | 1.2 (BRC), 2.4 (Phoenix) |
| Leverage recalibration, 1 year, at 50,000 / 100,000 / 200,000 particles | 1.2 / 1.9 / 4.9 |
| SLV valuation, leverage every day, per valuation, no vega | 2.7 |
| SLV valuation, leverage every 5 days, per valuation, no vega | 1.3 |

**Daily revaluation is affordable:** a one-year trade takes 5 to 12 minutes.

**Daily leverage is the default.** On the BRC, a leverage reused for five days moves the value by 0.25 on average and 3.2 at most,
against a standard error of 0.065: beyond 2.8 standard errors on 36 % of the days, mostly in March 2020. Weekly would save half the time at the cost of a
stale model in exactly the weeks that matter.

The other side is noise: daily recalibration has a larger P&L residual than weekly (26 % against 18 % of the daily P&L, with no vega line under SLV), as
each calibration draws its own particles.

## SLV against local vol

Solved at the strike of the BRC, SLV gives 2.89 % ± 0.07 against 2.57 % ± 0.07 for local vol: 32 bp on the coupon, significant. On 02/03/2020 the running BRC
was worth 95.67 ± 0.09 under SLV and 95.86 ± 0.09 under local vol. These runs use local vol, which has since become the desk's base, with SLV as a provision
([pricing.md](pricing.md)). The Replay screen offers both.

## A reserve through a life

`results/replay_phx_desk.json` deals the same Phoenix at the desk's client price, with starting values for the bumps: sold at 3.24 % ± 0.18 p.a., against 5.06 % on the base alone, it holds a reserve of 1.076 per 100 of notional. The reserve falls linearly with the calendar and the 0.361 left is released on the call of 16/01/2024. The explained P&L has a line of +1.076 for it; P&L, value net of reserve and cash add up to 4e-15.

## What this does not show

- **The surface in March 2020 is tested up to about 2 months only,** on real SPY chains, and is still about 3.5 points low at the money above a VIX of 60
  ([surface_reconstruction.md](surface_reconstruction.md)). The knock-in date does not depend on it, but the values around it do.
- **The term structure beyond one year is an assumption,** and the Phoenix priced on it for its first two years.
- **The coupons are biased high:** the reconstruction overstates puts between 70 % and 95 % by about 2 volatility points in 2022.
- **Survivorship and period.** These products were picked for their stories. Two trades are a check of the machinery, not a backtest of a strategy.
- **Issuer credit is at zero here.** The fictitious bank's curve exists (`derive/bank.py`) and plugs into `wrapper.issuer_spread`; these runs do not use it.
- **No hedging.** The book is carried unhedged: the P&L is the product's, not a desk's.
