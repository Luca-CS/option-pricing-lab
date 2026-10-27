"""Leverage function of the Heston stochastic-local volatility model, calibrated by the particle
method of Guyon and Henry-Labordere (2012).

The SLV model reproduces the Dupire local volatility if and only if

    L(t, x)^2 E[v_t | x_t = x] = sigma_loc^2(t, x).

The particle method simulates the SLV dynamics forward and, at every step, estimates the
conditional expectation by kernel regression on the particles themselves, then sets L from it.
Particles are binned on a fine grid first, so a step costs O(N + bins * grid) instead of O(N * grid).
"""

from __future__ import annotations

import numpy as np

from .heston import HestonParams
from .market import Market
from .models import Leverage, SLVModel, time_grid
from .ssvi import SSVI


def _conditional_mean(x, v, x_eval, bandwidth, n_bins=400):
    lo, hi = x.min(), x.max()
    edges = np.linspace(lo, hi + 1e-12, n_bins + 1)
    centres = 0.5 * (edges[1:] + edges[:-1])
    idx = np.clip(((x - lo) / (edges[1] - edges[0])).astype(int), 0, n_bins - 1)
    counts = np.bincount(idx, minlength=n_bins).astype(float)
    sums = np.bincount(idx, weights=v, minlength=n_bins)
    kern = np.exp(-0.5 * ((x_eval[:, None] - centres[None, :]) / bandwidth) ** 2)
    num, den = kern @ sums, kern @ counts
    fallback = v.mean()
    return np.where(den > 1e-8, num / np.maximum(den, 1e-300), fallback)


def calibrate_leverage(params: HestonParams, surface: SSVI, market: Market, T_max: float,
                       mix: float = 1.0, n_particles: int = 100_000, dt_max=1 / 100, dt_min=1 / 2000, n_x=81,
                       bandwidth_scale=1.0, rng=None, l_bounds=(0.05, 8.0)) -> SLVModel:
    """Return the SLV model whose leverage reproduces the local volatility of `surface` up to T_max."""
    rng = np.random.default_rng(rng)
    grid = time_grid([T_max], dt_max, dt_min)
    p = HestonParams(params.v0, params.kappa, params.theta, mix * params.xi, params.rho)
    half = n_particles // 2
    x = np.zeros(2 * half)
    v = np.full(2 * half, p.v0)
    atm_vol = float(np.sqrt(surface.theta(min(1.0, surface.maturities[-1])) / min(1.0, surface.maturities[-1])))
    x_grids, values = [], []

    for i in range(len(grid) - 1):
        t0, h = grid[i], grid[i + 1] - grid[i]
        if i == 0:
            x_eval = np.linspace(-6, 6, n_x) * atm_vol * np.sqrt(max(h, 1e-4))
            cond_v = np.full(n_x, p.v0)
        else:
            q_lo, q_hi = np.quantile(x, [0.0005, 0.9995])
            x_eval = np.linspace(q_lo, q_hi, n_x)
            bw = bandwidth_scale * atm_vol * np.sqrt(max(t0, 0.15)) * len(x) ** -0.2
            cond_v = _conditional_mean(x, v, x_eval, bw)
        lev = np.sqrt(surface.local_var(x_eval, max(t0, 1 / 365)) / np.maximum(cond_v, 1e-8))
        lev = np.clip(lev, *l_bounds)
        x_grids.append(x_eval)
        values.append(lev)

        z1 = rng.standard_normal(half)
        z2 = rng.standard_normal(half)
        z1, z2 = np.concatenate([z1, -z1]), np.concatenate([z2, -z2])
        a = np.interp(x, x_eval, lev) ** 2 * np.maximum(v, 0.0)
        zx = p.rho * z2 + np.sqrt(1 - p.rho**2) * z1
        vp = np.maximum(v, 0.0)
        v = v + p.kappa * (p.theta - vp) * h + p.xi * np.sqrt(vp * h) * z2
        x = x - 0.5 * a * h + np.sqrt(a * h) * zx

    leverage = Leverage(grid[:-1], np.array(x_grids), np.array(values))
    return SLVModel(params, leverage, mix)
