# The desk price: a base and what is added to it

Run on 5 November 2026 with `scripts/pricing_examples.py`, on the market of 4 November 2026, 100,000 paths, stored in `results/pricing_examples.json`. Amounts are in % of notional, seen from a desk that sells: a positive cost is against it. Surfaces are reconstructed from volatility indices, so no option quote was read ([surface_reconstruction.md](surface_reconstruction.md)).

**The defaults are starting values, not a trader's.** They are in `optlab/structured/defaults.py`, labelled on every screen, and every cost below scales with them.

```
Base            local vol, or replication for a European product
+ Provisions    max(0, gap against the desk) per model of the family
+ Bumps         one cost per market parameter, signed by the trader
+ Digitals      the cost of reading each digital as a call spread
= Client price  the coupon is solved on it and the 2 % margin
```

## Results

| | Phoenix SPX | Athena SPX | BRC SPX | RC SPX | Phoenix AAPL | Worst of SPX, NDX, RUT | Worst of SPX, SX5E |
|---|---|---|---|---|---|---|---|
| Base | 96.99 ± 0.04 | 96.90 ± 0.04 | 97.73 ± 0.04 | 97.58 | 96.60 ± 0.05 | 95.08 ± 0.07 | 95.47 ± 0.06 |
| SLV provision | +0.58 ± 0.03 | +0.49 ± 0.03 | 0 (gap -0.18) | none | +0.65 ± 0.03 | +0.84 ± 0.04 | +0.94 ± 0.04 |
| Correlation skew provision | | | | | | +1.08 ± 0.06 | +0.62 ± 0.04 |
| Bumps, with interaction | +0.14 | +0.13 | +0.21 | +0.18 | +0.34 | +0.38 | +0.37 |
| Digitals, with interaction | +0.29 | +0.48 | +0.05 | +0.23 | +0.41 | +0.62 | +0.60 |
| Client price | 98.00 ± 0.04 | 98.00 ± 0.04 | 98.00 ± 0.04 | 98.00 | 98.00 ± 0.05 | 98.00 ± 0.08 | 98.00 ± 0.06 |
| Coupon on the base alone, % p.a. | 5.87 | 7.91 | 6.52 | 6.63 | 9.99 | 13.79 | 10.90 |
| Coupon with the costs, % p.a. | 3.80 ± 0.07 | 4.86 ± 0.10 | 6.25 ± 0.04 | 6.20 | 7.27 ± 0.10 | 8.81 ± 0.13 | 6.80 ± 0.09 |
| Seconds, 6 workers | 24 | 12 | 13 | 12 | 23 | 132 | 86 |

The terms are 2 years for the autocalls and 1 year for the others: BRC with a daily barrier at 70 %, reverse convertible with a European barrier at 80 %, autocalls with a coupon barrier at 70 % and a final barrier at 60 %. The time of a basket includes the leverages and the fit of its correlation, kept for later quotes of that basket and date; those of SPX include its leverage only on the first line.

Provisions are the largest lines, then the final barrier's digital. On the worst-ofs, the coupon falls by 4 to 5 points, and the desk holds about 2.5 to 3 % in reserves against a 2 % margin. These starting values are what to correct first.

On the SPX phoenix the costs take 208 bp off the coupon. The old policy, SLV for all path-dependent products, put it at 4.64 %. The BRC's gap goes the other way: SLV is 0.18 of price below local vol, so there is no provision.

## Bumps

All on the base's random numbers. On the phoenix, the SE of a line is about a tenth of the SE of the price. A bump of the forward (dividends, repo, spot) costs no simulation.

| Phoenix SPX | Used | Cost | Coupon, bp |
|---|---|---|---|
| Volatility | -0.25 pt | +0.071 ± 0.004 | -14.7 |
| Skew, 90-110 % at 1 year | -0.25 pt | +0.048 ± 0.006 | -9.9 |
| Dividends | -5 % | +0.010 ± 0.002 | -2.1 |
| Repo | -5 bp | +0.009 ± 0.002 | -1.8 |
| Spot, delta bid-ask | +2 bp | +0.006 ± 0.002 | -1.2 |
| Interaction | | -0.006 ± 0.008 | +1.3 |

Each bump was checked against Black-Scholes on a 90 % put, by Monte Carlo and by replication, within 4 SE (`tests/test_structured_costs.py`). The skew rotation has exact derivatives for Dupire, and the check for butterfly and calendar arbitrage warns on the quote.

## Provisions of model

**Common random numbers needed a change.** Local vol draws one normal per step and SLV two, so on one seed they shared nothing: correlation of the pairs 0.035, SE of the gap 0.052. Now SLV takes its spot normals from the same stream as local vol and the rest of its variance's driver from a stream of its own (`SLVModel.spot_driven`), with the same law. Correlation 0.7, SE 0.030. Local vol's numbers do not change.

**A floor has a bias.** At a gap of zero, `max(0, gap)` averages about 0.4 of its SE, here about 1 bp at 100,000 paths. No threshold removes it, as agreed.

**The mix is not calibrated and it moves the provision a lot**, and on a basket so does the correlation of the variances, which is an assumption too. Gap to SLV of a phoenix with a 12 % coupon:

| | SPX, NDX, RUT: mix 0.25 / 0.5 / 0.75 | SPX, SX5E: mix 0.25 / 0.5 / 0.75 |
|---|---|---|
| Variances correlated like the spots | +0.51 / +0.94 / +1.16 | +0.60 / +1.13 / +1.44 |
| Variances with independent residuals | +0.33 / +0.39 / +0.26 | +0.56 / +1.02 / +1.32 |

