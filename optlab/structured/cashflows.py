"""Product formats (wrappers): how the flows of a script become a value for the investor.

- Note: the investor pays the notional N at issue and receives every flow; flows are discounted on the issuer
  curve D(t) exp(-s t). Price in % of N.
- Swap (upfront): no exchange of notional; the structured leg is the coupons, the upside and (redemption - N) at
  the redemption payment date, discounted on the risk-free curve. Upfront in % of N, investor's view.
On the same paths, Note(s=0) - Swap = 100 E[D(redemption payment time)].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..market import Market
from .script.evaluator import BatchFlows


@dataclass(frozen=True)
class Note:
    spread: float = 0.0  # issuer funding spread, continuously compounded

    name = "note"

    def discount(self, market: Market, t: float) -> float:
        return float(market.D(t)) * float(np.exp(-self.spread * t))

    def present_values(self, flows: BatchFlows, market: Market, notional: float) -> np.ndarray:
        pv = 0.0
        for (t, _tag), f in flows.flows.items():
            pv = pv + market.D(t) * f if self.spread == 0 else pv + self.discount(market, t) * f
        return np.broadcast_to(pv, (flows.n_paths,)).astype(float)


@dataclass(frozen=True)
class Swap:
    name = "swap"

    def present_values(self, flows: BatchFlows, market: Market, notional: float) -> np.ndarray:
        pv = 0.0
        for (t, _tag), f in flows.flows.items():
            pv = pv + market.D(t) * f
        for t, w in flows.redemption_weights.items():
            pv = pv - notional * market.D(t) * w
        return np.broadcast_to(pv, (flows.n_paths,)).astype(float)


def pair_averages(values: np.ndarray) -> np.ndarray:
    """Average of each antithetic pair (the batch is stored as [half, mirrored half])."""
    d = np.asarray(values)
    if len(d) % 2:
        raise ValueError("a batch must hold an even number of paths")
    return 0.5 * (d[: len(d) // 2] + d[len(d) // 2:])
