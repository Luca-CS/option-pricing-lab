"""Local vol under stochastic rates: the equity's local variance recalibrated beside a Hull-White rate, so that every
vanilla of the surface is still repriced.

With C(T, K) = E[D_T (S_T - K)+], Ito-Tanaka gives sigma_SR^2(T, K) = sigma_Dup^2(T, K) - c(T, K), where

    c(T, k) = 2 E^T[(r_T - f(0,T)) 1{k_T > k}] / p^T_k(k),

k = ln(K / F(0,T)), E^T the T-forward measure (Q weighted by D_T / P(0,T)) and p^T_k the density of k_T under it. The
numerator is an unconditional tail expectation: no regression of the rate is needed. The particle method estimates it
step by step, as `slv.calibrate_leverage` estimates the leverage: particles (X, x, Y) take the hybrid's own step under the
variance found so far, and at each step the weighted tail sum and density, binned and smoothed by a Gaussian kernel,
give the row of sigma_SR^2 on a grid of k. Under Black-Scholes and Hull-White the correction is Gaussian and closed
(`gaussian_correction`), which serves as its check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.special import ndtr

from optlab.core.market import Market
from optlab.core.models import LocalVarSurface, time_grid
from optlab.core.rates import B, HullWhite1F

SQRT_2PI = np.sqrt(2 * np.pi)


@dataclass
class RatesLocalVar:
    """sigma_SR^2(t, k) on a time grid: piecewise constant in t, linear in k with flat extrapolation (the shape of
    `Leverage`); a `LocalVarSurface`, so a plain `LocalVolModel` over it is the hybrid's equity."""

    times: np.ndarray
    k_grids: list
    values: list
    floored: float = 0.0  # share of the nodes held at the floor
    diagnostics: dict = field(default_factory=dict)

    def local_var(self, k, T, /):
        T = float(np.max(T)) if np.ndim(T) else float(T)
        i = max(int(np.searchsorted(self.times, T, side="right")) - 1, 0)
        return np.interp(k, self.k_grids[i], self.values[i])


def gaussian_correction(hw: HullWhite1F, rho: float, sigma_s, T: float, n: int = 200) -> float:
    """c_G(T) = 2 [rho sigma_r int_0^T e^{-a(T-u)} sigma_S(u) du + sigma_r^2 int_0^T B_a(T-u) e^{-a(T-u)} du], the
    correction when (r_T, k_T) is jointly Gaussian (exact under Black-Scholes and Hull-White); sigma_s(u) the equity's
    volatility (a number, or a function of time)."""
    u = np.linspace(0.0, T, n + 1)
    s = np.asarray(sigma_s(u) if callable(sigma_s) else np.full_like(u, sigma_s), dtype=float)
    decay = np.exp(-hw.a * (T - u))
    first = np.trapezoid(decay * s, u)
    second = np.trapezoid(np.asarray(B(hw.a, T - u)) * decay, u)
    return float(2 * (rho * hw.sigma * first + hw.sigma**2 * second))


def calibrate_local_var_under_rates(
    surface: LocalVarSurface,
    market: Market,
    hw: HullWhite1F,
    rho: float,
    T_max: float,
    n_particles: int = 100_000,
    dt_max: float = 1 / 100,
    dt_min: float = 1 / 2000,
    n_x: int = 81,
    rng=2026,
    n_bins: int = 400,
    bandwidth_scale: float = 0.9,
    floor: float = 0.1,
    cap: float = 4.0,
) -> RatesLocalVar:
    """The particle method: `n_particles` antithetic particles on `time_grid([T_max], dt_max, dt_min)`, a seed of
    its own. Each row is sigma_Dup^2 - c, held between `floor` sigma_Dup^2 and `cap`."""
    rng = np.random.default_rng(rng)
    grid = time_grid([float(T_max)], dt_max, dt_min)
    half = n_particles // 2
    n = 2 * half
    X, x, Y = np.zeros(n), np.zeros(n), np.zeros(n)
    atm = float(np.sqrt(max(float(np.asarray(surface.local_var(np.zeros(1), 1.0 / 365))[0]), 1e-6)))
    times, k_grids, values = [], [], []
    at_floor = nodes = 0
    for i in range(len(grid) - 1):
        t0, h = grid[i], grid[i + 1] - grid[i]
        V = float(hw.var_integral(t0))
        k = X + Y + 0.5 * V
        T_read = max(t0, 1.0 / 365)
        if i == 0:
            k_eval = np.linspace(-6, 6, n_x) * atm * np.sqrt(h)
            row = np.asarray(surface.local_var(k_eval, T_read), dtype=float)
        else:
            lo, hi = np.quantile(k, [0.0005, 0.9995])
            k_eval = np.linspace(float(lo), float(hi), n_x)
            dup = np.asarray(surface.local_var(k_eval, T_read), dtype=float)
            c = _correction(k, x, Y, k_eval, n_bins, bandwidth_scale)
            row = np.clip(dup - c, floor * dup, cap)
            at_floor += int(np.sum(dup - c <= floor * dup))
            nodes += n_x
        times.append(t0)
        k_grids.append(k_eval)
        values.append(row)
        z1 = rng.standard_normal(half)
        z23 = rng.standard_normal((2, half))
        z1, z23 = np.concatenate([z1, -z1]), np.concatenate([z23, -z23], axis=1)
        L = hw.step_factor(h, rho)
        av = np.interp(k, k_eval, row)
        X = X - 0.5 * av * h + np.sqrt(av) * L[0, 0] * z1
        Y = Y + float(B(hw.a, h)) * x + L[2, 0] * z1 + L[2, 1] * z23[0] + L[2, 2] * z23[1]
        x = np.exp(-hw.a * h) * x + L[1, 0] * z1 + L[1, 1] * z23[0]
    return RatesLocalVar(
        np.array(times), k_grids, values, at_floor / nodes if nodes else 0.0, {"particles": n, "steps": len(times)}
    )


def _correction(k, x, Y, k_eval, n_bins: int, bandwidth_scale: float) -> np.ndarray:
    """2 E^t[(r - f) 1{k_t > k}] / p^t(k) on k_eval: weights exp(-Y) (the t-forward measure, P(0,t) and e^{-V/2}
    cancel), the rate centred on its weighted mean, binned and smoothed by a Gaussian kernel (tail and density from the
    same kernel)."""
    w = np.exp(-(Y - Y.mean()))
    w = w / w.sum()
    rate = x - np.dot(w, x)
    lo, hi = k.min(), k.max()
    edges = np.linspace(lo, hi + 1e-12, n_bins + 1)
    centres = 0.5 * (edges[1:] + edges[:-1])
    idx = np.clip(((k - lo) / (edges[1] - edges[0])).astype(int), 0, n_bins - 1)
    mass = np.bincount(idx, weights=w, minlength=n_bins)
    moment = np.bincount(idx, weights=w * rate, minlength=n_bins)
    mean_k = np.dot(w, k)
    sd = np.sqrt(max(np.dot(w, (k - mean_k) ** 2), 1e-16))
    bw = bandwidth_scale * sd * len(k) ** (-0.2)
    u = (centres[None, :] - k_eval[:, None]) / bw
    tail = ndtr(u) @ moment
    dens = (np.exp(-0.5 * u * u) / SQRT_2PI) @ mass / bw
    return 2 * tail / np.maximum(dens, 1e-12)