SEs are 0.03 to 0.06. Correlating the variances like the spots is the default: two identical names at a correlation of 1 then have the same paths and give the single-name price, which the independent choice cannot do, and index volatilities do move together. On three close indices that choice alone moves the provision by 55 bp of notional. Nothing here calibrates it.

**SLV on a basket.** One leverage per name, from its own marginal, kept per name and date: 21 s for three names over 2 years. Vanillas of each name against its surface, 400,000 paths, on one seed: the largest gap is 4.8 SE, 16 bp of volatility at most. That gap is mostly the forward's own noise on that seed (+0.115 %, about 3.5 SE, on all three names, as they are correlated); over six other seeds the forward stays within 2.5 SE and has no bias. With the forward as a control variate, the vanillas are within 4 SE plus 10 bp of vol (`tests/test_lake_real.py`). So a leverage bias of the order of 10 bp of vol is possible and not excluded.

## Digitals

Width = max(min spread, jump / delta max), on the side against the desk, measured digital by digital on common paths. On the SPX products the side came out below the level on every date, which is the side that pays the holder more often.

| Phoenix SPX | Dates | Width | Cost |
|---|---|---|---|
| Coupon barrier | 8 | 0.95 pt | +0.004 |
| Autocall trigger | 7 | 0.95 pt | +0.099 |
| Final barrier | 1 | 8.00 pt | +0.195 |

- **The bound is measured.** The payoff's delta is at the limit to within 1 %, and over ten times it for the hard digital (`tests/test_structured_digitals.py`). An upside digital matches the closed-form call spread. A daily barrier moved by the min spread gives, bit for bit, the sheet with the barrier moved.
- **The jump of an autocall takes the continuation value at par.** The real gap depends on the path. This keeps the width known before any simulation, and it is the weakest definition of the four.
- **Memory is left out** of a coupon's jump.
- **The width depends on the solved coupon,** so the digitals solve twice, or three times on an Athena, within 1 % of width.

## Correlation skew

**The form.** Z_i = sqrt(w) Z_0 + sqrt(1 - w) (L z)_i, with w depending on the level of the basket, in its standard deviations. This is the family of Langnau and Reghai; Guyon shows that many local correlations fit a basket smile, so the shape is a choice. On each path the matrix stays valid, and each name keeps its law (tested on closed forms within 4 SE). On one strike, CvC and PvP are the same instrument path by path with the model.

**It rests on an assumption.** Only pseudo-quotes exist, where the skew is an input (`derive/correl.py`, origin `synthetic`), and there are no broker levels. So this provision measures what that assumption implies, the screen says so, and it would change with real levels.

**The fit at 1 year**, in % of notional, SE about 0.007:

| | SPX, NDX, RUT: 90 / 100 / 110 % | SPX, SX5E: 90 / 100 / 110 % |
|---|---|---|
| Pseudo-quote | -0.482 / -0.595 / -0.550 | -0.687 / -0.758 / -0.604 |
| Constant correlation | -0.651 / -0.606 / -0.492 | -0.801 / -0.775 / -0.575 |
| Local correlation | -0.495 / -0.585 / -0.570 | -0.703 / -0.771 / -0.624 |
| Stochastic mix | -0.623 / -0.589 / -0.494 | -0.710 / -0.750 / -0.625 |

The fit errs by 1 to 3 SE at 1 year. Out of the fit, at 3 and 6 months, it errs by up to 0.036 at 110 %.

**Recommendation: local correlation.** The stochastic alternative, a Jacobi weight correlated with the market factor, costs the same to calibrate (40 to 70 s) but carries one more state. It fitted SPX, SX5E about as well, and on three names the fit never left its starting point. That is a limit of this fit, not a proof about the model. Local correlation stays the provision; `StochasticMix` is kept for comparison.

**Where this is fragile.** The calibration reads 90 to 110 %, and a worst-of with a barrier at 60 % lives far below. On the three indices the weight is 0.055 at the money and rises by 0.83 per standard deviation, so it reaches 1 about 1.1 standard deviations down, near 77 % at one year, and correlation is 1 below that. So part of the +1.08 is the form's extrapolation, how much was not measured. A cap on the weight would lower it, and nothing here says where to put it.

## What the book sees

Lines are frozen at issue in the trade, amortised by calendar days over the contractual life, and released at a call. The Book values at the base and shows what is left.

The phoenix of `reports/replay.md`, sold on 14/01/2022 at 3.24 % ± 0.18 p.a. instead of 5.06 %, holds 1.076 at issue. It is called on 16/01/2024: 0.713 was left on 19/01/2023, and the remaining 0.361 is released on the day of the call. In the explained P&L, the reserve is a line of +1.076. P&L equals value net of reserve plus cash to 4e-15, and cash equals the events to 1e-14 (`results/replay_phx_desk.json`).

## What this does not show

- **Default bumps are signed for a desk that sells,** and apply as given. On a buyer's quote they come out in its favour (SHOW-12: vol -0.117, skew -0.118), so a buyer must override them.
- **Trades booked before this change have no reserve:** the screens say "none booked".
- **A position's size picks a tier in the currency of its group,** with no FX. A worst-of of USD and EUR names compares one notional against both grids.
- **The reserve is not repriced** after issue, as agreed, so the Book's reserve can drift from what the same line would cost today.
- **The mix band is on single names only** on the screen. On a basket, the table above is from the script.
- **No hedging simulation** backs the digital limits, or says what a bump should be.
