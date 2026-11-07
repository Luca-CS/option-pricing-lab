"""Product formats (wrappers): how the flows of a script become a value for the investor.

- Note: the investor pays the notional N at issue and receives every flow; flows are discounted on the issuer
  curve D(t) exp(-s t). Price in % of N.
- Swap (upfront): no exchange of notional; the structured leg is the coupons, the upside and (redemption - N) at
  the redemption payment date, discounted on the risk-free curve. Upfront in % of N, investor's view.
On the same paths, Note(s=0) - Swap = 100 E[D(redemption payment time)]. Flows paid on or before the valuation date
(t <= 0, a product already running) are not part of the value.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from optlab.core.market import Market
from optlab.structured.script.evaluator import BatchFlows


@dataclass(frozen=True)
class Note:
    spread: float = 0.0  # issuer funding spread, continuously compounded

    name = "note"

    def discount(self, market: Market, t: float) -> float:
        return float(market.D(t)) * float(np.exp(-self.spread * t))

    def present_values(self, flows: BatchFlows, market: Market, notional: float) -> np.ndarray:
        pv = 0.0
        for (t, _tag), f in flows.flows.items():
            if t <= 0:
                continue
            pv = pv + market.D(t) * f if self.spread == 0 else pv + self.discount(market, t) * f
        return np.broadcast_to(pv, (flows.n_paths,)).astype(float)


@dataclass(frozen=True)
class Swap:
    name = "swap"

    def present_values(self, flows: BatchFlows, market: Market, notional: float) -> np.ndarray:
        pv = 0.0
        for (t, _tag), f in flows.flows.items():
            if t > 0:
                pv = pv + market.D(t) * f
        for t, w in flows.redemption_weights.items():
            if t > 0:
                pv = pv - notional * market.D(t) * w
        return np.broadcast_to(pv, (flows.n_paths,)).astype(float)


@dataclass(frozen=True)
class FundedSwap:
    """Swap whose investor also pays a funding leg, floating rate plus spread on the notional, until the product
    redeems. Single curve: the forward rate of a period [s, e] is (D(s) / D(e) - 1) / tau, tau in ACT/360, paid at
    the period's payment date. A period is paid when the product is still alive at its start, so with funding
    periods on the call dates the leg stops with the call. Rates are deterministic, so each amount is known and
    only the survival is random.

    periods: (start time, end time, payment time, accrual) tuples; event_times: observation time of each script
    event (to read the survival after each event)."""

    spread: float
    periods: tuple
    event_times: tuple

    name = "swap"

    def funding_amounts(self, market: Market, notional: float) -> np.ndarray:
        out = []
        for s, e, p, tau in self.periods:
            rate = (float(market.D(s)) / float(market.D(e)) - 1) / tau
            out.append(notional * (rate + self.spread) * tau * float(market.D(p)))
        return np.array(out)

    def survival(self, flows: BatchFlows) -> np.ndarray:
        """(n_paths, n_periods): alive at the start of each period (after the events observed up to then)."""
        alive = np.ones(flows.n_paths)
        cum = [alive]
        for d in flows.terminated:
            alive = alive - d
            cum.append(alive)
        times = np.asarray(self.event_times)
        cols = []
        for s, _, _, _ in self.periods:
            k = int(np.searchsorted(times, s + 1e-12, side="right"))  # events at or before the period start
            cols.append(np.broadcast_to(cum[k], (flows.n_paths,)))
        return np.stack(cols, axis=1)

    def present_values(self, flows: BatchFlows, market: Market, notional: float) -> np.ndarray:
        leg = Swap().present_values(flows, market, notional)
        future = np.array([p > 0 for _, _, p, _ in self.periods])
        amounts = self.funding_amounts(market, notional) * future
        return leg - self.survival(flows) @ amounts


def funding_periods(
    strike, months: int, freq_months: int, valuation, convention="modified_following", pay_lag: int = 5, calendar=None
) -> tuple:
    """Funding periods from the strike date: (start, end, payment, ACT/360 accrual), times from the valuation."""
    from optlab.structured.dates import schedule, year_fraction

    start, out = strike, []
    for end, pay in schedule(strike, months, freq_months, convention, pay_lag, calendar):
        out.append(
            (
                year_fraction(valuation, start),
                year_fraction(valuation, end),
                year_fraction(valuation, pay),
                year_fraction(start, end, "ACT/360"),
            )
        )
        start = end
    return tuple(out)


def pair_averages(values: np.ndarray) -> np.ndarray:
    """Average of each antithetic pair (the batch is stored as [half, mirrored half])."""
    d = np.asarray(values)
    if len(d) % 2:
        raise ValueError("a batch must hold an even number of paths")
    return 0.5 * (d[: len(d) // 2] + d[len(d) // 2 :])
