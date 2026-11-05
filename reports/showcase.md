# Showcase: the desk on real history, in fifteen minutes

A book of twelve trades is ready in its own store (`optlab-data/showcase`), so your own trades stay out of it. Each trade was sold on a past date,
with its coupon solved on that date's market, and real closes are loaded as fixings. The market in use is the close of 4 November 2026.

Open it from this worktree (branch `data`). `--workers 6` brings a book valuation from about 100 s to about 40 s.

```
..\..\..\.venv\Scripts\python.exe -m optlab.app --store "C:\Users\Luca\Code\projets\optlab-data\showcase\desk.sqlite" --market SPX-20261104T1600-8b7fa10118 --workers 6 --open
```

To start over: delete the `showcase` folder and run `scripts/seed_showcase.py`, which prints this command again with the new market id.

## 1. Overview: where every number comes from

The status bar names the market, its date and its **origin**: here `reconstructed`, as no option quote was read. Every market part carries one of four
origins: observed, derived, reconstructed, synthetic. The next 60 days list the fixings to come across the book.

## 2. Market: a crash, then today

- **Today.** ATM volatility rises from 13.8 % at 3 weeks to 18.3 % at 5 years. Forward over spot climbs, with small steps where the dividends of last year come
  back.
- **Press Use on the line 23 Mar 2020.** ATM volatility falls from 59 % at 3 weeks to 21 % at 5 years, as VIX9D 78.8, VIX 61.6, VIX3M 56.6, VIX6M 46.6 and VIX1Y 36.4 say. Forward over spot now
  falls, with the rates at zero and a yield of 2.5 % of a spot that has just lost a third.
- **Press Use on 04 Nov 2026 before going on.** Fixings are in the scale of this market.

The line 01 Jul 2022 is the one market with an SVI fit on a real chain, `observed`.

## 3. Book: eleven live trades, one simulation

Press **Price the book** with Delta and vega on. Quick takes about 12 s, Standard about 40 s.

- **Value about -57.2 m**, ± 29 k on Quick and ± 13 k on Standard. The trades were sold by the desk, so the value is negative. Standard errors are measured on one shared simulation.
- **Two groups:** 8 single-name trades under SLV, 3 worst-ofs under local vol.
- **Delta by name:** SPX about -190 k per 1 %.
- **Other underlyings** of the worst-ofs come from the lake at that date: level, dividend, correlations (NDX-SPX 0.91, SPX-SX5E 0.61).
- **Book report** writes a standalone HTML page.

## 4. Lifecycle: four stories on real closes

Open each trade from the Book, section Events.

| Trade | What it shows |
|---|---|
| SHOW-03 | BRC struck 19/02/2025, daily barrier at 85 %: **knocked in on 04/04/2025**, in the tariff sell-off. Coupons keep paying |
| SHOW-02 | Phoenix on the same date, monthly coupon at an 85 % barrier: **one coupon missed**, then paid back by the memory |
| SHOW-01 | Phoenix struck 15/11/2024: **called** after one coupon, status Called, out of the valuation |
| SHOW-11 | Swap with a floating funding leg, spread from the fictitious bank on its strike date |
| SHOW-12 | The one long position, half a unit bought back |

The others are ordinary: reverse convertible, airbag, capital protected, Athena, three worst-ofs.

## 5. Data: what the lake holds

- **Coverage and origin**, table by table.
- **A market at a past date.** SPX, 2020-03-23: each part with its origin. *Price on this market* switches the desk; come back as in step 2.
- **CvC and PvP, 1 year.** Option on the basket against the same option on its members. They are `synthetic`: the correlation goes in as an assumption. Look at
  two things: on one strike CvC and PvP are the same number, and the column *Read back* is the direct formula on a surface with a smile. It overstates by about 2
  points at 100 % and 4 at 110 %, and is right at 90 %.
- **Quality:** what the checks found.

## 6. Vol lab: real quotes against four models

SPX · historicaldata, day 2022-09-15, then **Fit**. About 10 s, more with SLV.

- **SVI 24 bp, SSVI 32 bp, Heston 93 bp** against 785 quotes, each with its share inside the bid-ask.
- Every run is stored with a hash of its quotes. **Replay** on a run recomputes it and answers Same.
- DoltHub adds short-dated chains from 2019 for AAPL, AMZN, GOOGL, GS, IBM and SPY.

## 7. Replay: a product through its life

- **Open `replay_brc_lv/BRC-2020`.** Struck 15/01/2020: knocked in on 23/03/2020 at 68.0 %, while the close before was 70.07 %, then redeemed at 100 because the index came back. The P&L
  table splits delta, gamma, vega, time and a published residual. Cash booked equals the events.
- **New run:** Barrier reverse convertible, SPX, 2020-01-15, every 20 days, local vol, about 35 s. Each value uses only what was known on its date; a test erases the future of the lake
  and finds the same market. Sparse steps make a large residual: daily is the honest setting.
- Replays of a share work too, on its own index and dividends.

## 8. Pricer: from a past market to a new trade

Phoenix, Quick, **Quote**. The policy picks SLV and says why, with local vol as a control (3.5 SE apart on this one). Expected life, probability of ending at each date, expected flows, generated
script, then **Book this trade**.

## What this does not show

- **FX is loaded, and the quanto reaches the replay only.** The lake has ECB spots since 2015 (H.10 as a check), EURUSD forwards by covered parity, and realised FX volatility and correlation. A term sheet has a payment currency. On the lake's market, so in a replay, a worst-of on SPX and SX5E has SX5E on its quanto forward and the dollar curve. The Pricer and the Book still take flat worst-ofs: the quote says the performances are not adjusted, an explicit payment currency is refused, and SHOW-09 stays unadjusted. [fx_check.md](fx_check.md).
- **The FX inputs have limits.** The volatility is realised (`derived`): no free source quotes an implied one. USDJPY and GBPUSD have no forward, for lack of a curve. From six months out, EURUSD forwards sit about 22 bp a year above the CME future, on Treasury and AAA curves with no basis. The adjustment is a deterministic drift, exact under Black-Scholes. Under local vol the state-dependent term is left out: on a 2-year 80 % put, 5 to 16 bp, a half or more of the adjustment itself.
- **Worst-ofs of the Pricer and the Book take flat inputs for the other names**, filled from the lake by the seed script. Only the replay gives each name its own surface.
- **Reconstructed surfaces are not quotes.** Above a VIX of 60 the at-the-money level is still about 3.5 points low. From 70 to 95 % of the forward they ran about 2 points high in 2022, so coupons
  solved on them are a little high. [surface_reconstruction.md](surface_reconstruction.md).
- **Forwards sit about 0.25 % below put-call parity at one year** on SPX in 2022: the Treasury curve is not the funding of the index.
- **The SX5E dividend season is an assumption**, as is the yield of NDX and SX5E.
- **A shared desk** (`--host`, `--tunnel`) hides quotes and index levels: tested at the API, not on a real tunnel.
