"""Several underlyings: correlated simulation, basket scenarios and a basket pricing context.

Each name keeps its own model (Black-Scholes, Dupire local vol, Heston or SLV) and its own forward curve, and is
simulated in its log-forward coordinate exactly like `models.simulate`. The spot drivers are correlated by a
matrix square root of the correlation; a name with a variance process draws it with its own spot-vol
correlation, independently across names. Discounting uses the curve of the first name (one currency).
With one name and Black-Scholes, the draws are those of `simulate`, so prices match it bit for bit.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from ..market import Market
from ..models import BlackScholesModel, LocalVolModel, Model, time_grid
from .scenario import batch_sizes


def correlation_root(corr) -> np.ndarray:
    """L with L L^T = corr, by eigen-decomposition (works for singular matrices, e.g. correlation 1)."""
    c = np.atleast_2d(np.asarray(corr, dtype=float))
    if c.shape[0] != c.shape[1] or not np.allclose(c, c.T, atol=1e-12):
        raise ValueError("the correlation matrix must be square and symmetric")
    if not np.allclose(np.diag(c), 1.0, atol=1e-12):
        raise ValueError("the correlation matrix must have ones on its diagonal")
    lam, vec = np.linalg.eigh(c)
    if lam.min() < -1e-10:
        raise ValueError(f"the correlation matrix is not positive semi-definite (eigenvalue {lam.min():.2e})")
    if c.shape[0] == 1:
        return np.ones((1, 1))
    return vec * np.sqrt(np.maximum(lam, 0.0))


def moved_correlation(corr, shift: float) -> np.ndarray:
    """Every pair moved by `shift`, kept within (-0.99, 0.99), and repaired into a correlation matrix when the move breaks it."""
    from ..marketdata.derive.correl import nearest_correlation
    c = np.array(corr, dtype=float)
    moved = np.clip(c + shift, -0.99, 0.99)
    np.fill_diagonal(moved, 1.0)
    return nearest_correlation(moved)


@dataclass
class Basket:
    names: tuple
    models: dict  # name → Model
    markets: dict  # name → Market (spot and forward curve)
    corr: np.ndarray

    def __post_init__(self):
        self.names = tuple(self.names)
        missing = [n for n in self.names if n not in self.models or n not in self.markets]
        if missing:
            raise ValueError(f"no model or market for {', '.join(missing)}")
        self.corr = np.atleast_2d(np.asarray(self.corr, dtype=float))
        if self.corr.shape != (len(self.names),) * 2:
            raise ValueError(f"the correlation matrix must be {len(self.names)}x{len(self.names)}")
        self.root = correlation_root(self.corr)

    @property
    def discount_market(self) -> Market:
        return self.markets[self.names[0]]


def simulate_basket(basket: Basket, times, n_paths: int, rng=None, dt_max=1 / 252, antithetic=True,
                    dt_min=1 / 2000) -> dict:
    """Spots of every name at the observation times: {name: (n_paths, len(times))}."""
    rng = np.random.default_rng(rng)
    times = np.asarray(times, dtype=float)
    models = [basket.models[n] for n in basket.names]
    if all(isinstance(m, BlackScholesModel) for m in models):
        dt_max, dt_min = np.inf, None
    grid = time_grid(times, dt_max, dt_min)
    obs = np.searchsorted(grid, times)
    half = n_paths // 2 if antithetic else n_paths
    n = 2 * half if antithetic else n_paths
    k = len(models)
    x = np.zeros((k, n))
    hps = [getattr(m, "heston", None) if m.has_variance else None for m in models]
    v = [np.full(n, hp.v0) if hp is not None else None for hp in hps]
    out = np.empty((k, n, len(times)))

    def normals(shape):
        z = rng.standard_normal(shape)
        return np.concatenate([z, -z], axis=-1) if antithetic else z

    j = 0
    for i in range(1, len(grid)):
        t0, dt = grid[i - 1], grid[i] - grid[i - 1]
        z = basket.root @ normals((k, half))
        for a, (m, hp) in enumerate(zip(models, hps)):
            var = m.inst_var(t0, x[a], v[a] if v[a] is not None else np.zeros(n))
            if hp is not None:
                z2 = hp.rho * z[a] + np.sqrt(1 - hp.rho**2) * normals(half)
                vp = np.maximum(v[a], 0.0)
                v[a] = v[a] + hp.kappa * (hp.theta - vp) * dt + hp.xi * np.sqrt(vp * dt) * z2
            x[a] = x[a] - 0.5 * var * dt + np.sqrt(var * dt) * z[a]
        while j < len(obs) and obs[j] == i:
            out[:, :, j] = x
            j += 1
    return {name: basket.markets[name].F(times)[None, :] * np.exp(out[a]) for a, name in enumerate(basket.names)}


class BasketScenario:
    """Scenario interface for several names (see scenario.Scenario)."""

    def __init__(self, times, paths: dict, spot0: dict, history: dict | None = None):
        self.times = np.asarray(times, dtype=float)
        self.paths = paths
        self.spot0 = {k: float(v) for k, v in spot0.items()}
        self.history = {name: {round(float(t), 12): float(x) for t, x in h.items()}
                        for name, h in (history or {}).items()}
        self.n_paths = next(iter(paths.values())).shape[0]
        self._col = {float(t): j for j, t in enumerate(self.times)}

    def _past(self, name, t):
        h = self.history.get(name, {})
        key = round(float(t), 12)
        if key in h:
            return h[key]
        if t == 0:
            return self.spot0[name]
        raise KeyError(f"no fixing of {name} at t = {t:.6f} (before the valuation date): give it in fixings")

    def _check(self, name):
        if name not in self.paths:
            raise KeyError(f"no paths for underlying {name!r} (this basket simulates {', '.join(self.paths)})")

    def spot(self, name: str, t: float):
        self._check(name)
        if t <= 0:
            return self._past(name, t)
        return self.paths[name][:, self._col[float(t)]]

    def extreme(self, name: str, times, func: str):
        """Lowest ("min") or highest ("max") fixing over `times` on each path, without copying the window: the
        simulated dates of a window are contiguous columns. Same result as fixings(...).min(axis=1)."""
        self._check(name)
        times = np.asarray(times, dtype=float)
        future = times[times > 0]
        reduce = np.min if func == "min" else np.max
        out = None
        if len(future):
            cols = [self._col[float(t)] for t in future]
            lo, hi = cols[0], cols[-1] + 1
            block = self.paths[name]
            contiguous = hi - lo == len(cols) and cols == list(range(lo, hi))
            out = reduce(block[:, lo:hi] if contiguous else block[:, cols], axis=1)
        past = [self._past(name, t) for t in times if t <= 0]
        if past:
            p = reduce(np.array(past))
            out = np.full(self.n_paths, p) if out is None else (np.minimum if func == "min" else np.maximum)(out, p)
        return out

    def fixings(self, name: str, times) -> np.ndarray:
        self._check(name)
        times = np.asarray(times, dtype=float)
        out = self.paths[name][:, [self._col[float(t)] for t in times if t > 0]]
        past = [self._past(name, t) for t in times if t <= 0]
        if past:
            out = np.hstack([np.tile(np.array(past), (self.n_paths, 1)), out])
        return out


def basket_scenarios(basket: Basket, times, n_paths: int, seed=None, batch: int | None = None,
                     history: dict | None = None, initial_fixings: dict | None = None,
                     **sim_kwargs) -> Iterator[BasketScenario]:
    """Batches of correlated paths, with the seed convention of `scenario.simulated_scenarios`.
    `initial_fixings` replaces the spots read on the valuation date (struck products on bumped markets)."""
    sizes = batch_sizes(n_paths, batch)
    if len(sizes) == 1:
        rngs = [np.random.default_rng(seed)]
    else:
        rngs = [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(len(sizes))]
    times = np.asarray(times, dtype=float)
    spot0 = {n: (initial_fixings or {}).get(n, basket.markets[n].spot) for n in basket.names}
    for size, rng in zip(sizes, rngs):
        paths = simulate_basket(basket, times, size, rng, **sim_kwargs) if len(times) else \
            {n: np.empty((size, 0)) for n in basket.names}
        yield BasketScenario(times, paths, spot0, history)


@dataclass
class BasketContext:
    """Markets, volatilities (a surface, or a flat vol) and correlation of several names, for worst-of products.
    Only SPX is calibrated in this repository: other names are given by the user (flat vol and carry)."""
    names: tuple
    markets: dict
    vols: dict  # name → SVISurface or float (flat Black-Scholes vol)
    corr: np.ndarray
    valuation: dt.date

    def __post_init__(self):
        self.names = tuple(self.names)
        correlation_root(self.corr)

    @classmethod
    def with_spx(cls, others: dict, corr, spx=None) -> "BasketContext":
        """SPX from the calibration plus other names given as {name: (spot, r, q, vol)} on flat markets."""
        from .context import PricingContext
        spx = spx or PricingContext.from_calibration()
        names = ("SPX", *others)
        markets = {"SPX": spx.market, **{n: Market.flat(s, r, q) for n, (s, r, q, _) in others.items()}}
        vols = {"SPX": spx.surface, **{n: v for n, (_, _, _, v) in others.items()}}
        return cls(names, markets, vols, np.asarray(corr, dtype=float), spx.valuation)

    @property
    def market(self) -> Market:
        return self.markets[self.names[0]]

    def bumped(self, name: str, spot_factor: float = 1.0, vol_shift: float = 0.0) -> "BasketContext":
        """The same context with one name's spot scaled (sticky moneyness) or its implied vols shifted."""
        from .greeks import ShiftedSurface
        markets, vols = dict(self.markets), dict(self.vols)
        if spot_factor != 1.0:
            markets[name] = markets[name].scaled(markets[name].spot * spot_factor)
        if vol_shift:
            v = vols[name]
            vols[name] = v + vol_shift if np.isscalar(v) else ShiftedSurface(v, vol_shift)
        return BasketContext(self.names, markets, vols, self.corr, self.valuation)

    def with_bumps(self, vol: float = 0.0, skew: float = 0.0, corr: float = 0.0) -> "BasketContext":
        """The same context with every name's implied vols moved, and every pair's correlation, the matrix repaired if needed."""
        from .surfaces import BumpedSurface
        vols = {n: (v + vol if np.isscalar(v) else BumpedSurface(v, vol, skew)) if vol or skew else v for n, v in self.vols.items()}
        return BasketContext(self.names, self.markets, vols, moved_correlation(self.corr, corr) if corr else self.corr, self.valuation)

    @property
    def t_max(self) -> float:
        surfaces = [v for v in self.vols.values() if not np.isscalar(v)]
        return min((float(s.maturities[-1]) for s in surfaces), default=np.inf)

    @property
    def has_surfaces(self) -> bool:
        return all(not np.isscalar(self.vols[n]) for n in self.names)

    def atm_vol(self, name: str, T: float) -> float:
        v = self.vols[name]
        return float(v) if np.isscalar(v) else float(v.iv(0.0, T))

    def model(self, name: str, T: float = 1.0, names=None) -> Basket:
        """'bs' (ATM vol at T for every name) or 'lv' (Dupire on each surface; flat names stay Black-Scholes)."""
        names = tuple(names or self.names)
        models: dict[str, Model] = {}
        for n in names:
            vol = self.vols[n]
            if name == "lv" and not np.isscalar(vol):
                models[n] = LocalVolModel(vol)
            elif name in ("bs", "lv"):
                models[n] = BlackScholesModel(self.atm_vol(n, T))
            else:
                raise ValueError(f"basket model {name!r} is not available, expected bs or lv")
        idx = [self.names.index(n) for n in names]
        return Basket(names, models, {n: self.markets[n] for n in names}, np.asarray(self.corr)[np.ix_(idx, idx)])
