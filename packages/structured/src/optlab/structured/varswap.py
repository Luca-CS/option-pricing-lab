"""Variance and volatility swaps: the realised variance of a window of daily fixings, and its replication on the smile.

Realised variance of a window of n + 1 daily fixings F_0 .. F_n (n returns), annualised on 252 days:
    sigma^2 = (252 / n) sum_i ln(F_i / F_{i-1})^2
and its corridor version, counting a return only when the fixing before it lies in [L, U] (the market's convention for
a corridor variance swap, normalised by every return of the window, inside or not):
    sigma^2_corridor = (252 / n) sum_i ln(F_i / F_{i-1})^2 1{L <= F_{i-1} <= U}
L = 0 is an up-corridor's absence of a floor, U = infinity a down-corridor's absence of a cap.

The fair variance of a var swap is model-free when the spot moves continuously: the log-contract replication,
    K_var T = 2 [ int_0^F P(K) / K^2 dK + int_F^inf C(K) / K^2 dK ] / D(T),
with undiscounted out-of-the-money option prices read on the implied surface, and the same integral restricted to
[L, U] for a corridor (the corridor then read on the spot itself, monitored continuously). `fair_variance` computes it in
log-moneyness, k = ln K / F, where dK / K^2 = e^-k dk / F. Daily sampling, jumps and the corridor read on the previous
fixing move the Monte Carlo price away from it by a little: the tests bound the gap. A vol swap has no such replication:
its fair strike lies below sqrt(K_var) by a convexity that depends on the vol of vol, so it is priced by Monte Carlo
and its model reserve (SLV against local vol) carries that risk.
"""

from __future__ import annotations

import numpy as np

ANNUAL = 252.0


def realised_variance(fixings, lower=None, upper=None) -> np.ndarray:
    """(n_paths,) annualised realised variance of (n_paths, n + 1) daily fixings, in a corridor [lower, upper] read on
    the fixing before each return when either bound is given (bounds: numbers or (n_paths,) arrays)."""
    f = np.asarray(fixings, dtype=float)
    if f.ndim == 1:
        f = f[None, :]
    if f.shape[1] < 2:
        raise ValueError("a variance window needs at least two fixings")
    r2 = np.log(f[:, 1:] / f[:, :-1]) ** 2
    if lower is not None or upper is not None:
        prev = f[:, :-1]
        inside = np.ones_like(prev, dtype=bool)
        if lower is not None:
            inside &= prev >= np.asarray(lower, dtype=float).reshape(-1, 1)
        if upper is not None:
            inside &= prev <= np.asarray(upper, dtype=float).reshape(-1, 1)
        r2 = np.where(inside, r2, 0.0)
    return ANNUAL / r2.shape[1] * r2.sum(axis=1)


def _otm(k: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Undiscounted out-of-the-money option prices per unit forward at log-moneyness k and total variance w."""
    from scipy.special import ndtr

    sd = np.sqrt(np.maximum(w, 1e-300))
    d1 = -k / sd + 0.5 * sd
    d2 = d1 - sd
    strike = np.exp(k)
    call = ndtr(d1) - strike * ndtr(d2)
    put = strike * ndtr(-d2) - ndtr(-d1)
    return np.where(k >= 0.0, call, put)


def fair_variance(surface, market, T: float, lower: float | None = None, upper: float | None = None, n: int = 4001):
    """The replicated fair variance (annualised) of a var swap to T on the surface, in a corridor of absolute spot levels
    [lower, upper] when given. A flat vol (a number) gives its square, inside the corridor's share of the density."""
    F = float(market.F(T))
    if np.isscalar(surface):
        sd = float(surface) * np.sqrt(T)  # pyright: ignore[reportArgumentType] (a number, isscalar says so)
        w_atm = sd * sd
    else:
        w_atm = float(surface.w(0.0, T))
    sd = np.sqrt(max(w_atm, 1e-12))
    lo = -12.0 * sd if lower is None or lower <= 0 else max(np.log(lower / F), -12.0 * sd)
    hi = 12.0 * sd if upper is None or not np.isfinite(upper) else min(np.log(upper / F), 12.0 * sd)
    if hi <= lo:
        return 0.0
    k = np.linspace(lo, hi, n)
    w = np.full_like(k, w_atm) if np.isscalar(surface) else np.asarray(surface.w(k, np.full_like(k, T)), dtype=float)
    integrand = _otm(k, w) * np.exp(-k)
    return float(2.0 / T * np.trapezoid(integrand, k))
