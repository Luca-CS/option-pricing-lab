"""Heston model: characteristic function, semi-closed-form vanilla prices (Lewis formula)
and calibration to implied volatilities.

    dS / S = (r - q) dt + sqrt(v) dW,      dv = kappa (theta - v) dt + xi sqrt(v) dZ,   d<W, Z> = rho dt
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import least_squares

from .blackscholes import black_price, black_vega, implied_vol


@dataclass
class HestonParams:
    v0: float
    kappa: float
    theta: float
    xi: float
    rho: float

    def feller(self) -> float:
        """2 kappa theta / xi^2: above 1 the variance never touches zero."""
        return 2 * self.kappa * self.theta / self.xi**2

    def to_dict(self) -> dict:
        return asdict(self)

    def as_array(self) -> np.ndarray:
        return np.array([self.v0, self.kappa, self.theta, self.xi, self.rho])


def char_function(u, T, p: HestonParams):
    """E[exp(i u X_T)] with X_T = ln(S_T / F_T), in the 'little trap' form of Albrecher et al.
    (no branch-cut discontinuity for long maturities)."""
    u = np.asarray(u, dtype=complex)
    iu = 1j * u
    beta = p.kappa - p.rho * p.xi * iu
    d = np.sqrt(beta**2 + p.xi**2 * (iu + u**2))
    g = (beta - d) / (beta + d)
    e = np.exp(-d * T)
    C = p.kappa * p.theta / p.xi**2 * ((beta - d) * T - 2.0 * np.log((1 - g * e) / (1 - g)))
    Dv = (beta - d) / p.xi**2 * (1 - e) / (1 - g * e)
    return np.exp(C + Dv * p.v0)


_GL_X, _GL_W = np.polynomial.legendre.leggauss(64)


def _lewis_integral(F, K, T, p: HestonParams):
    """Integral of Re[exp(i u ln(F/K)) phi(u - i/2)] / (u^2 + 1/4) over u >= 0, for one maturity."""
    vol_scale = np.sqrt(max(p.v0, p.theta, 1e-4) * T)
    u_max = float(np.clip(40.0 / vol_scale, 60.0, 5000.0))
    edges = np.linspace(0.0, u_max, 9)
    u = (0.5 * (edges[1:, None] - edges[:-1, None]) * _GL_X[None, :]
         + 0.5 * (edges[1:, None] + edges[:-1, None])).ravel()
    w = (0.5 * (edges[1:, None] - edges[:-1, None]) * _GL_W[None, :]).ravel()
    phi = char_function(u - 0.5j, T, p)
    logfk = np.log(np.asarray(F, dtype=float) / np.asarray(K, dtype=float))
    integrand = np.real(np.exp(1j * np.multiply.outer(logfk, u)) * phi) / (u**2 + 0.25)
    return integrand @ w


def heston_price(F, K, T, p: HestonParams, D=1.0, cp="call"):
    """Vanilla price by the Lewis (2001) formula: C = D [F - sqrt(F K) / pi * integral]."""
    K = np.atleast_1d(np.asarray(K, dtype=float))
    call = D * (F - np.sqrt(F * K) / np.pi * _lewis_integral(F, K, T, p))
    if str(cp).lower().startswith("c"):
        return call
    return call - D * (F - K)


def heston_iv(F, K, T, p: HestonParams, D=1.0):
    """Black implied volatility of Heston prices (out-of-the-money side for accuracy)."""
    K = np.atleast_1d(np.asarray(K, dtype=float))
    call = heston_price(F, K, T, p, D, "call")
    put = call - D * (F - K)
    otm_call = K >= F
    price = np.where(otm_call, call, put)
    return np.where(otm_call, implied_vol(price, F, K, T, D, "call"), implied_vol(price, F, K, T, D, "put"))


BOUNDS = (np.array([1e-4, 0.05, 1e-4, 0.05, -0.99]), np.array([1.0, 20.0, 1.0, 3.0, 0.5]))


def calibrate_heston(quotes, x0: HestonParams | None = None, weights=None):
    """Least-squares fit to implied vols.

    quotes: iterable of maturity slices, each a dict with T, F, D, K (array) and iv (array).
    Residuals are vega-scaled price errors, i.e. first-order implied-vol errors, which avoids
    inverting every model price inside the optimiser.
    """
    slices = [dict(s) for s in quotes]
    for s in slices:
        s["K"] = np.asarray(s["K"], dtype=float)
        s["iv"] = np.asarray(s["iv"], dtype=float)
        s["price"] = black_price(s["F"], s["K"], s["T"], s["iv"], s["D"], "call")
        s["vega"] = np.maximum(black_vega(s["F"], s["K"], s["T"], s["iv"], s["D"]), 1e-8 * s["F"])
    if weights is None:
        weights = [np.ones_like(s["K"]) for s in slices]
    if x0 is None:
        atm = np.mean([np.interp(0.0, np.log(s["K"] / s["F"]), s["iv"]) for s in slices])
        x0 = HestonParams(atm**2, 2.0, atm**2, 0.6, -0.6)

    def residuals(x):
        p = HestonParams(*x)
        out = []
        for s, w in zip(slices, weights):
            model = heston_price(s["F"], s["K"], s["T"], p, s["D"], "call")
            out.append(np.sqrt(w) * (model - s["price"]) / s["vega"])
        return np.concatenate(out)

    start = np.clip(x0.as_array(), BOUNDS[0] + 1e-6, BOUNDS[1] - 1e-6)
    sol = least_squares(residuals, start, bounds=BOUNDS, x_scale=np.array([0.01, 1.0, 0.01, 0.3, 0.3]),
                        xtol=1e-10, ftol=1e-12, max_nfev=400)
    return HestonParams(*sol.x), sol
