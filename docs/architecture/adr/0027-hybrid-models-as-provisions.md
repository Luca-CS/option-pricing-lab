# ADR-0027: Hybrid models as provisions against local vol

- Status: Accepted, 27 November 2026

## Context

Local vol with deterministic curves prices most structured notes well. Two risks are outside it:

- **The rates' moves.** An issuer callable's call depends on the rates as much as on the equity, and the equity's
  discounting moves with them.
- **The correlation's moves.** A worst-of's correlation rises when its names fall. Until now its provision was
  fitted on pseudo-quotes, whose correlation is an assumption by construction.

The desk already keeps the gap to SLV as a model reserve when it goes against the desk, frozen at issue and amortised.
These two risks needed models of their own, measured the same way, without moving the base or any default number.

## Decision

- **Stochastic rates: a hybrid beside the base.**
  - A one-factor Hull-White short rate (G1++) is fitted exactly to the discount curve, in the payment currency.
  - The equity is the desk's local vol, recalibrated under the rates by a particle method so that every vanilla is still repriced (`core.rates_lv`). The correction is Dupire less 2 E^T[(r - f) 1{k_T > k}] / p^T(k).
  - The simulation is exact for the rate factor and its integral on any grid. The equity takes local vol's log-Euler step in X = ln(S D / (F P)), so the prepaid forward holds path by path.
  - The spot normals are local vol's own and the rates draw from a stream of their own, so the gap is a difference on common random numbers.
  - Payments are deflated path by path (D at the observation times the bond to the payment date). The wrappers discount the deflated flows on their deterministic curves, and the issuer's policy reads path-wise values with the rate factor in its state.
- **The provision is the gap, each model on its own policy.** It is the investor value under the hybrid less that under local vol. Each model takes its own policy, fitted on its own pilot, so the reserve is the issuer's real option under each model. It is floored at zero on the desk's side.
- **Its parameters are labelled.**
  - Hull-White's volatility comes from two years of the lake's zero-rate changes (`derived`), fitted on the shape sigma B_a(tau)/tau over the tenors from one year.
  - The mean reversion is 3 % (`assumption`: history gives no positive estimate).
  - The equity-rate correlation comes from the same window (`derived`, with its band).
  - The line's origin is the weakest of these, `assumption`.
- **Rate-driven callables.** A fixed-coupon note's call depends on the rates alone: under deterministic curves its
  issuer option is the immediate call or nothing. Its gap to the hybrid is always in the desk's favour when it sells,
  and the hybrid prices it (the base for that class). Equity callables keep local vol as their base.
- **Local correlation: a lambda shift anchored at the measured matrix** (Langnau's family,
  `structured.localcorr.LambdaShift`).
  - The model is rho(X) = C + lambda+ (U - C) + lambda- (I - C), with lambda a bounded function of the basket's level in standard
    deviations, zero at the money (the level is the base's).
  - Every value is a convex combination of correlation matrices, so no repair is needed, and each name keeps its local vol law exactly.
  - The step draws the base's own correlated normals, plus a common normal and, with a lower side, one per name from a stream of their
    own. At lambda 0 the paths are the base's bit for bit.
  - The shape is measured on the lake's closes (`derived`): the conditional realised correlation by the basket's 63-day state,
    with block-bootstrap bands. Without enough history, the fit on the pseudo-quotes stays (`synthetic`).
  - The "Overcorrel skew" over moves its downside cap. The risk engine gets a parallel lambda shift of every basket's matrix
    (`Shock.corr_lambda`) and its lambda-vega (`corr_lambda|*|`).
- **What is not in it.** The issuer's funding spread stays deterministic, its term structure the bank curve's (a stochastic spread is a diagnostic only). A basket, a quanto name or a funded swap under the hybrid is refused with its reason. The compiled engine does not simulate the hybrid yet: `auto` falls back to NumPy and says so.

## Consequences

- No default number moves: a market without a rates model, and every product but an issuer callable, take exactly the
  code they took; a basket without a measured shape keeps the pseudo-quotes' fit. Deterministic paths are untouched, and the deflated flows exist only on a hybrid's scenarios.
- The reserve on equity callables is small (about 0.1 % of notional at an equity-rate correlation of +0.4 and 80 bp,
  none at 0 or below). On rate-driven callables the hybrid is worth the issuer's option on the rates, about 0.8 % on
  a 5-year note above the curve.
- References: Hull-White closed forms (bonds, Jamshidian, Black-Scholes under Hull-White), the smile kept by the recalibrated local vol, and a Hull-White PDE for the callable fixed-coupon note.

## Alternatives considered

- **A two-factor rates model, or a stochastic spread in the price.** The lake holds no swaption or cap volatility to
  calibrate them. The spread's realistic correlation with the equity lowers the gap, so it would understate the reserve. Kept as diagnostics.
- **The hybrid as the base for every callable.** Its parameters are history and assumption, not quotes, and its
  numbers would move with them. It is a provision, except where local vol values the product's main risk at nothing.
- **Dupire under stochastic rates without the correction.** It misprices 5-year vanillas by 0.6 to 0.85 vol points.
