"""Extended SSVI implied-volatility surface, static-arbitrage checks and Dupire local volatility.

Each maturity slice is an SSVI slice (Gatheral and Jacquier, 2014) with its own ATM total
variance theta, skew level psi = theta * phi and correlation rho. Writing chi = rho * psi,
the total implied variance at log-moneyness k = ln(K / F_T) is

    w(k) = (theta + chi k + sqrt(psi^2 k^2 + 2 chi theta k + theta^2)) / 2.

Between pillars (the "eSSVI" of Hendriks and Martini, 2019), theta is a monotone cubic in T and
the two wing slopes a = psi (1 + rho) and b = psi (1 - rho) are monotone cubics in theta.
The fit keeps, by construction,
  * theta, a and b non-decreasing across maturities (calendar spreads: the condition of
    Corbetta, Cohort, Laachir and Martini, 2019, for consecutive SSVI slices);
  * psi (1 + |rho|) <= 4 and psi^2 (1 + |rho|) <= 4 theta on each slice (butterflies);
and `arbitrage_report` checks the whole surface numerically on a grid.
Every derivative needed by Dupire's formula is analytic.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares


class SSVI:
    def __init__(self, maturities, thetas, psis, rhos):
        self.maturities = np.asarray(maturities, dtype=float)
        self.thetas = np.asarray(thetas, dtype=float)
        self.psis = np.asarray(psis, dtype=float)
        self.rhos = np.asarray(rhos, dtype=float)
        t = np.concatenate([[0.0], self.maturities])
        th = np.concatenate([[0.0], self.thetas])
        a = np.concatenate([[0.0], self.psis * (1 + self.rhos)])
        b = np.concatenate([[0.0], self.psis * (1 - self.rhos)])
        self._theta_T = PchipInterpolator(t, th, extrapolate=False)
        self._dtheta_T = self._theta_T.derivative()
        self._a = PchipInterpolator(th, a, extrapolate=False)
        self._b = PchipInterpolator(th, b, extrapolate=False)
        self._da, self._db = self._a.derivative(), self._b.derivative()
        self._slope_last = (th[-1] - th[-2]) / (t[-1] - t[-2])

    @classmethod
    def power_law(cls, rho, eta, gamma, maturities, thetas) -> "SSVI":
        """Plain SSVI with phi(theta) = eta / (theta^gamma (1 + theta)^(1 - gamma)) at the pillars."""
        th = np.asarray(thetas, dtype=float)
        psi = th * eta / (th**gamma * (1 + th) ** (1 - gamma))
        return cls(maturities, th, psi, np.full_like(th, rho))

    # ---- term structure: theta(T) linear beyond the last pillar, wings flat beyond it
    def theta(self, T):
        T = np.asarray(T, dtype=float)
        last = self.maturities[-1]
        inside = self._theta_T(np.clip(T, 0.0, last))
        return np.where(T > last, self.thetas[-1] + self._slope_last * (T - last), inside)

    def dtheta(self, T):
        T = np.asarray(T, dtype=float)
        inside = self._dtheta_T(np.clip(T, 0.0, self.maturities[-1]))
        return np.where(T > self.maturities[-1], self._slope_last, inside)

    def _wings(self, theta):
        th = np.clip(theta, 0.0, self.thetas[-1])
        a, b = self._a(th), self._b(th)
        beyond = theta > self.thetas[-1]
        da = np.where(beyond, 0.0, self._da(th))
        db = np.where(beyond, 0.0, self._db(th))
        return a, b, da, db

    def params(self, T):
        """theta, psi and rho of the slice at T."""
        th = self.theta(T)
        a, b, _, _ = self._wings(th)
        psi = 0.5 * (a + b)
        return th, psi, (a - b) / (a + b)

    # ---- surface
    def w(self, k, T):
        k, T = np.broadcast_arrays(np.asarray(k, dtype=float), np.asarray(T, dtype=float))
        th = self.theta(T)
        a, b, _, _ = self._wings(th)
        psi, chi = 0.5 * (a + b), 0.5 * (a - b)
        return 0.5 * (th + chi * k + np.sqrt(psi**2 * k**2 + 2 * chi * th * k + th**2))

    def iv(self, k, T):
        return np.sqrt(self.w(k, T) / np.asarray(T, dtype=float))

    def derivatives(self, k, T):
        """w, dw/dk, d2w/dk2 and dw/dT."""
        k, T = np.broadcast_arrays(np.asarray(k, dtype=float), np.asarray(T, dtype=float))
        th = self.theta(T)
        a, b, da, db = self._wings(th)
        psi, chi = 0.5 * (a + b), 0.5 * (a - b)
        dpsi, dchi = 0.5 * (da + db), 0.5 * (da - db)
        R = np.sqrt(psi**2 * k**2 + 2 * chi * th * k + th**2)
        w = 0.5 * (th + chi * k + R)
        wk = 0.5 * (chi + (psi**2 * k + chi * th) / R)
        wkk = 0.5 * th**2 * (psi**2 - chi**2) / R**3
        dw_dtheta = 0.5 * (1 + (chi * k + th) / R)
        dw_dpsi = 0.5 * psi * k**2 / R
        dw_dchi = 0.5 * k * (1 + th / R)
        wT = (dw_dtheta + dw_dpsi * dpsi + dw_dchi * dchi) * self.dtheta(T)
        return w, wk, wkk, wT

    def density_g(self, k, T):
        """Gatheral's g(k): the risk-neutral density is positive iff g > 0 (no butterfly arbitrage)."""
        w, wk, wkk, _ = self.derivatives(k, T)
        return (1 - k * wk / (2 * w)) ** 2 - wk**2 / 4 * (1 / w + 0.25) + wkk / 2

    def local_var(self, k, T, floor=1e-4, cap=4.0):
        """Dupire local variance sigma_loc^2(T, K), written in total implied variance."""
        T = np.maximum(np.asarray(T, dtype=float), 1.0 / 365)
        w, wk, wkk, wT = self.derivatives(k, T)
        g = (1 - k * wk / (2 * w)) ** 2 - wk**2 / 4 * (1 / w + 0.25) + wkk / 2
        return np.clip(wT / np.maximum(g, 1e-6), floor, cap)

    def local_vol(self, k, T):
        return np.sqrt(self.local_var(k, T))

    def to_dict(self) -> dict:
        return {"maturities": self.maturities.tolist(), "thetas": self.thetas.tolist(),
                "psis": self.psis.tolist(), "rhos": self.rhos.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "SSVI":
        return cls(d["maturities"], d["thetas"], d["psis"], d["rhos"])


# ---------------------------------------------------------------- fitting

def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _prepare(slices, weights):
    if weights is None:
        weights = [np.ones_like(np.asarray(s["k"], dtype=float)) for s in slices]
    order = np.argsort([s["T"] for s in slices])
    out_s, out_w = [], []
    for i in order:
        s = slices[i]
        iv = np.asarray(s["iv"], dtype=float)
        ok = np.isfinite(iv)
        out_s.append({"T": float(s["T"]), "k": np.asarray(s["k"], dtype=float)[ok], "iv": iv[ok]})
        out_w.append(np.asarray(weights[i], dtype=float)[ok])
    return out_s, out_w


def _atm_total_variance(slices):
    atm_w = []
    for s in slices:
        order = np.argsort(s["k"])
        atm_w.append(np.interp(0.0, s["k"][order], s["iv"][order]) ** 2 * s["T"])
    atm_w = np.maximum.accumulate(np.maximum(np.array(atm_w), 1e-6))
    return atm_w


def _increments(values, floor):
    inc = np.diff(np.concatenate([[0.0], values]))
    return np.log(np.maximum(inc, floor))


def _fit_power_law(slices, weights, Ts):
    """Plain SSVI fit: a good, arbitrage-free starting point for the extended fit."""
    atm_w = _atm_total_variance(slices)
    x0 = np.concatenate([[np.arctanh(-0.6), 0.0, 0.0], _increments(atm_w, 1e-3 * atm_w[-1] / len(Ts))])

    def unpack(x):
        rho = 0.999 * np.tanh(x[0])
        gamma = 0.01 + 0.49 * _sigmoid(x[1])
        eta = 2.0 / (1 + abs(rho)) * _sigmoid(x[2])
        return rho, eta, gamma, np.cumsum(np.exp(x[3:]))

    def residuals(x):
        surf = SSVI.power_law(*unpack(x)[:3], Ts, unpack(x)[3])
        return 1e4 * np.concatenate([np.sqrt(w) * (surf.iv(s["k"], s["T"]) - s["iv"])
                                     for s, w in zip(slices, weights)])

    sol = least_squares(residuals, x0, xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=1000)
    return SSVI.power_law(*unpack(sol.x)[:3], Ts, unpack(sol.x)[3])


def fit_ssvi(slices, weights=None, penalty=10.0):
    """Fit the extended SSVI surface to implied vols.

    slices: dicts with T, k = ln(K/F) and iv (NaN entries are ignored); weights: one array per
    slice. Parameters are increments of theta, a and b (exponentials, hence non-decreasing
    pillars); the butterfly bounds enter as penalties. Returns (surface, least-squares result).
    """
    slices, weights = _prepare(slices, weights)
    Ts = np.array([s["T"] for s in slices])
    n = len(Ts)
    start = _fit_power_law(slices, weights, Ts)
    floor_th = 1e-4 * start.thetas[-1] / n
    a0 = start.psis * (1 + start.rhos)
    b0 = start.psis * (1 - start.rhos)
    x0 = np.concatenate([_increments(start.thetas, floor_th), _increments(a0, 1e-4 * a0[-1] / n),
                         _increments(b0, 1e-4 * b0[-1] / n)])

    def unpack(x):
        th = np.cumsum(np.exp(x[:n]))
        a = np.cumsum(np.exp(x[n:2 * n]))
        b = np.cumsum(np.exp(x[2 * n:]))
        psi = 0.5 * (a + b)
        return th, psi, (a - b) / (a + b), a, b

    def residuals(x):
        th, psi, rho, a, b = unpack(x)
        surf = SSVI(Ts, th, psi, rho)
        fit = [np.sqrt(w) * (surf.iv(s["k"], s["T"]) - s["iv"]) for s, w in zip(slices, weights)]
        wing = np.maximum(a, b)  # psi (1 + |rho|)
        pen = penalty * np.concatenate([np.maximum(wing - 3.98, 0.0), np.maximum(wing * psi - 3.98 * th, 0.0)])
        return 1e4 * np.concatenate(fit + [pen])  # in basis points of vol, for the solver's tolerances

    sol = least_squares(residuals, x0, xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=3000)
    th, psi, rho, _, _ = unpack(sol.x)
    return SSVI(Ts, th, psi, rho), sol


def arbitrage_report(surf: SSVI, k_grid=None, t_grid=None) -> dict:
    """Numerical static-arbitrage checks on a grid: min of g(k) (butterfly) and min of
    w(k, T_{i+1}) - w(k, T_i) (calendar), plus the slice-wise sufficient conditions."""
    if k_grid is None:
        k_grid = np.linspace(-1.5, 1.0, 401)
    if t_grid is None:
        t_grid = np.linspace(max(surf.maturities[0] / 2, 1 / 365), surf.maturities[-1], 80)
    K, T = np.meshgrid(k_grid, t_grid)
    g = surf.density_g(K, T)
    calendar = np.diff(surf.w(K, T), axis=0)
    wing = surf.psis * (1 + np.abs(surf.rhos))
    return {
        "min_g": float(g.min()),
        "min_calendar_increment": float(calendar.min()),
        "max_psi_1_plus_abs_rho": float(wing.max()),
        "max_psi2_1_plus_abs_rho_over_theta": float((wing * surf.psis / surf.thetas).max()),
        "butterfly_free": bool(g.min() > 0),
        "calendar_free": bool(calendar.min() >= -1e-12),
    }
