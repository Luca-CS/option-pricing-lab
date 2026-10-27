"""Raw SVI slices fitted under no-arbitrage constraints, interpolated into a surface for Dupire.

A single SSVI slice has three parameters and cannot follow the SPX smile: it ties the wing
curvature to the skew, which leaves the deep put wing ~70 bp too low. Raw SVI (Gatheral, 2004),

    w(k) = a + b (rho (k - m) + sqrt((k - m)^2 + sigma^2)),

has five. Each slice is fitted by the quasi-explicit method (Zeliade, 2009; De Marco and
Martini): for fixed (m, sigma) the problem is a small quadratic programme in (a, b rho sigma,
b sigma) whose constraints are linear: |rho| <= 1, Lee's wing bound, and w above the previous
slice on a grid (no calendar spread). Butterflies (g > 0 on a grid from -6 to +4 ATM standard
deviations) are a penalty in the outer search over (m, sigma). The extended-SSVI fit,
arbitrage-free by construction, provides the starting point.

Between pillars the total variance is interpolated linearly in the ATM total variance theta(T),
itself a monotone cubic in T:  w(k, T) = (1 - alpha) w_i(k) + alpha w_{i+1}(k) with
alpha = (theta(T) - theta_i) / (theta_{i+1} - theta_i). Ordered slices stay ordered, so the
calendar condition carries over to the whole surface.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import PchipInterpolator

from .ssvi import fit_ssvi


def svi_w(p, k):
    a, b, rho, m, sig = p
    x = k - m
    return a + b * (rho * x + np.sqrt(x * x + sig * sig))


def svi_derivs(p, k):
    a, b, rho, m, sig = p
    x = k - m
    R = np.sqrt(x * x + sig * sig)
    return a + b * (rho * x + R), b * (rho + x / R), b * sig * sig / R**3


def g_function(w, wk, wkk, k):
    return (1 - k * wk / (2 * w)) ** 2 - wk**2 / 4 * (1 / w + 0.25) + wkk / 2


def ssvi_slice_to_svi(theta, psi, rho):
    """Raw-SVI parameters of an SSVI slice (phi = psi / theta)."""
    phi = psi / theta
    return np.array([0.5 * theta * (1 - rho**2), 0.5 * psi, rho, -rho / phi, np.sqrt(1 - rho**2) / phi])


class SVISurface:
    def __init__(self, maturities, params):
        self.maturities = np.asarray(maturities, dtype=float)
        self.params = np.asarray(params, dtype=float)  # (n, 5): a, b, rho, m, sigma
        self.thetas = np.array([svi_w(p, 0.0) for p in self.params])
        t = np.concatenate([[0.0], self.maturities])
        th = np.concatenate([[0.0], self.thetas])
        self._theta = PchipInterpolator(t, th, extrapolate=False)
        self._dtheta = self._theta.derivative()
        self._slope_last = (th[-1] - th[-2]) / (t[-1] - t[-2])

    def theta(self, T):
        T = np.asarray(T, dtype=float)
        last = self.maturities[-1]
        inside = self._theta(np.clip(T, 0.0, last))
        return np.where(T > last, self.thetas[-1] + self._slope_last * (T - last), inside)

    def dtheta(self, T):
        T = np.asarray(T, dtype=float)
        inside = self._dtheta(np.clip(T, 0.0, self.maturities[-1]))
        return np.where(T > self.maturities[-1], self._slope_last, inside)

    def _slice_derivs(self, i, k):
        if i < 0:  # the zero slice before the first maturity
            z = np.zeros_like(k)
            return z, z, z
        return svi_derivs(self.params[i], k)

    def derivatives(self, k, T):
        """w, dw/dk, d2w/dk2, dw/dT. T may be a scalar or broadcast with k."""
        k, T = np.broadcast_arrays(np.asarray(k, dtype=float), np.asarray(T, dtype=float))
        out = [np.empty(k.shape) for _ in range(4)]
        th = self.theta(T)
        dth = self.dtheta(T)
        n = len(self.maturities)
        seg = np.clip(np.searchsorted(self.maturities, T, side="left"), 0, n)  # slice index above T
        for j in np.unique(seg):
            sel = seg == j
            kk = k[sel]
            if j == n:  # beyond the last pillar: last slice scaled by theta(T) / theta_n
                w, wk, wkk = svi_derivs(self.params[-1], kk)
                s = th[sel] / self.thetas[-1]
                ds = dth[sel] / self.thetas[-1]
                vals = (s * w, s * wk, s * wkk, ds * w)
            else:
                lo = self.thetas[j - 1] if j > 0 else 0.0
                hi = self.thetas[j]
                alpha = (th[sel] - lo) / (hi - lo)
                dalpha = dth[sel] / (hi - lo)
                w0, wk0, wkk0 = self._slice_derivs(j - 1, kk)
                w1, wk1, wkk1 = self._slice_derivs(j, kk)
                vals = ((1 - alpha) * w0 + alpha * w1, (1 - alpha) * wk0 + alpha * wk1,
                        (1 - alpha) * wkk0 + alpha * wkk1, dalpha * (w1 - w0))
            for o, v in zip(out, vals):
                o[sel] = v
        return tuple(out)

    def w(self, k, T):
        return self.derivatives(k, T)[0]

    def iv(self, k, T):
        return np.sqrt(np.maximum(self.w(k, T), 0.0) / np.asarray(T, dtype=float))

    def density_g(self, k, T):
        w, wk, wkk, _ = self.derivatives(k, T)
        return g_function(w, wk, wkk, np.broadcast_to(k, w.shape))

    def local_var(self, k, T, floor=1e-4, cap=4.0):
        """Dupire local variance sigma_loc^2(T, K), written in total implied variance."""
        T = np.maximum(np.asarray(T, dtype=float), 1.0 / 365)
        w, wk, wkk, wT = self.derivatives(k, T)
        g = g_function(w, wk, wkk, np.broadcast_to(k, w.shape))
        return np.clip(wT / np.maximum(g, 1e-6), floor, cap)

    def local_vol(self, k, T):
        return np.sqrt(self.local_var(k, T))

    def to_dict(self) -> dict:
        return {"kind": "svi", "maturities": self.maturities.tolist(), "params": self.params.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "SVISurface":
        return cls(d["maturities"], d["params"])


def _inner_fit(m, sig, k, w_mkt, wt, k_grid, w_floor, rho_max=0.0, x0=None):
    """Quasi-explicit step: with m and sigma fixed, w = a + d y + c sqrt(y^2 + 1), y = (k - m) / sigma,
    is linear in (a, d, c). Constraints, all linear:
      c >= 0, -c <= d <= rho_max c (rho in [-1, rho_max]), c + |d| <= 2 sigma (Lee: wing slopes <= 2),
      w(k_j) >= w_floor(k_j) on a grid (above the previous slice: no calendar spread; and >= 0).
    """
    y = (k - m) / sig
    X = np.column_stack([np.ones_like(y), y, np.sqrt(y * y + 1)])
    yg = (k_grid - m) / sig
    Xg = np.column_stack([np.ones_like(yg), yg, np.sqrt(yg * yg + 1)])
    sw = np.sqrt(wt)
    if x0 is None:
        x0, *_ = np.linalg.lstsq(X * sw[:, None], w_mkt * sw, rcond=None)
    A = np.vstack([[0, 0, 1], [0, 1, 1], [0, -1, rho_max], [0, -1, -1], [0, 1, -1], Xg])
    lb = np.concatenate([[0, 0, 0, -2 * sig, -2 * sig], w_floor])
    ub = np.concatenate([[np.inf] * 5, np.full(len(k_grid), np.inf)])
    from scipy.optimize import LinearConstraint, minimize

    H = (X * wt[:, None]).T @ X
    f = (X * wt[:, None]).T @ w_mkt
    res = minimize(lambda x: 0.5 * x @ H @ x - f @ x, x0, jac=lambda x: H @ x - f, method="SLSQP",
                   constraints=[LinearConstraint(A, lb, ub)], options={"maxiter": 300, "ftol": 1e-14})
    x = res.x
    r = X @ x - w_mkt
    return x, float(r @ (wt * r))


def _to_raw(m, sig, a, d, c):
    b = c / sig
    rho = d / c if c > 0 else 0.0
    return np.array([a, b, np.clip(rho, -0.999999, 0.999999), m, sig])


CALENDAR_K = np.linspace(-1.0, 0.5, 61)  # absolute log-moneyness band where slices may not cross


def fit_svi_surface(slices, weights=None, rho_max=0.0):
    """Extended-SSVI fit first (arbitrage-free starting point), then each raw-SVI slice by the
    quasi-explicit method of Zeliade / De Marco and Martini: an outer Nelder-Mead on (m, sigma)
    around a constrained linear least squares on (a, d, c), in total variance weighted to
    approximate implied-vol errors. Slices are fitted from short to long so each one can be
    constrained to lie above the previous one, on the slice's own range (-6 to +4 ATM standard
    deviations) and on the absolute band CALENDAR_K. rho is capped at rho_max (equity skew):
    otherwise short slices can drift to a positive rho with the SVI minimum far to the right of the
    quotes, which fits the data range but extrapolates absurdly.

    Returns (SVISurface, the SSVI starting surface)."""
    from scipy.optimize import minimize

    start, _ = fit_ssvi(slices, weights)
    if weights is None:
        weights = [np.ones_like(np.asarray(s["k"], dtype=float)) for s in slices]
    order = np.argsort([s["T"] for s in slices])
    params, prev = [], None
    for i in order:
        s = slices[i]
        k = np.asarray(s["k"], dtype=float)
        iv = np.asarray(s["iv"], dtype=float)
        ok = np.isfinite(iv)
        k, iv, wt = k[ok], iv[ok], np.asarray(weights[i], dtype=float)[ok]
        T = float(s["T"])
        w_mkt = iv * iv * T
        wt_w = wt / (2 * iv * T) ** 2  # d(iv) ~ dw / (2 iv T)
        th, psi, rho = (float(np.asarray(v)) for v in start.params(T))
        p0 = ssvi_slice_to_svi(th, psi, rho)
        sd = np.sqrt(th)
        k_grid = np.unique(np.concatenate([np.linspace(-6.0, 4.0, 41) * sd, CALENDAR_K]))
        w_floor = np.maximum(svi_w(prev, k_grid), 0.0) if prev is not None else np.zeros_like(k_grid)
        k_lo, k_hi = k.min(), k.max()

        g_grid = np.linspace(-6.0, 4.0, 81) * sd
        scale = float(wt_w @ (w_mkt * w_mkt)) * 1e-4  # penalty of the order of a 1 % relative error

        def outer(z):
            m, sig = z[0], np.exp(z[1])
            if not (k_lo - sd <= m <= k_hi + sd) or not (1e-3 <= sig <= 3.0):
                return 1e6
            x, sse = _inner_fit(m, sig, k, w_mkt, wt_w, k_grid, w_floor, rho_max)
            g = g_function(*svi_derivs(_to_raw(m, sig, *x), g_grid), g_grid)
            return sse + scale * 1e4 * float(np.sum(np.maximum(0.02 - g, 0.0) ** 2))  # butterfly

        best = None
        for z0 in ([p0[3], np.log(max(p0[4], 1e-3))], [0.0, np.log(sd)], [0.5 * k_lo, np.log(2 * sd)]):
            res = minimize(outer, z0, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-14, "maxiter": 600})
            if best is None or res.fun < best.fun:
                best = res
        m, sig = best.x[0], np.exp(best.x[1])
        (a, d, c), _ = _inner_fit(m, sig, k, w_mkt, wt_w, k_grid, w_floor, rho_max)
        p = _to_raw(m, sig, a, d, c)
        params.append(p)
        prev = p
    return SVISurface(np.array([slices[i]["T"] for i in order]), np.array(params)), start


def svi_arbitrage_report(surf: SVISurface, n_t=120) -> dict:
    """Butterfly check (g > 0) from -6 to +4 ATM standard deviations at each maturity, and calendar
    check (w non-decreasing in T) on the band CALENDAR_K, between and across pillars."""
    T = np.linspace(max(surf.maturities[0] / 4, 1 / 365), surf.maturities[-1], n_t)
    z = np.linspace(-6.0, 4.0, 201)
    sd = np.sqrt(surf.theta(T))
    min_g, min_cal = np.inf, np.inf
    for j in range(len(T)):
        k = z * sd[j]
        min_g = min(min_g, float(surf.density_g(k, T[j]).min()))
    k_abs = np.linspace(CALENDAR_K[0], CALENDAR_K[-1], 301)
    W = np.array([surf.w(k_abs, t) for t in T])
    min_cal = float(np.diff(W, axis=0).min())
    return {"min_g": min_g, "min_calendar_increment": min_cal, "butterfly_free": bool(min_g > 0),
            "calendar_free": bool(min_cal >= -1e-6), "max_lee_slope": float(np.max(surf.params[:, 1] * (
                1 + np.abs(surf.params[:, 2]))))}
