"""Paths from the compiled engine of `cuda/` (paths.hpp): the discretisation of `models.simulate` and of
`multiasset.simulate_basket`, run in C++ on every CPU thread (double) or on a CUDA GPU (float).

Every model of the library is covered: Black-Scholes, Dupire local vol, Heston and SLV, alone or in a basket of up
to eight correlated names with an optional local correlation. Local variances and leverages are read from tables on
uniform grids of x (as the opt-in fast models of `structured.fastmodels`), so prices differ from the NumPy ones by
that interpolation (about 1e-4 relative on the local variance) and by their draws: the engine's normals come from
Philox, a function of (seed, pair, step), so a price depends on (seed, number of paths) only, not on the batches, the
threads or the device. The CPU and the GPU use the same normals; their prices agree within a fraction of a standard
error (cuda/tests/test_paths.cpp, tests/test_native.py).

`backend`: "cpu", "gpu" or "auto" (the GPU when there is one). `choose()` decides between the engine and NumPy for
the batch engine of `structured.engine`.
"""

from __future__ import annotations

import ctypes
import os
from collections import OrderedDict
from dataclasses import asdict, dataclass

import numpy as np
from optlab.core.models import BlackScholesModel, HestonModel, Leverage, LocalVolModel, Model, SLVModel, time_grid
from optlab.native import gpu as _gpu

ENGINES = ("numpy", "cpu", "gpu", "auto")
_KIND = {"const": 0, "table": 1, "heston": 2, "slv": 3}
N_X = 801  # points per table row
MAX_NAMES = 8


class _PathsRequest(ctypes.Structure):  # optlab_paths_request in cuda/include/optlab_cuda/capi.h
    _fields_ = [
        ("n_names", ctypes.c_int),
        ("n_steps", ctypes.c_int),
        ("n_obs", ctypes.c_int),
        ("n_x", ctypes.c_int),
        ("n_rows", ctypes.c_int),
        ("n_pairs", ctypes.c_longlong),
        ("first_pair", ctypes.c_longlong),
        ("seed", ctypes.c_ulonglong),
        ("dt", ctypes.c_void_p),
        ("t0", ctypes.c_void_p),
        ("obs_step", ctypes.c_void_p),
        ("root", ctypes.c_void_p),
        ("kind", ctypes.c_void_p),
        ("cvar", ctypes.c_void_p),
        ("heston", ctypes.c_void_p),
        ("same", ctypes.c_int),
        ("step_row", ctypes.c_void_p),
        ("row_lo", ctypes.c_void_p),
        ("row_inv_dx", ctypes.c_void_p),
        ("table", ctypes.c_void_p),
        ("fwd", ctypes.c_void_p),
        ("mixer", ctypes.c_int),
        ("mix", ctypes.c_double * 8),
        ("low_root", ctypes.c_void_p),
        ("n_cols", ctypes.c_int),
        ("n_keep", ctypes.c_int),
        ("col_of", ctypes.c_void_p),
        ("n_windows", ctypes.c_int),
        ("win_lo", ctypes.c_void_p),
        ("win_hi", ctypes.c_void_p),
        ("out", ctypes.c_void_p),
        ("kernel_ms", ctypes.c_double),
        ("total_ms", ctypes.c_double),
    ]


class _LeverageRequest(ctypes.Structure):  # optlab_leverage_request
    _fields_ = [
        ("n_steps", ctypes.c_int),
        ("dt", ctypes.c_void_p),
        ("t0", ctypes.c_void_p),
        ("n_pairs", ctypes.c_longlong),
        ("seed", ctypes.c_ulonglong),
        ("v0", ctypes.c_double),
        ("kappa", ctypes.c_double),
        ("theta", ctypes.c_double),
        ("xi", ctypes.c_double),
        ("rho", ctypes.c_double),
        ("n_x", ctypes.c_int),
        ("n_bins", ctypes.c_int),
        ("n_fine", ctypes.c_int),
        ("atm_vol", ctypes.c_double),
        ("bandwidth_scale", ctypes.c_double),
        ("l_min", ctypes.c_double),
        ("l_max", ctypes.c_double),
        ("q_lo", ctypes.c_double),
        ("q_hi", ctypes.c_double),
        ("local_linear", ctypes.c_int),
        ("positive_part", ctypes.c_int),
        ("n_lv", ctypes.c_int),
        ("lv_lo", ctypes.c_void_p),
        ("lv_inv_dx", ctypes.c_void_p),
        ("lv_rows", ctypes.c_void_p),
        ("x_grids", ctypes.c_void_p),
        ("values", ctypes.c_void_p),
        ("total_ms", ctypes.c_double),
    ]


