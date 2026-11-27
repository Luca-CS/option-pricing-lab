# ADR-0025: Hedges are trades

- Status: Accepted, 26 November 2026

## Context

The desk sells structured notes and hedges them. Before this decision the book held only the notes: the Greeks, the
scenarios, the VaR and the P&L were the notes' alone, and a hedge lived outside the tool. A desk needs the hedged book's
numbers: what is left after the futures, the swaps and the options it holds, and what those cost.

## Decision

- **One trade table.** A hedge is a `Trade` whose product is `{"kind": "instrument", "instrument": {...}, "fill", "fees"}`,
  versioned, audited and frozen by jobs like a note. It sits in the same book with the tag `hedge`, so netting is natural.
- **Linear instruments in closed form.** Futures, cash, interest rate swaps, deposits, funding swaps, dividend swaps and
  dividend futures (`optlab.structured.hedges`) are valued exactly on a context's market and never simulated:
  - in the risk engine, each shock's moved market values them (SE 0), and they enter every measure beside the simulated
    positions of their group;
  - `Book.price` and `Book.greeks`, the EOD, the VaR's full revaluation and the P&L explain read the same values.
- **Where a hedge sits.** An instrument on a name joins the group of that name and payment currency; a rates instrument
  joins the group of its currency's reference index (SPX for USD, SX5E for EUR). A group may hold instruments only.
- **The value is that of the flows to come.** The price paid or received at the fill is cash, and day one's P&L is the
  mark less the fill. A future is the exception: its value is the variation margin to date and to come,
  multiplier × (F(T) − price), since the store holds no margin account.
- **Proxies are labelled.** Swaps discount on the government curve plus a swap spread from the cost table (origin
  `government proxy`); deposits and funding swaps on the issuer's funding curve, the fictitious bank's (origin
  `synthetic`); dividends come from the market's forward (origin `model`).
- **What-ifs.** A trade may carry the status `simulated`: a hedge proposed by the optimizer or typed by the trader.
  It is never in the official EOD nor in the P&L history; Greeks, scenarios and VaR take it when the run asks
  (`include_simulated`). `promote` books it (`live`), `discard` removes it, both audited.
- **New risk factors.** The issuer's funding spread per tenor (`Shock.funding`, Greeks `funding|<entity>|` and
  `funding_tenor|<entity>|<tenor>`), which moves the notes' present values and the instruments on the bank's curve; the
  dividends of each calendar year (`Shock.dividend_years`, Greek `dividend_year|<name>|<year>`). Neither needs a
  simulation of its own.
- **Costs.** The cost table holds the instruments' half-spreads beside the notes' overs (futures in ticks, cash in bp of
  the price, swaps, deposits and funding swaps in bp of the rate, dividend swaps and futures in % of the year's points)
  and the swap spread per currency, versioned with it.

## Consequences

- Every risk number of the desk includes its hedges, with no second pipeline to keep in step.
- A hedged book's error is the notes' error: the instruments add none.
- The default numbers of the library do not move: a book without instruments takes exactly the code paths it took.
- Vanillas, listed and OTC, are trades of the same kind; being non-linear, they are simulated with the notes of their
  group (a mixed group) or replicated (a vanilla-only group).

## Alternatives considered

- **A hedge store beside the book**, with its own valuation: two pipelines to keep in step for every risk number, and a
  netting done by hand. Refused.
- **Every instrument as a payoff script**, simulated with the notes: one code path, but linear instruments would carry
  Monte Carlo noise they do not have, and a group of futures alone would need a simulation. Kept for vanillas only.
- **A separate book `hedge`**: the netting would need a book-of-books. The same book with a tag gives the hedged numbers
  directly and still filters the hedges out when asked.
