"""Opt-in faster models: interpolation on uniform grids instead of binary search (`np.interp`) per path.

`UniformLeverage` resamples each time row of the SLV leverage on a uniform grid of its own bounds and interpolates
by direct indexing; `TabulatedLocalVol` tabulates the Dupire variance of the surface once per time step on a
uniform log-moneyness grid. Both change the numbers (by the interpolation error, about 1e-4 relative on the local
variance and 1e-3 on the leverage), so they are validated within the Monte Carlo error, not bit for bit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models import Model, SLVModel


def _uniform_interp(x, x0: float, inv_dx: float, y: np.ndarray):
    u = (np.asarray(x, dtype=float) - x0) * inv_dx
    np.clip(u, 0.0, len(y) - 1 - 1e-9, out=u)
    i = u.astype(np.intp)
    w = u - i
    return y[i] * (1.0 - w) + y[i + 1] * w


class UniformLeverage:
    """Same interface as models.Leverage (piecewise constant in t, linear in x, flat outside)."""

    def __init__(self, leverage, n_x: int = 321):
        self.times = leverage.times
        self.rows = []
        for xs, vs in zip(leverage.x_grids, leverage.values):
            grid = np.linspace(xs[0], xs[-1], n_x)
            self.rows.append((float(grid[0]), (n_x - 1) / (grid[-1] - grid[0]), np.interp(grid, xs, vs)))

    def __call__(self, t, x):
        i = int(np.clip(np.searchsorted(self.times, t + 1e-12, side="right") - 1, 0, len(self.times) - 1))
        x0, inv_dx, y = self.rows[i]
        return _uniform_interp(x, x0, inv_dx, y)


def fast_slv(model: SLVModel, n_x: int = 321) -> SLVModel:
    """The same SLV model with its leverage on uniform grids."""
    return SLVModel(model.params, UniformLeverage(model.leverage, n_x), model.mix)


@dataclass
class TabulatedLocalVol(Model):
    """Dupire local variance tabulated per time step on x in [lo, hi] * atm_vol * sqrt(t) (flat beyond)."""
    surface: object
    n_x: int = 801
    sd_range: tuple = (-8.0, 6.0)
    _cache: dict = field(default_factory=dict, repr=False)

    def inst_var(self, t, x, v):
        key = round(float(t), 12)
        row = self._cache.get(key)
        if row is None:
            T = max(float(t), 1.0 / 365)
            sd = float(np.sqrt(self.surface.w(0.0, T)))
            lo, hi = self.sd_range[0] * sd, self.sd_range[1] * sd
            grid = np.linspace(lo, hi, self.n_x)
            row = (lo, (self.n_x - 1) / (hi - lo), self.surface.local_var(grid, T))
            self._cache[key] = row
        return _uniform_interp(x, *row)
