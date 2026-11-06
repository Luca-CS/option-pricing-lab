"""Shared inputs of the risk tests: a Black-Scholes world (flat market, flat SVI surface) and dated vanilla scripts."""

import datetime as dt

import numpy as np

from optlab import native
from optlab.structured.context import PricingContext
from optlab.structured.portfolio import Book, Position
from _structured_refs import FLAT, Q, R, S0, VOL, flat_surface

VALUATION = dt.date(2026, 10, 27)


def flat_ctx(vol=VOL, valuation=VALUATION, market=FLAT) -> PricingContext:
    spx = PricingContext.from_calibration()
    return PricingContext(market, flat_surface(vol, (0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0)), spx.heston, spx.slv,
                          valuation, "SPX")


def day(valuation, days: int) -> str:
    return (valuation + dt.timedelta(days=days)).isoformat()


def vanilla(K: float, days: int, kind: str = "call", valuation=VALUATION, N: float = 100.0) -> str:
    """A European call or put paying at expiry, `days` calendar days from the valuation (T = days / 365)."""
    d = day(valuation, days)
    payoff = f"max(SPX - {K}, 0)" if kind == "call" else f"max({K} - SPX, 0)"
    return f"underlying SPX\nparam N = {N}\nevent {d} pay {d}\n  pay N / {S0} * {payoff} as redemption\nend\n"


def digital(K: float, days: int, valuation=VALUATION, N: float = 100.0) -> str:
    d = day(valuation, days)
    return f"underlying SPX\nparam N = {N}\nevent {d} pay {d}\n  if SPX >= {K} then\n    pay N as coupon\n  end\nend\n"


def forward_contract(days: int, valuation=VALUATION, N: float = 100.0) -> str:
    d = day(valuation, days)
    return f"underlying SPX\nparam N = {N}\nevent {d} pay {d}\n  pay N * (SPX / {S0} - 1) as redemption\nend\n"


def book(*scripts, quantities=None) -> Book:
    quantities = quantities or [1.0] * len(scripts)
    return Book([Position(f"p{i}", s, q) for i, (s, q) in enumerate(zip(scripts, quantities))])


def engine_name() -> str:
    """The compiled CPU engine when the library is there, else NumPy (fewer paths are then used by the tests)."""
    return "cpu" if native.available() else "numpy"


def within(value, ref, se, k=4.0, floor=0.0):
    assert abs(value - ref) <= k * se + floor, f"{value} vs {ref}: {abs(value - ref) / max(se, 1e-300):.1f} SE (se {se})"


__all__ = ["FLAT", "Q", "R", "S0", "VOL", "np"]