class _Device(ctypes.Structure):  # optlab_device
    _fields_ = [
        ("name", ctypes.c_char * 256),
        ("major", ctypes.c_int),
        ("minor", ctypes.c_int),
        ("sm_count", ctypes.c_int),
        ("clock_mhz", ctypes.c_int),
        ("total_mem", ctypes.c_longlong),
        ("free_mem", ctypes.c_longlong),
        ("integrated", ctypes.c_int),
        ("kernel_timeout", ctypes.c_int),
    ]


_ready = None


def _lib():
    """The shared library with the path engine's functions declared, or None (missing, or built before them)."""
    global _ready
    if _ready is not None:
        return _ready or None
    lib = _gpu._library()
    if lib is None or not hasattr(lib, "optlab_paths") or not hasattr(lib, "optlab_leverage"):
        _ready = False
        return None
    lib.optlab_paths.argtypes = [ctypes.POINTER(_PathsRequest), ctypes.c_int, ctypes.c_int]
    lib.optlab_paths.restype = ctypes.c_int
    lib.optlab_leverage.argtypes = [ctypes.POINTER(_LeverageRequest), ctypes.c_int, ctypes.c_int]
    lib.optlab_leverage.restype = ctypes.c_int
    lib.optlab_device_info.argtypes = [ctypes.c_int, ctypes.POINTER(_Device)]
    lib.optlab_device_info.restype = ctypes.c_int
    lib.optlab_has_cuda.restype = ctypes.c_int
    lib.optlab_cpu_threads.restype = ctypes.c_int
    _ready = lib
    return lib


def available() -> bool:
    """The compiled path engine is there (its CPU backend then works)."""
    return _lib() is not None


def gpu_available() -> bool:
    lib = _lib()
    return lib is not None and lib.optlab_has_cuda() == 1 and lib.optlab_gpu_count() > 0


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
    for i in range(lib.optlab_gpu_count()):
        d = _Device()
        if lib.optlab_device_info(i, ctypes.byref(d)):
            out.append(
                Device(
                    d.name.decode(),
                    f"{d.major}.{d.minor}",
                    d.sm_count,
                    d.clock_mhz,
                    d.total_mem / 2**30,
                    d.free_mem / 2**30,
                    bool(d.integrated),
                    bool(d.kernel_timeout),
                )
            )
    return out


def cpu_threads() -> int:
    lib = _lib()
    return int(lib.optlab_cpu_threads()) if lib is not None else (os.cpu_count() or 1)


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


def seed64(seed) -> int:
    """A 64-bit key from an int, a SeedSequence or None (fresh entropy)."""
    if isinstance(seed, np.random.SeedSequence):
        return int(seed.generate_state(1, np.uint64)[0])
    if seed is None:
        return int(np.random.SeedSequence().generate_state(1, np.uint64)[0])
    return int(seed) & (2**64 - 1)


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
            lo.append(rows[0]), inv.append(rows[1]), tab.append(rows[2])
            rows_of[a] = step + offset
            offset += len(rows[0])
    fwd = np.stack([np.asarray(mk.F(times), dtype=float) for mk in markets]) if len(times) else np.zeros((k, 0))
    mixer, mix, low = 0, (0.0,) * 8, None
    if isinstance(model, Basket) and model.mixer is not None:
        from optlab.structured.localcorr import aligned_factors

        mx = model.mixer
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


