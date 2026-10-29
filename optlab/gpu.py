"""Bridge to the C++/CUDA Monte Carlo engine of `cuda/`, with a NumPy fallback.

The engine is loaded through ctypes from the `optlab_cuda_c` shared library: `$OPTLAB_CUDA_LIB` if set, else
`build/Release/` or `build/` at the repository root (`cmake --build build --config Release`). Without the library
or without a CUDA device, every function falls back to the NumPy pricers of `products.py`.

What the engine covers: Black-Scholes and Heston on a flat market (constant r and q), European calls and puts,
Black-Scholes Asian calls and puts, down-and-out calls and puts. Heston uses Andersen's QE scheme with uniform
steps, not the full-truncation Euler of `simulate`, so GPU and NumPy Heston prices differ by a scheme effect
(about 0.02 on the SPX down-and-out call). The Asian has no control variate on the engine side.

`backend` is "auto" (GPU when available and the case is covered, else NumPy), "gpu", "cpu" (the engine's
double-precision C++ reference, multithreaded) or "numpy". The result says which one ran.
"""

from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .market import Market
from .models import BlackScholesModel, HestonModel, Model
from .products import MCResult, asian_mc, barrier_mc, european_mc

_ROOT = Path(__file__).resolve().parent.parent
_LIB_NAME = "optlab_cuda_c.dll" if os.name == "nt" else "liboptlab_cuda_c.so"
_LEG = {"call": 1, "put": 2}
_MODEL_BS, _MODEL_HESTON = 0, 1
_EUROPEAN, _ASIAN, _BARRIER = 0, 1, 2
_BACKEND = {"cpu": 0, "gpu": 1}


class _Request(ctypes.Structure):  # optlab_request in cuda/include/optlab_cuda/capi.h
    _fields_ = [
        ("model", ctypes.c_int), ("product", ctypes.c_int), ("legs", ctypes.c_int * 3),
        ("strikes", ctypes.c_double * 3), ("H", ctypes.c_double), ("n_steps", ctypes.c_int),
        ("S0", ctypes.c_double), ("r", ctypes.c_double), ("q", ctypes.c_double), ("T", ctypes.c_double),
        ("sigma", ctypes.c_double),
        ("v0", ctypes.c_double), ("kappa", ctypes.c_double), ("theta", ctypes.c_double), ("xi", ctypes.c_double),
        ("rho", ctypes.c_double),
        ("n_paths", ctypes.c_longlong), ("seed", ctypes.c_ulonglong),
    ]


class _Result(ctypes.Structure):  # optlab_result
    _fields_ = [("price", ctypes.c_double * 4), ("se", ctypes.c_double * 4), ("n_paths", ctypes.c_longlong),
                ("kernel_ms", ctypes.c_double), ("total_ms", ctypes.c_double)]


@dataclass
class EngineResult(MCResult):
    """MCResult plus the backend that ran and its compute time (kernel time on the GPU)."""

    backend: str = "numpy"
    compute_ms: float = math.nan


class EngineError(RuntimeError):
    pass


_lib = None
_lib_tried = False


def _library():
    global _lib, _lib_tried
    if _lib_tried:
        return _lib
    _lib_tried = True
    env = os.environ.get("OPTLAB_CUDA_LIB")
    candidates = [Path(env)] if env else [_ROOT / "build" / "Release" / _LIB_NAME, _ROOT / "build" / _LIB_NAME]
    for path in candidates:
        if path.is_file():
            try:
                lib = ctypes.CDLL(str(path))
            except OSError:
                continue
            lib.optlab_gpu_count.restype = ctypes.c_int
            lib.optlab_price.argtypes = [ctypes.POINTER(_Request), ctypes.c_int, ctypes.c_int, ctypes.POINTER(_Result)]
            lib.optlab_price.restype = ctypes.c_int
            lib.optlab_last_error.restype = ctypes.c_char_p
            lib.optlab_gpu_name.restype = ctypes.c_char_p
            _lib = lib
            break
    return _lib


def engine_available() -> bool:
    """The shared library is found and loads (its CPU reference then works, GPU or not)."""
    return _library() is not None


def gpu_available() -> bool:
    lib = _library()
    return lib is not None and lib.optlab_gpu_count() > 0


def gpu_name() -> str:
    return _library().optlab_gpu_name().decode() if gpu_available() else ""


def _flat_rates(market: Market, T: float):
    """(r, q) if the market has a constant short rate and carry up to T, else None."""
    r, carry = float(market.zero_rate(T)), float(market.carry_rate(T))
    t = np.linspace(T / 16, T, 16)
    if np.allclose(market.zero_rate(t), r, rtol=0, atol=1e-10) and np.allclose(
            market.carry_rate(t), carry, rtol=0, atol=1e-10):
        return r, r - carry
    return None


def _unsupported(model: Model, market: Market, T: float, cp: str, why: str | None):
    """Why the engine cannot price this case, or None."""
    if why:
        return why
    if not isinstance(model, (BlackScholesModel, HestonModel)):
        return f"{type(model).__name__} is not in the engine (Black-Scholes and Heston only)"
    if cp not in _LEG:
        return f"cp must be 'call' or 'put', not {cp!r}"
    if _flat_rates(market, T) is None:
        return "the engine needs a flat market (constant r and q)"
    return None


