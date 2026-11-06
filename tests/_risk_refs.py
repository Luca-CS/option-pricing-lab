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
    return f"underlying SPX\nparam N = {N}\nevent {d} pay {d}\n  if SPX >= {K} then\n    pay N as coupon\n  end\n  pay 0 as redemption\nend\n"


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


def bs_cross(S, K, T, r, q, vol):
    """Analytic vanna (d2V / dS dvol) and volga (d2V / dvol2) of a call or a put (same for both)."""
    from scipy.stats import norm
    sd = vol * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * vol**2) * T) / sd
    d2 = d1 - sd
    vega = S * np.exp(-q * T) * norm.pdf(d1) * np.sqrt(T)
    return {"vanna": -np.exp(-q * T) * norm.pdf(d1) * d2 / vol, "volga": vega * d1 * d2 / vol}


def worst_put_shifted(K, T, vols, rho, r, qs, a=(1.0, 1.0)):
    """E[(K - min(a1 X1, a2 X2))+], X = S_T / S_0 lognormal: the worst-of put after spot moves a (initial fixings kept)."""
    from scipy.integrate import quad
    from scipy.stats import norm
    from optlab.blackscholes import black_price
    (v1, v2), (q1, q2) = vols, qs
    m1, s1 = (r - q1 - 0.5 * v1**2) * T + np.log(a[0]), v1 * np.sqrt(T)
    m2, s2 = (r - q2 - 0.5 * v2**2) * T + np.log(a[1]), v2 * np.sqrt(T)
    s2c = s2 * np.sqrt(1 - rho**2)

    def put2(c, z):
        fwd = np.exp(m2 + rho * s2 * z + 0.5 * s2c**2)
        return float(black_price(fwd, c, 1.0, s2c, 1.0, "put"))

    def f(z):
        x1 = np.exp(m1 + s1 * z)
        inner = (K - x1) + put2(x1, z) if x1 < K else put2(K, z)
        return inner * norm.pdf(z)

    return quad(f, -9, 9, limit=400, epsabs=1e-12)[0]