def _ptr(a):
    return None if a is None else a.ctypes.data


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
        raise _gpu.EngineError(
            f"{_gpu._LIB_NAME} with the path engine not found: build cuda/ (README) or set OPTLAB_CUDA_LIB"
        )
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
    rq = _PathsRequest()
    rq.n_names, rq.n_steps, rq.n_obs, rq.n_x = k, len(p.dt), p.n_obs, p.n_x
    rq.n_rows = 0 if p.lo is None else len(p.lo)
    rq.n_pairs, rq.first_pair, rq.seed = n_pairs, first_pair, seed
    grid = [np.ascontiguousarray(a) for a in (p.dt, p.t0)]  # held until the call returns
    rq.dt, rq.t0 = _ptr(grid[0]), _ptr(grid[1])
    rq.obs_step, rq.root, rq.kind, rq.cvar, rq.heston = (
        _ptr(p.obs_step),
        _ptr(p.root),
        _ptr(p.kind),
        _ptr(p.cvar),
        _ptr(p.heston),
    )
    rq.same, rq.step_row, rq.row_lo, rq.row_inv_dx = p.same, _ptr(p.step_row), _ptr(p.lo), _ptr(p.inv_dx)
    rq.table, rq.fwd, rq.mixer, rq.low_root, rq.out = _ptr(p.table), _ptr(p.fwd), p.mixer, _ptr(p.low_root), _ptr(out)
    rq.mix[:] = p.mix
    rq.n_cols, rq.n_keep, rq.col_of, rq.n_windows = n_cols, len(keep), _ptr(col_of), len(windows)
    rq.win_lo, rq.win_hi = (_ptr(win_lo), _ptr(win_hi)) if len(windows) else (None, None)
    if lib.optlab_paths(ctypes.byref(rq), 1 if backend == "gpu" else 0, threads) != 0:
        raise _gpu.EngineError(lib.optlab_last_error().decode())
    m = len(keep)
    spots = [out[a, :m].T for a in range(k)]  # (rows, kept) views, Fortran order: one column per date, contiguous
    extremes = [[(out[a, m + 2 * w], out[a, m + 2 * w + 1]) for w in range(len(windows))] for a in range(k)]
    return spots, extremes, {"kernel_ms": rq.kernel_ms, "total_ms": rq.total_ms, "backend": backend}


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
    SLV vanillas it gives are checked against the surface in tests/test_native.py."""
    from optlab.core.heston import HestonParams

    lib = _lib()
    if lib is None:
        raise _gpu.EngineError("the compiled engine is not built")
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
    rq = _LeverageRequest()
    rq.n_steps, rq.n_pairs, rq.seed = len(t0), n_particles // 2, seed64(rng)
    keep = [np.ascontiguousarray(a) for a in (dt, t0, lv_lo, lv_inv, lv_rows)]
    rq.dt, rq.t0, rq.lv_lo, rq.lv_inv_dx, rq.lv_rows = (a.ctypes.data for a in keep)
    rq.v0, rq.kappa, rq.theta, rq.xi, rq.rho = p.v0, p.kappa, p.theta, p.xi, p.rho
    rq.n_x, rq.n_bins, rq.n_fine = n_x, 400, 8192
    rq.atm_vol, rq.bandwidth_scale, rq.l_min, rq.l_max = atm_vol, bandwidth_scale, l_bounds[0], l_bounds[1]
    rq.q_lo, rq.q_hi = 0.0005, 0.9995
    rq.local_linear, rq.positive_part, rq.n_lv = int(regression == "local_linear"), int(positive_part), N_X
    rq.x_grids, rq.values = xg.ctypes.data, vals.ctypes.data
    if lib.optlab_leverage(ctypes.byref(rq), 1 if backend == "gpu" else 0, threads) != 0:
        raise _gpu.EngineError(lib.optlab_last_error().decode())
    return SLVModel(params, Leverage(t0, xg, vals), mix)


_DEFAULT: str | None = None


def set_default(engine: str | None):
    """The engine of every simulation that does not name one (None: back to $OPTLAB_ENGINE or numpy)."""
    global _DEFAULT
    if engine is not None and engine.lower() not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}, not {engine!r}")
    _DEFAULT = engine.lower() if engine else None


def summary() -> dict:
    """What this machine offers the engine: the library, its CUDA build, the devices and the CPU threads."""
    lib = _lib()
    return {
        "engine": default_engine(),
        "compiled": lib is not None,
        "cuda_build": bool(lib and lib.optlab_has_cuda()),
        "devices": [asdict(d) for d in devices()],
        "cpu_threads": cpu_threads(),
        "resolved": "numpy"
        if lib is None or default_engine() == "numpy"
        else ("gpu" if default_engine() in ("gpu", "auto") and gpu_available() else "cpu"),
    }


def default_engine() -> str:
    """set_default's, else $OPTLAB_ENGINE, else numpy: the library's reference numbers stay those of NumPy unless
    asked."""
    if _DEFAULT:
        return _DEFAULT
    e = os.environ.get("OPTLAB_ENGINE", "numpy").lower()
    return e if e in ENGINES else "numpy"


def choose(engine: str | None, model) -> tuple[str, str | None]:
    """(backend, reason): "numpy", "cpu" or "gpu" for this request; the reason says why NumPy when it falls back."""
    engine = (engine or default_engine()).lower()
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}, not {engine!r}")
    if engine == "numpy":
        return "numpy", None
    why = unsupported(model)
    if why is None and not available():
        why = "the compiled engine is not built"
    if why is None and engine == "gpu" and not gpu_available():
        why = "no CUDA device"
    if why is not None:
        if engine == "auto":
            return "numpy", why
        raise _gpu.EngineError(why)
    if engine == "auto":
        return ("gpu" if gpu_available() else "cpu"), None
    return engine, None
