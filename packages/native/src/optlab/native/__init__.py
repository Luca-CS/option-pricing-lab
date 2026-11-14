"""Paths from the compiled engine of `cuda/` (paths.hpp): the discretisation of `models.simulate` and of
`multiasset.simulate_basket`, run in C++ on every CPU thread (double) or on a CUDA GPU (float).

Every model of the library is covered: Black-Scholes, Dupire local vol, Heston and SLV, alone or in a basket of up
to eight correlated names with an optional local correlation. Local variances and leverages are read from tables on
uniform grids of x (as the opt-in fast models of `structured.fastmodels`), so prices differ from the NumPy ones by
that interpolation (about 1e-4 relative on the local variance) and by their draws: the engine's normals come from
Philox, a function of (seed, pair, step), so a price depends on (seed, number of paths) only, not on the batches, the
threads or the device. The CPU and the GPU use the same normals; their prices agree within a fraction of a standard
error (cuda/tests/test_paths.cpp, tests/test_native.py).

`backend`: "cpu", "gpu" or "auto" (the GPU when there is one). This module is the `Engine` of `optlab.core.engines`,
registered through the entry point group `optlab.engines`: the core and the products reach it by name there
(`engines.choose`), never by importing it.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, overload

import numpy as np

from optlab.core.engines import EngineError, default_engine, seed64
from optlab.core.models import BlackScholesModel, HestonModel, Leverage, LocalVolModel, Model, SLVModel, time_grid
from optlab.native import gpu as _gpu

if TYPE_CHECKING:
    from optlab.structured.multiasset import Basket

__all__ = [
    "MAX_NAMES",
    "MAX_WINDOWS",
    "N_X",
    "Device",
    "Plan",
    "available",
    "calibrate_leverage",
    "cpu_threads",
    "devices",
    "gpu_available",
    "plan",
    "run",
    "simulate",
    "summary",
    "unsupported",
]

_KIND = {"const": 0, "table": 1, "heston": 2, "slv": 3}
N_X = 801  # points per table row
MAX_NAMES = 8


def _lib():
    """The compiled module (optlab.native._ext), or None."""
    return _gpu._ext()


def available() -> bool:
    """The compiled path engine is there (its CPU backend then works)."""
    return _lib() is not None


def gpu_available() -> bool:
    lib = _lib()
    return lib is not None and lib.has_cuda() and lib.gpu_count() > 0


@dataclass(frozen=True)
class Device:
    name: str
    compute_capability: str
    sm_count: int
    clock_mhz: int
    memory_gb: float
    free_gb: float
    integrated: bool
    watchdog: bool  # a display GPU: Windows resets kernels longer than 2 s, the engine launches in short chunks


def devices() -> list[Device]:
    lib = _lib()
    if lib is None or not gpu_available():
        return []
    out = []
    for i in range(lib.gpu_count()):
        d = lib.device_info(i)
        if d is not None:
            out.append(
                Device(
                    d["name"],
                    f"{d['major']}.{d['minor']}",
                    d["sm_count"],
                    d["clock_mhz"],
                    d["total_mem"] / 2**30,
                    d["free_mem"] / 2**30,
                    bool(d["integrated"]),
                    bool(d["kernel_timeout"]),
                )
            )
    return out


def cpu_threads() -> int:
    lib = _lib()
    return int(lib.cpu_threads()) if lib is not None else (os.cpu_count() or 1)


# --- models to tables ---------------------------------------------------------------------------------------------

_TABLES: OrderedDict = OrderedDict()  # (id of the surface or leverage, grid) -> rows; a few kept, the objects held


def _cached(key, obj, build):
    hit = _TABLES.get(key)
    if hit is not None and hit[0] is obj:
        _TABLES.move_to_end(key)
        return hit[1]
    rows = build()
    _TABLES[key] = (obj, rows)
    while len(_TABLES) > 16:
        _TABLES.popitem(last=False)
    return rows


def _local_var_rows(surface, t0: np.ndarray, n_x: int, sd_range=(-8.0, 6.0)):
    """(lo, inv_dx, rows) of the Dupire local variance at each step start, on x in sd_range ATM standard deviations."""

    def build():
        if hasattr(surface, "local_var_rows"):  # a surface that builds its own table (optlab.risk.surfaces)
            return surface.local_var_rows(t0, n_x, sd_range)
        lo, inv, rows = np.empty(len(t0)), np.empty(len(t0)), np.empty((len(t0), n_x))
        for i, t in enumerate(t0):
            T = max(float(t), 1.0 / 365)
            sd = float(np.sqrt(surface.w(0.0, T)))
            a, b = sd_range[0] * sd, sd_range[1] * sd
            lo[i], inv[i] = a, (n_x - 1) / (b - a)
            rows[i] = surface.local_var(np.linspace(a, b, n_x), T)
        return lo, inv, rows

    return _cached((id(surface), "lv", n_x, t0.tobytes()), surface, build)


def _leverage_rows(leverage, t0: np.ndarray, n_x: int):
    """(lo, inv_dx, squared leverage rows) of each leverage time row, and the row of each step."""

    def build():
        times = np.asarray(leverage.times, dtype=float)
        if hasattr(leverage, "rows"):  # fastmodels.UniformLeverage: (x0, inv_dx, y) per row
            src = [
                (x0, x0 + (len(y) - 1) / inv, np.linspace(x0, x0 + (len(y) - 1) / inv, len(y)), y)
                for x0, inv, y in leverage.rows
            ]
        else:
            src = [(xs[0], xs[-1], xs, vs) for xs, vs in zip(leverage.x_grids, leverage.values)]
        lo, inv, rows = np.empty(len(src)), np.empty(len(src)), np.empty((len(src), n_x))
        for r, (a, b, xs, vs) in enumerate(src):
            grid = np.linspace(a, b, n_x)
            lo[r], inv[r] = a, (n_x - 1) / (b - a)
            rows[r] = np.interp(grid, xs, vs) ** 2
        step = np.clip(np.searchsorted(times, t0 + 1e-12, side="right") - 1, 0, len(times) - 1).astype(np.int32)
        return lo, inv, rows, step

    return _cached((id(leverage), "lev", n_x, t0.tobytes()), leverage, build)


def _describe(model: Model, t0: np.ndarray, n_x: int):
    """(kind, constant variance, Heston parameters, table rows or None, step rows or None) of one name."""
    from optlab.structured.fastmodels import TabulatedLocalVol, UniformLeverage  # noqa: F401

    if isinstance(model, BlackScholesModel):
        return _KIND["const"], model.vol**2, (0.0, 1.0, 1.0, 0.0, 0.0), None, None
    if isinstance(model, (LocalVolModel, TabulatedLocalVol)):
        sd = model.sd_range if isinstance(model, TabulatedLocalVol) else (-8.0, 6.0)
        lo, inv, rows = _local_var_rows(model.surface, t0, n_x, sd)
        return _KIND["table"], 0.0, (0.0, 1.0, 1.0, 0.0, 0.0), (lo, inv, rows), np.arange(len(t0), dtype=np.int32)
    if isinstance(model, SLVModel):
        h = model.heston
        lo, inv, rows, step = _leverage_rows(model.leverage, t0, n_x)
        return _KIND["slv"], 0.0, (h.v0, h.kappa, h.theta, h.xi, h.rho), (lo, inv, rows), step
    if isinstance(model, HestonModel):
        h = model.heston
        return _KIND["heston"], 0.0, (h.v0, h.kappa, h.theta, h.xi, h.rho), None, None
    raise NotImplementedError(f"{type(model).__name__} is not in the path engine")


def unsupported(model) -> str | None:
    """Why the engine cannot simulate this model or basket, or None."""
    from optlab.structured.fastmodels import TabulatedLocalVol
    from optlab.structured.multiasset import Basket

    known = (BlackScholesModel, LocalVolModel, TabulatedLocalVol, SLVModel, HestonModel)
    if isinstance(model, Basket):
        if len(model.names) > MAX_NAMES:
            return f"more than {MAX_NAMES} names"
        bad = [n for n, m in model.models.items() if not isinstance(m, known)]
        if bad:
            return f"{', '.join(type(model.models[n]).__name__ for n in bad)} not in the path engine"
        if model.mixer is not None:
            from optlab.structured.localcorr import LocalCorrelation

            if not isinstance(model.mixer, LocalCorrelation):
                return f"{type(model.mixer).__name__} is not in the path engine (local correlation only)"
        return None
    if not isinstance(model, known):
        return f"{type(model).__name__} is not in the path engine"
    if isinstance(model, SLVModel) and not isinstance(model.leverage, Leverage) and not hasattr(model.leverage, "rows"):
        return "this leverage is not in the path engine"
    return None


@dataclass
class Plan:
    """Everything of a simulation but the pairs: built once, run on any range of pairs."""

    names: tuple
    kind: np.ndarray
    cvar: np.ndarray
    heston: np.ndarray
    root: np.ndarray
    same: int
    dt: np.ndarray
    t0: np.ndarray
    obs_step: np.ndarray
    fwd: np.ndarray
    step_row: np.ndarray | None
    lo: np.ndarray | None
    inv_dx: np.ndarray | None
    table: np.ndarray | None
    mixer: int
    mix: tuple
    low_root: np.ndarray | None
    n_x: int

    @property
    def n_obs(self) -> int:
        return len(self.obs_step)


def plan(model, market, times, dt_max=1 / 252, dt_min=1 / 2000, n_x: int = N_X) -> Plan:
    """The simulation of `models.simulate` (one model) or `simulate_basket` (a Basket) as arrays for the engine."""
    from optlab.structured.multiasset import Basket

    why = unsupported(model)
    if why:
        raise NotImplementedError(why)
    times = np.asarray(times, dtype=float)
    if isinstance(model, Basket):
        names, models = tuple(model.names), [model.models[n] for n in model.names]
        markets = [model.markets[n] for n in model.names]
        root, same = np.asarray(model.root, dtype=float), int(model.vol_corr == "same")
    else:
        names, models, markets, root, same = ("_",), [model], [market], np.eye(1), 1
    if all(isinstance(m, BlackScholesModel) for m in models):
        dt_max, dt_min = np.inf, None
    grid = time_grid(times, dt_max, dt_min)
    obs = np.searchsorted(grid, times).astype(np.int32)
    dt, t0 = np.diff(grid), grid[:-1].copy()
    k = len(models)
    kind, cvar, hes = np.empty(k, np.int32), np.empty(k), np.empty((k, 5))
    lo, inv, tab, rows_of = [], [], [], np.zeros((k, len(dt)), np.int32)
    offset = 0
    for a, m in enumerate(models):
        kind[a], cvar[a], hes[a], rows, step = _describe(m, t0, n_x)
        if rows is not None:
            assert step is not None  # noqa: S101 (narrows the type for pyright: a table comes with its steps)
            lo.append(rows[0]), inv.append(rows[1]), tab.append(rows[2])
            rows_of[a] = step + offset
            offset += len(rows[0])
    fwd = np.stack([np.asarray(mk.F(times), dtype=float) for mk in markets]) if len(times) else np.zeros((k, 0))
    mixer, mix, low = 0, (0.0,) * 8, None
    if isinstance(model, Basket) and model.mixer is not None:
        from optlab.structured.localcorr import LocalCorrelation, aligned_factors

        mx = model.mixer
        assert isinstance(mx, LocalCorrelation)  # noqa: S101 (narrows the type for pyright: unsupported() refused the others)
        low, sign, turn = aligned_factors(model.corr, model.root, mx.w0)
        mixer, mix = 1, (mx.w0, mx.level, mx.down, mx.up, mx.sigma, sign, np.cos(turn), np.sin(turn))
        low = np.ascontiguousarray(low, dtype=float)
    return Plan(
        names,
        kind,
        cvar,
        np.ascontiguousarray(hes),
        np.ascontiguousarray(root),
        same,
        dt,
        t0,
        obs,
        np.ascontiguousarray(fwd),
        rows_of if tab else None,
        np.concatenate(lo) if lo else None,
        np.concatenate(inv) if inv else None,
        np.ascontiguousarray(np.concatenate(tab)) if tab else None,
        mixer,
        tuple(float(x) for x in mix),
        low,
        n_x,
    )


_NO_INT = np.empty(0, np.int32)
_NO_FLOAT = np.empty(0)


def _given(a, empty):
    """The array, or an empty one for a part the request does not use (no table, no local correlation, no window)."""
    return empty if a is None else a


MAX_WINDOWS = 2


def run(
    p: Plan, n_pairs: int, seed: int, first_pair: int = 0, backend: str = "cpu", threads: int = 0, keep=None, windows=()
):
    """(spots, extremes, timing). spots[a] is an (2 n_pairs, len(keep)) array (Fortran order) of name a at the kept
    observations (all of them by default), pair i at row i and its antithetic at row n_pairs + i. `windows` lists
    up to two (first, last) observation indices: extremes[a][w] is then (lowest, highest) spot over that window,
    computed while simulating. timing: {"kernel_ms", "total_ms", "backend"}."""
    lib = _lib()
    if lib is None:
        raise EngineError("the compiled engine (optlab.native._ext) is not built: reinstall optlab-native")
    if backend == "auto":
        backend = "gpu" if gpu_available() else "cpu"
    if backend not in ("cpu", "gpu"):
        raise ValueError(f"backend must be cpu, gpu or auto, not {backend!r}")
    k = len(p.names)
    keep = np.arange(p.n_obs) if keep is None else np.asarray(keep, dtype=int)
    if len(windows) > MAX_WINDOWS:
        raise ValueError(f"at most {MAX_WINDOWS} windows")
    col_of = np.full(p.n_obs, -1, np.int32)
    col_of[keep] = np.arange(len(keep), dtype=np.int32)
    win_lo = np.array([w[0] for w in windows], np.int32)
    win_hi = np.array([w[1] for w in windows], np.int32)
    n_cols = len(keep) + 2 * len(windows)
    out = np.empty((k, n_cols, 2 * n_pairs))
    grid = [np.ascontiguousarray(a) for a in (p.dt, p.t0)]
    kernel_ms, total_ms = lib.paths(
        dt=grid[0],
        t0=grid[1],
        obs_step=p.obs_step,
        root=p.root,
        kind=p.kind,
        cvar=p.cvar,
        heston=p.heston,
        same=p.same,
        step_row=_given(p.step_row, _NO_INT),
        row_lo=_given(p.lo, _NO_FLOAT),
        row_inv_dx=_given(p.inv_dx, _NO_FLOAT),
        table=_given(p.table, _NO_FLOAT),
        fwd=p.fwd,
        mixer=p.mixer,
        mix=p.mix,
        low_root=_given(p.low_root, _NO_FLOAT),
        n_x=p.n_x,
        n_pairs=n_pairs,
        first_pair=first_pair,
        seed=seed,
        col_of=col_of,
        n_keep=len(keep),
        win_lo=win_lo,
        win_hi=win_hi,
        out=out,
        backend=1 if backend == "gpu" else 0,
        n_threads=threads,
    )
    m = len(keep)
    spots = [out[a, :m].T for a in range(k)]  # (rows, kept) views, Fortran order: one column per date, contiguous
    extremes = [[(out[a, m + 2 * w], out[a, m + 2 * w + 1]) for w in range(len(windows))] for a in range(k)]
    return spots, extremes, {"kernel_ms": kernel_ms, "total_ms": total_ms, "backend": backend}


@overload
def simulate(
    model: Basket,
    market,
    times,
    n_paths: int,
    seed=...,
    dt_max=...,
    dt_min=...,
    backend: str = ...,
    first_pair: int = ...,
    threads: int = ...,
) -> dict[str, np.ndarray]: ...


@overload
def simulate(
    model: Model,
    market,
    times,
    n_paths: int,
    seed=...,
    dt_max=...,
    dt_min=...,
    backend: str = ...,
    first_pair: int = ...,
    threads: int = ...,
) -> np.ndarray: ...


def simulate(
    model,
    market,
    times,
    n_paths: int,
    seed=None,
    dt_max=1 / 252,
    dt_min=1 / 2000,
    backend: str = "auto",
    first_pair: int = 0,
    threads: int = 0,
):
    """`models.simulate` (or `simulate_basket` for a Basket: a {name: array} dict) on the engine."""
    from optlab.structured.multiasset import Basket

    p = plan(model, market, times, dt_max, dt_min)
    spots, _, _ = run(p, n_paths // 2, seed64(seed), first_pair, backend, threads)
    if isinstance(model, Basket):
        return dict(zip(p.names, spots))
    return spots[0]


def calibrate_leverage(
    params,
    surface,
    market,
    T_max: float,
    mix: float = 1.0,
    n_particles: int = 100_000,
    dt_max=1 / 100,
    dt_min=1 / 2000,
    n_x=81,
    bandwidth_scale=1.0,
    rng=None,
    l_bounds=(0.05, 8.0),
    regression="nadaraya_watson",
    positive_part=False,
    backend: str = "auto",
    threads: int = 0,
):
    """`slv.calibrate_leverage` on the engine: the same algorithm (binned kernel regression on 400 bins), with the
    quantiles that bound the grid read from a histogram of 8192 bins and the local variance from uniform rows (1e-4
    relative), on Philox draws. The leverage therefore differs from NumPy's by noise and those approximations; the
    SLV vanillas it gives are checked against the surface in packages/native/tests/test_native.py."""
    from optlab.core.heston import HestonParams

    lib = _lib()
    if lib is None:
        raise EngineError("the compiled engine is not built")
    if backend == "auto":
        backend = "gpu" if gpu_available() else "cpu"
    if regression not in ("nadaraya_watson", "local_linear"):
        raise ValueError(f"unknown regression {regression!r}, expected nadaraya_watson or local_linear")
    grid = time_grid([T_max], dt_max, dt_min)
    dt, t0 = np.diff(grid), grid[:-1].copy()
    p = HestonParams(params.v0, params.kappa, params.theta, mix * params.xi, params.rho)
    lv_lo, lv_inv, lv_rows = _local_var_rows(surface, t0, N_X)
    T1 = min(1.0, float(surface.maturities[-1]))
    atm_vol = float(np.sqrt(surface.theta(T1) / T1))
    xg, vals = np.empty((len(t0), n_x)), np.empty((len(t0), n_x))
    lv = [np.ascontiguousarray(a) for a in (dt, t0, lv_lo, lv_inv, lv_rows)]
    lib.leverage(
        dt=lv[0],
        t0=lv[1],
        n_pairs=n_particles // 2,
        seed=seed64(rng),
        v0=p.v0,
        kappa=p.kappa,
        theta=p.theta,
        xi=p.xi,
        rho=p.rho,
        n_bins=400,
        n_fine=8192,
        atm_vol=atm_vol,
        bandwidth_scale=bandwidth_scale,
        l_min=l_bounds[0],
        l_max=l_bounds[1],
        q_lo=0.0005,
        q_hi=0.9995,
        local_linear=int(regression == "local_linear"),
        positive_part=int(positive_part),
        lv_lo=lv[2],
        lv_inv_dx=lv[3],
        lv_rows=lv[4],
        x_grids=xg,
        values=vals,
        backend=1 if backend == "gpu" else 0,
        n_threads=threads,
    )
    return SLVModel(params, Leverage(t0, xg, vals), mix)


def summary() -> dict[str, Any]:
    """What this machine offers the engine: the library, its CUDA build, the devices and the CPU threads."""
    lib = _lib()
    return {
        "engine": default_engine(),
        "compiled": lib is not None,
        "cuda_build": bool(lib is not None and lib.has_cuda()),
        "devices": [asdict(d) for d in devices()],
        "cpu_threads": cpu_threads(),
        "resolved": "numpy"
        if lib is None or default_engine() == "numpy"
        else ("gpu" if default_engine() in ("gpu", "auto") and gpu_available() else "cpu"),
    }
