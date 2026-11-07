"""Bumps of the discount and forward curves of a `Market`, in parallel or by tent on the tenors of the grid.

A move of b(T) (continuously compounded, 1 bp = 1e-4) on a rate:
- the discount rate: D'(T) = D(T) exp(-b(T) T), the forward unchanged (`rho_discount`);
- the forward's carry: F'(T) = F(T) exp(b(T) T), the discounts unchanged (`rho_forward`); a dividend yield or a repo
  rate moves the carry the other way;
- the rate itself: both, so that F = S exp((r - q) T) follows r with the dividends unchanged (`rho`).
b(T) = parallel + sum_j h_j psi_j(T) with the tenor tents of `grid`. The bumped market keeps every pillar of the base
and adds the tenors and a monthly grid, on which ln D and ln F / S are linear between the base pillars: the base curve is
reproduced exactly and the bump is sampled finely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from optlab.core.market import Market
from optlab.risk import grid

BP = 1e-4


@dataclass
class CurveMove:
    """A rate move in basis points: parallel, plus one bump per tenor of the grid ({tenor index: bp})."""

    parallel: float = 0.0
    tenors: dict = field(default_factory=dict)

    def __post_init__(self):
        self.parallel = float(self.parallel)
        self.tenors = {int(j): float(v) for j, v in self.tenors.items()}

    def __bool__(self) -> bool:
        return bool(self.parallel) or any(v for v in self.tenors.values())

    def __add__(self, other: "CurveMove") -> "CurveMove":
        tenors = dict(self.tenors)
        for j, v in other.tenors.items():
            tenors[int(j)] = tenors.get(int(j), 0.0) + float(v)
        return CurveMove(self.parallel + other.parallel, tenors)

    def scaled(self, c: float) -> "CurveMove":
        return CurveMove(c * self.parallel, {int(j): c * v for j, v in self.tenors.items()})

    def rate(self, T) -> np.ndarray:
        """b(T) as a rate (fraction)."""
        T = np.asarray(T, dtype=float)
        out = np.full(T.shape, self.parallel * BP)
        if self.tenors:
            psi = grid.tenor_tents(T, order=0)[0]
            for j, v in self.tenors.items():
                out = out + float(v) * BP * psi[int(j)]
        return out

    def to_dict(self) -> dict:
        return {"parallel": self.parallel, "tenors": {str(int(j)): float(v) for j, v in self.tenors.items()}}

    @classmethod
    def from_dict(cls, d) -> "CurveMove":
        if d is None:
            return cls()
        if isinstance(d, (int, float)):
            return cls(float(d))
        return cls(float(d.get("parallel", 0.0)), {int(j): float(v) for j, v in (d.get("tenors") or {}).items()})


def _pillars(market: Market) -> np.ndarray:
    base = np.asarray(market.to_dict()["pillars"], dtype=float)
    top = max(float(base.max()), grid.TENORS[-1]) + 1.0
    dense = np.arange(1, int(np.ceil(top * 12)) + 1) / 12.0
    return np.unique(np.concatenate([base, np.asarray(grid.TENORS), dense]))


def bumped_market(
    market: Market, discount: CurveMove | None = None, carry: CurveMove | None = None, dividend_relative: float = 0.0
) -> Market:
    """The market with the discount rate moved by `discount` and the forward's carry by `carry` (both in bp), and the
    dividends scaled by (1 + dividend_relative) as `structured.costs.forward_bumped` does (steps and season kept)."""
    if not discount and not carry and not dividend_relative:
        return market
    T = _pillars(market)
    D, F = np.asarray(market.D(T), dtype=float), np.asarray(market.F(T), dtype=float)
    if discount:
        D = D * np.exp(-discount.rate(T) * T)
    if dividend_relative:
        carried = np.log(market.spot / (np.asarray(market.F(T)) * np.asarray(market.D(T))))
        F = F * np.exp(-dividend_relative * carried)
    if carry:
        F = F * np.exp(carry.rate(T) * T)
    return Market(market.spot, T, D, F)


def forward_ratio(base: Market, moved: Market, times) -> np.ndarray:
    """F'(t) / F(t) at the simulated times: the factor that moves sticky-moneyness paths onto the bumped forward."""
    t = np.asarray(times, dtype=float)
    return np.asarray(moved.F(t), dtype=float) / np.asarray(base.F(t), dtype=float) * (base.spot / moved.spot)