def _run(model, market, T, cp, product, n_steps, n_paths, seed, backend, K, H=0.0, threads=0) -> _Result:
    r, q = _flat_rates(market, T)
    rq = _Request()
    rq.product, rq.n_steps, rq.H = product, n_steps, H
    rq.legs[:] = [_LEG[cp], 0, 0]
    rq.strikes[:] = [K, K, K]
    rq.S0, rq.r, rq.q, rq.T = market.spot, r, q, T
    if isinstance(model, HestonModel):
        p = model.params
        rq.model, rq.v0, rq.kappa, rq.theta, rq.xi, rq.rho = _MODEL_HESTON, p.v0, p.kappa, p.theta, p.xi, p.rho
    else:
        rq.model, rq.sigma = _MODEL_BS, model.vol
    rq.n_paths = max(4, n_paths + n_paths % 2)  # antithetic pairs
    rq.seed = seed if seed is not None else int(np.random.SeedSequence().entropy % 2**64)
    out = _Result()
    if _library().optlab_price(ctypes.byref(rq), _BACKEND[backend], threads, ctypes.byref(out)) != 0:
        raise EngineError(_library().optlab_last_error().decode())
    return out


def _choose(backend: str, reason: str | None) -> str:
    if backend not in ("auto", "gpu", "cpu", "numpy"):
        raise ValueError(f"backend must be 'auto', 'gpu', 'cpu' or 'numpy', not {backend!r}")
    if backend == "numpy":
        return backend
    if backend == "auto":
        return "gpu" if reason is None and gpu_available() else "numpy"
    if reason is not None:
        raise ValueError(f"backend {backend!r}: {reason}")
    if not engine_available():
        raise EngineError(f"{_LIB_NAME} not found: build cuda/ or set OPTLAB_CUDA_LIB")
    if backend == "gpu" and not gpu_available():
        raise EngineError("no CUDA device")
    return backend


def _result(out: _Result, k: int, backend: str) -> EngineResult:
    return EngineResult(out.price[k], out.se[k], int(out.n_paths), backend, out.kernel_ms)


def _numpy(res: MCResult) -> EngineResult:
    return EngineResult(res.price, res.stderr, res.n_paths, "numpy")


def european(model: Model, market: Market, K, T, cp="call", n_paths=1_000_000, seed: int | None = None,
             dt_max=1 / 252, backend="auto") -> EngineResult:
    """European call or put. Heston takes ceil(T / dt_max) uniform QE steps; Black-Scholes one exact step."""
    reason = _unsupported(model, market, T, cp, None)
    chosen = _choose(backend, reason)
    if chosen == "numpy":
        return _numpy(european_mc(model, market, K, T, cp, n_paths, seed, dt_max))
    n_steps = 1 if isinstance(model, BlackScholesModel) else max(1, math.ceil(T / dt_max - 1e-9))
    return _result(_run(model, market, T, cp, _EUROPEAN, n_steps, n_paths, seed, chosen, K), 0, chosen)


def asian(model: Model, market: Market, K, T, n_fixings=12, cp="call", average="arithmetic",
          n_paths=1_000_000, seed: int | None = None, backend="auto") -> EngineResult:
    """Fixed-strike Asian on n equally spaced fixings. Engine: Black-Scholes only (exact steps between
    fixings); NumPy keeps its geometric control variate for the arithmetic average."""
    if average not in ("arithmetic", "geometric"):
        raise ValueError(f"average must be 'arithmetic' or 'geometric', not {average!r}")
    why = None if isinstance(model, BlackScholesModel) else "Asian: Black-Scholes only in the engine"
    chosen = _choose(backend, _unsupported(model, market, T, cp, why))
    if chosen == "numpy":
        return _numpy(asian_mc(model, market, K, T, n_fixings, cp, average, n_paths, seed))
    out = _run(model, market, T, cp, _ASIAN, n_fixings, n_paths, seed, chosen, K)
    return _result(out, 0 if average == "arithmetic" else 1, chosen)


def barrier(model: Model, market: Market, K, H, T, kind="down-out", cp="call", n_monitor=252,
            n_paths=1_000_000, seed: int | None = None, backend="auto") -> EngineResult:
    """Single barrier on n_monitor equally spaced dates. Engine: down-and-out with H below the spot, one
    simulation step per monitoring date."""
    why = None
    if kind != "down-out":
        why = f"barrier: down-out only in the engine, not {kind!r}"
    elif not 0 < H < market.spot:
        why = "barrier: the engine needs 0 < H < spot"
    chosen = _choose(backend, _unsupported(model, market, T, cp, why))
    if chosen == "numpy":
        return _numpy(barrier_mc(model, market, K, H, T, kind, cp, n_monitor, n_paths, seed))
    return _result(_run(model, market, T, cp, _BARRIER, n_monitor, n_paths, seed, chosen, K, H), 0, chosen)
