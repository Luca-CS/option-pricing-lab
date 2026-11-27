"""Stochastic rates beside the equity: a one-factor Hull-White short rate (G1++) fitted exactly to the market's
discount curve, simulated with the equity on the library's grid by an exact joint Gaussian step.

The rate is r_t = phi(t) + x_t, with dx = -a x dt + sigma dW^r and x_0 = 0, and Y_t = int_0^t x ds. The deflator is
D_t = exp(-int_0^t r) = P(0,t) exp(-Y_t - V(t)/2), V(t) = Var Y_t, so E[D_t] = P(0,t) holds by construction and every
zero-coupon bond is exact in law on any grid.

The equity is simulated in X = ln(S D / (F P)), F and P the market's deterministic forward and discount. D S is a
martingale under the bank account's measure, so X takes the log-Euler step of the local vol model with the same
variance, read at the log-moneyness k = ln(S/F) = X + Y + V/2: the prepaid forward D S = P F e^X holds path by path.

Each step draws (dW^S, eps_x, eps_Y), centred Gaussian, from the Cholesky factor of their covariance over the step,
spot first: z1 is the local vol model's own normal of that step (drawn as `models.simulate` draws it, so the base and
the hybrid share their spot normals), z2 and z3 come from a stream of their own (`rng.spawn`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from optlab.core.market import Market
from optlab.core.models import BlackScholesModel, Model, time_grid


def B(a: float, h):
    """(1 - e^{-a h}) / a, and h as a goes to 0."""
    h = np.asarray(h, dtype=float)
    if a == 0:
        return h
    small = np.abs(a * h) < 1e-6
    return np.where(small, h * (1 - a * h / 2 + (a * h) ** 2 / 6), -np.expm1(-a * h) / a)


@dataclass(frozen=True)
class HullWhite1F:
    """G1++: mean reversion a, volatility sigma (the short rate's, in rate units a year)."""

    a: float
    sigma: float

    def var_integral(self, h):
        """Var of int_t^{t+h} x ds given x_t: sigma^2 / a^2 [h - 2 B_a(h) + B_2a(h)] (its series when a h is small)."""
        a, s2 = self.a, self.sigma**2
        h = np.asarray(h, dtype=float)
        if a == 0:
            return s2 * h**3 / 3
        small = np.abs(a * h) < 1e-3
        series = s2 * h**3 / 3 * (1 - 3 * a * h / 4 + 7 * (a * h) ** 2 / 20)
        exact = s2 / a**2 * (h - 2 * B(a, h) + B(2 * a, h))
        return np.where(small, series, exact)

    def bond(self, market: Market, t, T, x):
        """P(t, T) given x_t: P(0,T)/P(0,t) exp((V(t,T) - V(0,T) + V(0,t))/2 - B(T - t) x_t)."""
        t, T = np.asarray(t, dtype=float), np.asarray(T, dtype=float)
        ratio = market.D(T) / market.D(t)
        adj = 0.5 * (self.var_integral(T - t) - self.var_integral(T) + self.var_integral(t))
        return ratio * np.exp(adj - B(self.a, T - t) * x)

    def deflator(self, market: Market, t, Y):
        """D_t = P(0,t) exp(-Y_t - V(t)/2)."""
        return market.D(t) * np.exp(-Y - 0.5 * self.var_integral(t))

    def zero_bond_option(self, market: Market, T: float, S: float, K: float, kind: str = "call") -> float:
        """Jamshidian: an option expiring at T on the zero-coupon bond paying 1 at S > T, strike K."""
        sp = self.sigma * float(B(self.a, S - T)) * np.sqrt(float(B(2 * self.a, T)))
        PT, PS = float(market.D(T)), float(market.D(S))
        h = np.log(PS / (PT * K)) / sp + sp / 2
        if kind == "call":
            return PS * norm.cdf(h) - K * PT * norm.cdf(h - sp)
        return K * PT * norm.cdf(-h + sp) - PS * norm.cdf(-h)

    def step_factor(self, h: float, rho: float) -> np.ndarray:
        """The lower Cholesky factor of Cov(dW^S, eps_x, eps_Y) over a step h, rho the equity-rate correlation."""
        a, s = self.a, self.sigma
        Ba, B2a = float(B(a, h)), float(B(2 * a, h))
        var_x = s**2 * B2a
        var_y = float(self.var_integral(h))
        cov_xy = s**2 * Ba**2 / 2
        cov_wx = rho * s * Ba
        if a == 0 or abs(a * h) < 1e-3:
            cov_wy = rho * s * h**2 / 2 * (1 - a * h / 3)
        else:
            cov_wy = rho * s * (h - Ba) / a
        L = np.zeros((3, 3))
        L[0, 0] = np.sqrt(h)
        L[1, 0] = cov_wx / L[0, 0]
        L[1, 1] = np.sqrt(max(var_x - L[1, 0] ** 2, 0.0))
        L[2, 0] = cov_wy / L[0, 0]
        L[2, 1] = (cov_xy - L[1, 0] * L[2, 0]) / L[1, 1] if L[1, 1] > 0 else 0.0
        L[2, 2] = np.sqrt(max(var_y - L[2, 0] ** 2 - L[2, 1] ** 2, 0.0))
        return L


@dataclass(frozen=True)
class HybridModel(Model):
    """An equity model without a variance process (Black-Scholes, local vol) beside a Hull-White short rate, rho the
    correlation of their Brownian motions. The equity's `inst_var` is read at the log-moneyness to the deterministic
    forward, k = ln(S/F)."""

    equity: Model
    rates: HullWhite1F
    rho: float = 0.0

    def inst_var(self, t, x, v):
        return self.equity.inst_var(t, x, v)


@dataclass
class RateState:
    """The rate factor at the simulated times: x and Y (paths x times) and the deflator D; bonds and the deflator of a
    payment made after an observation."""

    model: HullWhite1F
    market: Market
    times: np.ndarray
    x: np.ndarray
    Y: np.ndarray

    def __post_init__(self):
        self._col = {round(float(t), 12): j for j, t in enumerate(self.times)}
        self.D = self.model.deflator(self.market, self.times[None, :], self.Y)

    def _j(self, t: float) -> int:
        key = round(float(t), 12)
        if key not in self._col:
            raise KeyError(f"t = {t:.6f} is not a simulated time of the rates")
        return self._col[key]

    def deflator(self, t: float):
        return 1.0 if t <= 0 else self.D[:, self._j(t)]

    def bond(self, t: float, T: float):
        if t <= 0:
            return float(self.market.D(T))
        return self.model.bond(self.market, t, T, self.x[:, self._j(t)])

    def pay_deflator(self, t_obs: float, t_pay: float):
        """E[D_{t_pay} | F_{t_obs}] = D_{t_obs} P(t_obs, t_pay): an amount known at t_obs, paid at t_pay."""
        if t_pay <= t_obs:
            return self.deflator(t_pay) if t_pay > 0 else 1.0
        return self.deflator(t_obs) * self.bond(t_obs, t_pay)

    def scaled(self, rows) -> RateState:
        """The same state on a subset of the paths."""
        return RateState(self.model, self.market, self.times, self.x[rows], self.Y[rows])


def simulate_hybrid(
    model: HybridModel,
    market: Market,
    times,
    n_paths: int,
    rng=None,
    dt_max=1 / 252,
    antithetic=True,
    dt_min=1 / 2000,
) -> tuple[np.ndarray, RateState]:
    """Spots at the observation times (paths x times) and the rate state there. The grid and the spot normals are
    `models.simulate`'s: a Black-Scholes equity steps from date to date (its step is exact, as the rates' are)."""
    rng = np.random.default_rng(rng)
    rates_rng = rng.spawn(1)[0]  # spawning leaves the stream of the spot's normals untouched
    times = np.asarray(times, dtype=float)
    if isinstance(model.equity, BlackScholesModel):
        dt_max, dt_min = np.inf, None
    grid = time_grid(times, dt_max, dt_min)
    obs = np.searchsorted(grid, times)
    half = n_paths // 2 if antithetic else n_paths
    n = 2 * half if antithetic else n_paths
    hw = model.rates
    X, x, Y = np.zeros(n), np.zeros(n), np.zeros(n)
    out = [np.empty((n, len(times))) for _ in range(3)]
    j = 0
    for i in range(1, len(grid)):
        t0, h = grid[i - 1], grid[i] - grid[i - 1]
        z1 = rng.standard_normal(half)
        z23 = rates_rng.standard_normal((2, half))
        if antithetic:
            z1 = np.concatenate([z1, -z1])
            z23 = np.concatenate([z23, -z23], axis=1)
        L = hw.step_factor(h, model.rho)
        dW = L[0, 0] * z1
        eps_x = L[1, 0] * z1 + L[1, 1] * z23[0]
        eps_y = L[2, 0] * z1 + L[2, 1] * z23[0] + L[2, 2] * z23[1]
        k = X + Y + 0.5 * float(hw.var_integral(t0))
        av = model.equity.inst_var(t0, k, None)
        X = X - 0.5 * av * h + np.sqrt(av) * dW
        Y = Y + float(B(hw.a, h)) * x + eps_y
        x = np.exp(-hw.a * h) * x + eps_x
        while j < len(obs) and obs[j] == i:
            out[0][:, j], out[1][:, j], out[2][:, j] = X, x, Y
            j += 1
    Xo, xo, Yo = out
    V = hw.var_integral(times)[None, :]
    paths = market.F(times)[None, :] * np.exp(Xo + Yo + 0.5 * V)
    return paths, RateState(hw, market, times, xo, Yo)
