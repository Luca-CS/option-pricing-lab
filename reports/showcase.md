# Showcase: the desk on real history, in fifteen minutes

A book of twelve trades is ready in its own store (`optlab-data/showcase`), so your own trades stay out of it. Each trade was dealt on a past date,
with its coupon solved on that date's market at the desk's client price, with the lines of that quote kept as its reserve, and real closes are loaded as fixings. The market in use is the close of 4 November 2026.

Open it from this worktree. `--workers 6` is what the times below use.

```
..\..\..\.venv\Scripts\python.exe -m optlab.server --store "C:\Users\Luca\Code\projets\optlab-data\showcase\desk.sqlite" --market SPX-20261104T1600-8b7fa10118 --workers 6 --open
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

## 3. Book: eleven live trades, each name on its own market

Press **Price the book** with Delta and vega on. Quick takes about 26 s, Standard about 57 s.

- **Value about -56.4 m**, ± 22 k on Quick and ± 10 k on Standard. The trades were sold by the desk, so the value is negative. It is the value at the **base**, local vol or replication.
- **Reserves left: 572 k of 893 k at issue,** so a value net of reserves of about -56.96 m. Each position shows its own. They amortise by the calendar and SHOW-01, called, has released its own. **Open SHOW-09** for the lines of a worst-of, frozen as they were on its strike date.
- **Two simulations:** 8 trades on SPX, 3 worst-ofs. A trade on another single name would get a group of its own, on its own market.
- **Delta by name:** SPX about -194 k per 1 %.
- **Worst-ofs read the lake:** each name its own surface, and a measured correlation (NDX-SPX 0.91, SPX-SX5E 0.61). The Market screen no longer holds inputs for them, unless you type some: what is typed wins.
- **Book report** writes a standalone HTML page, with the reserves.

## 4. Lifecycle: four stories on real closes

Open each trade from the Book, section Events.

| Trade | What it shows |
|---|---|
| SHOW-03 | BRC struck 19/02/2025, daily barrier at 85 %: **knocked in on 04/04/2025**, in the tariff sell-off. Coupons keep paying |
| SHOW-02 | Phoenix on the same date, monthly coupon at an 85 % barrier: **one coupon missed**, then paid back by the memory |
| SHOW-01 | Phoenix struck 15/11/2024: **called** after one coupon, status Called, out of the valuation |
| SHOW-11 | Swap with a floating funding leg, spread from the fictitious bank on its strike date |
| SHOW-12 | The one long position, half a unit bought back: its quote is on the buyer's side, so its SLV gap gives no provision |

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

- **Open `replay_phx_desk/PHX-2022`.** The Phoenix of January 2022 sold at the client price: 3.24 % p.a. instead of 5.06 % on the base. The reserve falls with the calendar in the first chart, and the 0.361 left is released on the call of 16/01/2024, a line of its own in the P&L, which still adds up to 4e-15.
- **Open `replay_brc_lv/BRC-2020`.** Struck 15/01/2020: knocked in on 23/03/2020 at 68.0 %, while the close before was 70.07 %, then redeemed at 100 because the index came back. The P&L
  table splits delta, gamma, vega, time and a published residual. Cash booked equals the events.
- **New run:** Barrier reverse convertible, SPX, 2020-01-15, every 20 days, local vol, about 35 s. Each value uses only what was known on its date; a test erases the future of the lake
  and finds the same market. Sparse steps make a large residual: daily is the honest setting.
- Replays of a share work too, on its own index and dividends.

## 8. Pricer: a base and what is added to it

**Cost defaults** first: bumps, SLV mix and limits of the digitals, by group of underlyings and size. Everything in italics is a starting value, none given by a trader: this is the table to correct.

**Phoenix, Standard, Quote.** About 20 s. The policy picks local vol, then adds what it does not see. Section **Hedging cost**, on the market of 4 November 2026:

| Line | Cost, % of notional | Coupon, bp |
|---|---|---|
| Base | 96.99 ± 0.04 | |
| Model: SLV, mix 0.5 | +0.58 ± 0.03 | -120 |
| Bumps, with their interaction | +0.14 | -28 |
| Digitals, with their interaction | +0.29 | -60 |
| Client price | 98.00 ± 0.04 | |

The coupon goes from 5.87 % on the base to 3.80 % ± 0.07. Open **Digitals, date by date**: width, side and cost of each.

1. **Override a parameter.** In the term sheet's Hedging costs section, each field shows its default as a placeholder. Enter -1 in Volatility and quote again: the line is marked overridden, and the coupon falls by about 45 bp.
2. **Change the size.** A notional of 20 m takes the next tier and the defaults double. At 1 billion the Pricer alerts and asks for the values, and Quote stays disabled until they are entered.
3. **Barrier reverse convertible.** SLV is below local vol here, gap -0.18: no provision, and the coupon only moves by the bumps and the barrier moved by the minimum spread.
4. **Underlying AAPL.** Its own surface, in Markets used, well above SPX in implied vol, and larger default bumps.
5. **Basket: Worst of, SPX, NDX, RUT.** The first quote of a basket takes a minute and a half: three leverages, then the correlation skew fitted on CvC and PvP. They stay for later quotes of that basket and date. Two provisions now, the second marked `synthetic`, with its fit shown next to it: this one rests on an assumption, as no broker levels exist.
6. **Book this trade.** Quantity -1 for a sale: the trade's page shows the frozen lines.

Numbers and limits, including what the mix and the correlation of the variances move: [pricing.md](pricing.md).

## What this does not show

- **FX is loaded, and the quanto reaches the Pricer, the Book and the replay.** The lake has ECB spots since 2015 (H.10 as a check), EURUSD forwards by covered parity, and realised FX volatility and correlation. A term sheet has a payment currency. When the lake builds a name, a worst-of on SPX and SX5E has SX5E on its quanto forward and the dollar curve, and the Book values trades paying in different currencies in separate groups. A name on typed or flat inputs is not adjusted: the quote says so, and an explicit payment currency is refused. SHOW-09 was dealt before the quanto reached the desk, so its dealt price is unadjusted while the Book now values it adjusted. [fx_check.md](fx_check.md).
- **The FX inputs have limits.** The volatility is realised (`derived`): no free source quotes an implied one. From six months out, EURUSD forwards sit about 22 bp a year above the CME future, on Treasury and AAA curves with no basis. The adjustment is a deterministic drift, exact under Black-Scholes. Under local vol the state-dependent term is left out: on a 2-year 80 % put, 5 to 16 bp, a half or more of the adjustment itself.
- **Defaults are starting values,** and on worst-ofs they hold more in reserves than the 2 % margin.
- **The correlation skew is an assumption:** no broker level exists, and on three indices its provision also rests on extrapolating below 90 % ([pricing.md](pricing.md)).
- **Reconstructed surfaces are not quotes.** Above a VIX of 60 the at-the-money level is still about 3.5 points low. From 70 to 95 % of the forward they ran about 2 points high in 2022, so coupons
  solved on them are a little high. [surface_reconstruction.md](surface_reconstruction.md).
- **Forwards sit about 0.25 % below put-call parity at one year** on SPX in 2022: the Treasury curve is not the funding of the index.
- **The SX5E dividend season is an assumption**, as is the yield of NDX and SX5E.
- **A shared desk** (`--host`, `--tunnel`) hides quotes and index levels: tested at the API, not on a real tunnel.
