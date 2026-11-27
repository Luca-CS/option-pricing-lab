# Two products through their lives on real closes

Run on 5 November 2026 with `optlab lake replay run`, stored in `results/replay_*.json` (performances and amounts in % of
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

Both dates were checked against an independent reading of the closes in `packages/server/tests/test_lake_real.py`. Cash booked in the daily loop equals the
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

## The same life, hedged

The BRC above, sold (the desk's side) on 1,000,000 of notional, replayed with the desk's hedges (`replay(..., hedge=...)`,
`optlab.marketdata.hedged`): delta held flat by the next quarterly ES future (rolled five days before expiry) or by cash
funded at the government rate plus the bank's spread, rebalanced every day or every five; in one run the vega held flat
every five days by a 3-month at-the-money call. Half-spreads from the cost table's starting values; 259 daily
valuations at 20,000 paths. Per 100 of notional:

| | Naked | Futures, daily | Futures, weekly | Cash, daily | Futures daily, vega weekly |
|---|---|---|---|---|---|
| Total P&L | -4.57 | +11.76 | +11.00 | +11.23 | -5.06 |
| Spread of the daily P&L | 1.56 | 0.48 | 0.64 | 0.48 | 0.82 |
| Delta | | 0.00 | -0.80 | 0.00 | 0.00 |
| Gamma | | +11.65 | +11.65 | +11.65 | +11.65 |
| Vega | | +8.21 | +8.21 | +8.21 | +8.21 |
| Time | | -5.84 | -5.84 | -5.84 | -5.84 |
| The vega hedge | | | | | -17.05 |
| Carry | | | | -0.05 | |
| Costs | | -0.04 | -0.02 | -0.07 | -2.09 |
| Slippage | | -2.22 | -2.19 | -2.68 | +0.07 |

What it says:

- **The daily delta hedge removes the spot.** The spread of the daily P&L falls by a factor of 3.3, and what is left is
  the note's own gamma, vega and time: the desk is long the investor's put, so a delta-hedged book earns its gamma and its
  vega in a crash year (+11.65 and +8.21) and pays its time (-5.84). Rebalancing weekly leaves more spread (0.64) and
  -0.80 of delta between rebalances, for half the spread paid.
- **Cash or futures** give the same hedge to within its carry: funding at the government rate plus the spread, less the
  dividends, cost 0.05 over the year, and cash's half-spread more than the futures'.
- **The vega hedge does not pay here.** Selling at-the-money calls against the note's long vega also sells its long
  gamma. In the 2020 rebound the calls lost 17.05, more than the note's vega made, and the weekly rebalancing paid 2.09
  of vol spread. A barrier note's vega is not an at-the-money option's: its hedge is a matter of strikes and tenors (the
  optimizer's vega per tenor and delta bucket), not of one call.
- **The slippage** is the note's own residual of the table above (+2.68 for the investor), the higher orders between
  revaluations; with the vega hedge it is the option's revaluation against its own Greeks, near zero by construction.

## What this does not show

- **The surface in March 2020 is tested up to about 2 months only,** on real SPY chains, and is still about 3.5 points low at the money above a VIX of 60
  ([surface_reconstruction.md](surface_reconstruction.md)). The knock-in date does not depend on it, but the values around it do.
- **The term structure beyond one year is an assumption,** and the Phoenix priced on it for its first two years.
- **The coupons are biased high:** the reconstruction overstates puts between 70 % and 95 % by about 2 volatility points in 2022.
- **Survivorship and period.** These products were picked for their stories. Two trades are a check of the machinery, not a backtest of a strategy.
- **Issuer credit is at zero here.** The fictitious bank's curve exists (`derive/bank.py`) and plugs into `wrapper.issuer_spread`; these runs do not use it.
- **One hedged product.** The hedged replay shows one note's life with simple rules (a daily delta, a weekly vega); a book hedged by the optimizer through time is not replayed.
