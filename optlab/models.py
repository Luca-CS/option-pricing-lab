"""Path simulation for Black-Scholes, Heston, Dupire local volatility and stochastic-local volatility.

All models are simulated in x = ln(S_t / F(t)). With the instantaneous variance a(t, x, v)
known at the start of each step, the log-Euler step

    x <- x - a dt / 2 + sqrt(a dt) Z

keeps exp(x) an exact martingale, so forwards are matched by construction and the only
discretisation error is in the path law. Variance follows a full-truncation Euler scheme.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .heston import HestonParams
from .market import Market
from .ssvi import SSVI


class Model:
    has_variance = False

    def inst_var(self, t, x, v):
        raise NotImplementedError


@dataclass
class BlackScholesModel(Model):
    vol: float

    def inst_var(self, t, x, v):
        return np.full_like(x, self.vol**2)


@dataclass
class HestonModel(Model):
    params: HestonParams
    has_variance = True

    @property
    def heston(self) -> HestonParams:
        return self.params

    def inst_var(self, t, x, v):
        return np.maximum(v, 0.0)


@dataclass
class LocalVolModel(Model):
    surface: SSVI

    def inst_var(self, t, x, v):
        return self.surface.local_var(x, max(t, 1.0 / 365))


class Leverage:
    """L(t, x) on a time grid; piecewise constant in t, linear in x (flat extrapolation)."""

    def __init__(self, times, x_grids, values):
        self.times = np.asarray(times, dtype=float)
        self.x_grids = np.asarray(x_grids, dtype=float)
        self.values = np.asarray(values, dtype=float)

    def __call__(self, t, x):
        i = int(np.clip(np.searchsorted(self.times, t + 1e-12, side="right") - 1, 0, len(self.times) - 1))
        return np.interp(x, self.x_grids[i], self.values[i])

    def to_dict(self) -> dict:
        return {"times": self.times.tolist(), "x_grids": self.x_grids.tolist(), "values": self.values.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "Leverage":
        return cls(d["times"], d["x_grids"], d["values"])


@dataclass
class SLVModel(Model):
    """dS/S = ... + L(t, S) sqrt(v) dW, with the Heston variance whose vol-of-vol is scaled by `mix`. With `spot_driven`, the spot is driven
    by the same normals as a model without variance on that seed, and the rest of the variance's driver comes from a stream of its own:
    same law, and a local vol price on the same seed is on common random numbers with this one."""

    params: HestonParams
    leverage: Leverage
    mix: float = 1.0
    spot_driven: bool = False
    has_variance = True

    @property
    def heston(self) -> HestonParams:
        p = self.params
        return HestonParams(p.v0, p.kappa, p.theta, self.mix * p.xi, p.rho)

    def inst_var(self, t, x, v):
        return self.leverage(t, x) ** 2 * np.maximum(v, 0.0)


def time_grid(times, dt_max, dt_min=None):
    """Fine grid containing every observation time, with steps no longer than dt_max.

    With dt_min, the mesh is graded: the step is dt_min + t / 60, capped at dt_max. Steep
    short-dated skews make the first weeks the main source of discretisation error, and an Euler
    step at time t costs roughly h / t in accuracy.
    """
    times = np.asarray(times, dtype=float)
    grid = [0.0]
    for t in times:
        if dt_min is not None:
            while t - grid[-1] > 1e-12:
                h = min(dt_max, dt_min + grid[-1] / 60)
                remaining = t - grid[-1]
                grid.append(t if remaining < 1.5 * h else grid[-1] + h)
            continue
        n = max(1, int(np.ceil((t - grid[-1]) / dt_max - 1e-9)))
        grid.extend(np.linspace(grid[-1], t, n + 1)[1:])
    return np.array(grid)


def simulate(
    model: Model,
    market: Market,
    times,
    n_paths: int,
    rng=None,
    dt_max=1 / 252,
    antithetic=True,
    return_variance=False,
    dt_min=1 / 2000,
    tile: int | None = 25_000,
):
    """Simulate S at the observation times. Returns an array (n_paths, len(times)),
    plus the variance at the same dates if return_variance is True.

    Each step is applied tile by tile (`tile` paths at a time, the draws unchanged and in the same order), which
    keeps the temporaries in cache: the result is the same bit for bit as without tiles (`tile=None`)."""
    rng = np.random.default_rng(rng)
    times = np.asarray(times, dtype=float)
    if isinstance(model, BlackScholesModel):
        dt_max, dt_min = np.inf, None  # constant volatility: the log-Euler step is exact
    grid = time_grid(times, dt_max, dt_min)
    obs = np.searchsorted(grid, times)
    half = n_paths // 2 if antithetic else n_paths
    n = 2 * half if antithetic else n_paths

    x = np.zeros(n)
    hp = getattr(model, "heston", None) if model.has_variance else None
    v = np.full(n, hp.v0 if hp is not None else 0.0)
    out_x = np.empty((n, len(times)))
    out_v = np.empty((n, len(times))) if return_variance else None
    width = half if tile is None or tile >= half else int(tile)
    # (first path, last path, offset into the draws, sign): the mirrored half reuses the draws with a minus sign
    segments = [(a, min(a + width, half), a, 1.0) for a in range(0, half, width)]
    if antithetic:
        segments += [(half + a, half + b, o, -1.0) for a, b, o, _ in segments]
    rho_c = np.sqrt(1 - hp.rho**2) if hp is not None else 0.0
    spot_driven = hp is not None and getattr(model, "spot_driven", False)
    residual = rng.spawn(1)[0] if spot_driven else rng  # spawning leaves the stream of the spot's normals untouched
    j = 0
    for i in range(1, len(grid)):
        t0, dt = grid[i - 1], grid[i] - grid[i - 1]
        z1 = rng.standard_normal(half)
        z2 = None
        for k, (a, b, o, sign) in enumerate(segments):
            s = slice(a, b)
            zz1 = z1[o : o + (b - a)] if sign > 0 else -z1[o : o + (b - a)]
            av = model.inst_var(t0, x[s], v[s])
            if hp is not None:
                if z2 is None:
                    z2 = residual.standard_normal(half)  # drawn after the variance of the first tile, as before
                zz2 = z2[o : o + (b - a)] if sign > 0 else -z2[o : o + (b - a)]
                if spot_driven:
                    zx, zv = zz1, hp.rho * zz1 + rho_c * zz2
                else:
                    zx, zv = hp.rho * zz2 + rho_c * zz1, zz2
                vp = np.maximum(v[s], 0.0)
                v[s] = v[s] + hp.kappa * (hp.theta - vp) * dt + hp.xi * np.sqrt(vp * dt) * zv
            else:
                zx = zz1
            x[s] = x[s] - 0.5 * av * dt + np.sqrt(av * dt) * zx
        while j < len(obs) and obs[j] == i:
            out_x[:, j] = x
            if return_variance:
                out_v[:, j] = np.maximum(v, 0.0)
            j += 1
    paths = market.F(times)[None, :] * np.exp(out_x)
    if return_variance:
        return paths, out_v
    return paths
