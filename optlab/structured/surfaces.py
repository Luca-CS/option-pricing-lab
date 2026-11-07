"""Faster evaluation of the SVI surface for path simulation.

Local volatility asks the surface for every path at one time per step. `SVISurface.derivatives` broadcasts that
scalar time over all paths, then interpolates theta, searches the slices and masks per path. With a scalar time
the same formulas need one interpolation and one slice pair: `FastSVISurface` takes that branch and returns the
same numbers bit for bit (same operations in the same order), so prices do not move.
"""

from __future__ import annotations

import numpy as np

from optlab.svi import SVISurface, g_function, svi_derivs


class FastSVISurface(SVISurface):
    def derivatives(self, k, T):
        if np.ndim(T) != 0:
            return super().derivatives(k, T)
        k = np.asarray(k, dtype=float)
        T = float(T)
        th, dth = float(self.theta(T)), float(self.dtheta(T))
        n = len(self.maturities)
        j = int(np.clip(np.searchsorted(self.maturities, T, side="left"), 0, n))
        if j == n:  # beyond the last pillar: last slice scaled by theta(T) / theta_n
            w, wk, wkk = svi_derivs(self.params[-1], k)
            s, ds = th / self.thetas[-1], dth / self.thetas[-1]
            return s * w, s * wk, s * wkk, ds * w
        lo = self.thetas[j - 1] if j > 0 else 0.0
        hi = self.thetas[j]
        alpha, dalpha = (th - lo) / (hi - lo), dth / (hi - lo)
        w0, wk0, wkk0 = self._slice_derivs(j - 1, k)
        w1, wk1, wkk1 = self._slice_derivs(j, k)
        return (
            (1 - alpha) * w0 + alpha * w1,
            (1 - alpha) * wk0 + alpha * wk1,
            (1 - alpha) * wkk0 + alpha * wkk1,
            dalpha * (w1 - w0),
        )


SKEW_WIDTH = (
    0.3  # log-moneyness over which a skew bump saturates: far wings keep their shape, so Lee's bounds are not touched
)
SKEW_T_FLOOR = 1 / 12  # no shorter maturity sees a larger rotation
_SKEW_NORM = 1.0 / (np.tanh(-np.log(0.9) / SKEW_WIDTH) + np.tanh(np.log(1.1) / SKEW_WIDTH))


class BumpedSurface:
    """A surface with its implied volatilities moved: sigma'(k, T) = sigma(k, T) + vol + skew(k, T).

    `skew` is the rise of the 90 % volatility against the 110 % one at one year, in fractions: skew(k, T) = -skew c sqrt(1 / T) tanh(k / l),
    with k the log-moneyness to the forward and c such that the 90-110 % gap at one year moves by exactly `skew`. It rotates around the
    forward and saturates at |k| of a few l. The maturity scale is sqrt(1 / T), held below one month. Every derivative is exact, so
    Dupire reads the bumped surface as it reads the base one."""

    def __init__(self, surface, vol: float = 0.0, skew: float = 0.0):
        self.base, self.vol, self.skew = surface, float(vol), float(skew)
        self.maturities = surface.maturities

    def _bump(self, k, T):
        """The bump in volatility and its derivatives in k, k twice and T."""
        k = np.asarray(k, dtype=float)
        T = np.asarray(T, dtype=float)
        if not self.skew:
            zero = np.zeros(np.broadcast(k, T).shape)
            return zero + self.vol, zero, zero, zero
        held = T < SKEW_T_FLOOR
        scale = np.sqrt(1.0 / np.maximum(T, SKEW_T_FLOOR))
        dscale = np.where(held, 0.0, -0.5 * scale / np.maximum(T, SKEW_T_FLOOR))
        a = -self.skew * _SKEW_NORM
        u = k / SKEW_WIDTH
        tanh = np.tanh(u)
        sech2 = 1.0 - tanh * tanh
        return (
            self.vol + a * scale * tanh,
            a * scale * sech2 / SKEW_WIDTH,
            -2.0 * a * scale * tanh * sech2 / SKEW_WIDTH**2,
            a * dscale * tanh,
        )

    def derivatives(self, k, T):
        w, wk, wkk, wT = self.base.derivatives(k, T)
        T = np.broadcast_to(np.asarray(T, dtype=float), w.shape)
        s = np.sqrt(np.maximum(w, 1e-300) / T)
        sk = wk / (2 * T * s)
        skk = (wkk - 2 * T * sk**2) / (2 * T * s)
        sT = (wT - s**2) / (2 * T * s)
        b, bk, bkk, bT = self._bump(k, T)
        s2, sk2, skk2, sT2 = s + b, sk + bk, skk + bkk, sT + bT
        return T * s2**2, 2 * T * s2 * sk2, 2 * T * (sk2**2 + s2 * skk2), s2**2 + 2 * T * s2 * sT2

    def w(self, k, T):
        return self.derivatives(k, T)[0]

    def theta(self, T):
        """ATM total implied variance: the rotation leaves it where it is."""
        T = np.asarray(T, dtype=float)
        return T * (np.sqrt(self.base.theta(T) / T) + self.vol) ** 2

    def iv(self, k, T):
        return np.sqrt(np.maximum(self.w(k, T), 0.0) / np.asarray(T, dtype=float))

    def local_var(self, k, T, floor=1e-4, cap=4.0):
        T = np.maximum(np.asarray(T, dtype=float), 1.0 / 365)
        w, wk, wkk, wT = self.derivatives(k, T)
        g = g_function(w, wk, wkk, np.broadcast_to(k, w.shape))
        return np.clip(wT / np.maximum(g, 1e-6), floor, cap)


def surface_arbitrage(surface, horizon: float, n_t: int = 40, n_k: int = 161) -> dict:
    """Butterfly (g > 0) and calendar (dw/dT >= 0) checks from -5 to +4 ATM standard deviations, from a week to the horizon."""
    min_g, min_wT = np.inf, np.inf
    for T in np.linspace(1 / 52, max(horizon, 1 / 12), n_t):
        k = np.linspace(-5.0, 4.0, n_k) * float(np.sqrt(surface.theta(T)))
        w, wk, wkk, wT = surface.derivatives(k, float(T))
        min_g = min(min_g, float(g_function(w, wk, wkk, k).min()))
        min_wT = min(min_wT, float(np.min(wT)))
    return {"min_g": min_g, "min_wT": min_wT, "butterfly_free": min_g > 0, "calendar_free": min_wT >= -1e-6}
