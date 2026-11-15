"""Bridge to the C++/CUDA Monte Carlo engine of `cuda/`, with a NumPy fallback.

The engine is the compiled module `optlab.native._ext` (nanobind, built with this package by scikit-build-core, with
CUDA when a toolkit was found). Without the module or without a CUDA device, every function falls back to the NumPy
pricers of `products.py`.

What the engine covers: Black-Scholes and Heston on a flat market (constant r and q), European calls and puts,
Black-Scholes Asian calls and puts, down-and-out calls and puts. Heston uses Andersen's QE scheme with uniform
steps, not the full-truncation Euler of `simulate`, so GPU and NumPy Heston prices differ by a scheme effect
(about 0.02 on the SPX down-and-out call). The Asian has no control variate on the engine side.

`backend` is "auto" (GPU when available and the case is covered, else NumPy), "gpu", "cpu" (the engine's
double-precision C++ reference, multithreaded) or "numpy". The result says which one ran.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import ModuleType

import numpy as np

from optlab.core.engines import EngineError  # noqa: F401 (gpu.EngineError, as before)
from optlab.core.market import Market
from optlab.core.models import BlackScholesModel, HestonModel, Model
from optlab.core.products import MCResult, asian_mc, barrier_mc, european_mc

_LEG = {"call": 1, "put": 2}
_MODEL_BS, _MODEL_HESTON = 0, 1
_EUROPEAN, _ASIAN, _BARRIER = 0, 1, 2
_BACKEND = {"cpu": 0, "gpu": 1}


@dataclass
class EngineResult(MCResult):
    """MCResult plus the backend that ran and its compute time (kernel time on the GPU)."""

    backend: str = "numpy"
    compute_ms: float = math.nan


_ext_module: list = []  # [module or None] once looked up


def _ext() -> ModuleType | None:
    """The compiled module, or None when this install has none (built without a C++ compiler, or removed)."""
    if not _ext_module:
        try:
            from optlab.native import _ext as module
        except ImportError:
            module = None
        _ext_module.append(module)
    return _ext_module[0]


def engine_available() -> bool:
    """The compiled module is there (its CPU reference then works, GPU or not)."""
    return _ext() is not None


def gpu_available() -> bool:
    ext = _ext()
    return ext is not None and ext.gpu_count() > 0


def gpu_name() -> str:
    ext = _ext()
    return ext.gpu_name() if ext is not None and gpu_available() else ""


def _flat_rates(market: Market, T: float):
    """(r, q) if the market has a constant short rate and carry up to T, else None."""
    r, carry = float(market.zero_rate(T)), float(market.carry_rate(T))
    t = np.linspace(T / 16, T, 16)
    if np.allclose(market.zero_rate(t), r, rtol=0, atol=1e-10) and np.allclose(
        market.carry_rate(t), carry, rtol=0, atol=1e-10
    ):
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


def _run(model, market, T, cp, product, n_steps, n_paths, seed, backend, K, H=0.0, threads=0) -> tuple:
    """(price[4], se[4], n_paths, kernel_ms, total_ms) from the engine; an engine error raises EngineError."""
    r, q = _flat_rates(market, T)
    heston = dict(v0=0.0, kappa=0.0, theta=0.0, xi=0.0, rho=0.0)
    sigma = 0.0
    if isinstance(model, HestonModel):
        p = model.params
        kind, heston = _MODEL_HESTON, dict(v0=p.v0, kappa=p.kappa, theta=p.theta, xi=p.xi, rho=p.rho)
    else:
        kind, sigma = _MODEL_BS, model.vol
    ext = _ext()
    assert ext is not None  # noqa: S101 (narrows the type for pyright: checked above)
    return ext.price(
        model=kind,
        product=product,
        legs=(_LEG[cp], 0, 0),
        strikes=(K, K, K),
        H=float(H),
        n_steps=int(n_steps),
        S0=float(market.spot),
        r=float(r),
        q=float(q),
        T=float(T),
        sigma=float(sigma),
        **{k: float(v) for k, v in heston.items()},
        n_paths=max(4, n_paths + n_paths % 2),  # antithetic pairs
        seed=seed if seed is not None else int(np.random.SeedSequence().entropy % 2**64),
        backend=_BACKEND[backend],
        n_threads=threads,
    )


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
        raise EngineError("the compiled engine (optlab.native._ext) is not built: reinstall optlab-native")
    if backend == "gpu" and not gpu_available():
        raise EngineError("no CUDA device")
    return backend


def _result(out: tuple, k: int, backend: str) -> EngineResult:
    price, se, n_paths, kernel_ms, _ = out
    return EngineResult(price[k], se[k], int(n_paths), backend, kernel_ms)


def _numpy(res: MCResult) -> EngineResult:
    return EngineResult(res.price, res.stderr, res.n_paths, "numpy")


def european(
    model: Model,
    market: Market,
    K,
    T,
    cp="call",
    n_paths=1_000_000,
    seed: int | None = None,
    dt_max=1 / 252,
    backend="auto",
) -> EngineResult:
    """European call or put. Heston takes ceil(T / dt_max) uniform QE steps; Black-Scholes one exact step."""
    reason = _unsupported(model, market, T, cp, None)
    chosen = _choose(backend, reason)
    if chosen == "numpy":
        return _numpy(european_mc(model, market, K, T, cp, n_paths, seed, dt_max))
    n_steps = 1 if isinstance(model, BlackScholesModel) else max(1, math.ceil(T / dt_max - 1e-9))
    return _result(_run(model, market, T, cp, _EUROPEAN, n_steps, n_paths, seed, chosen, K), 0, chosen)


def asian(
    model: Model,
    market: Market,
    K,
    T,
    n_fixings=12,
    cp="call",
    average="arithmetic",
    n_paths=1_000_000,
    seed: int | None = None,
    backend="auto",
) -> EngineResult:
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


def barrier(
    model: Model,
    market: Market,
    K,
    H,
    T,
    kind="down-out",
    cp="call",
    n_monitor=252,
    n_paths=1_000_000,
    seed: int | None = None,
    backend="auto",
) -> EngineResult:
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
