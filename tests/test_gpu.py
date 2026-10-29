"""optlab.gpu: the CUDA engine against closed forms and the NumPy library, and the NumPy fallback.

Engine tests are skipped when the shared library is not built, GPU tests when there is no CUDA device."""

import numpy as np
import pytest

from optlab import (BlackScholesModel, HestonModel, HestonParams, LocalVolModel, Market, asian_mc, barrier_mc,
                    barrier_price, barrier_shift_discrete, bs_price, european_mc, geometric_asian_price,
                    heston_price)
from optlab import gpu

S, r, q, vol = 100.0, 0.04, 0.01, 0.2
MARKET = Market.flat(S, r, q)
BS = BlackScholesModel(vol)
SPX = HestonParams(0.022846642482989885, 3.0892635624407627, 0.05372458999932075, 1.307745170926575,
                   -0.6879606734541107)

needs_engine = pytest.mark.skipif(not gpu.engine_available(), reason="cuda/ shared library not built")
needs_gpu = pytest.mark.skipif(not gpu.gpu_available(), reason="no CUDA device")
ENGINE_BACKENDS = [pytest.param("gpu", marks=needs_gpu), pytest.param("cpu", marks=needs_engine)]


def within(res, exact, n_se=4.0, floor=0.0):
    return abs(res.price - exact) <= n_se * res.stderr + floor


@pytest.mark.parametrize("backend", ENGINE_BACKENDS)
@pytest.mark.parametrize("cp", ["call", "put"])
@pytest.mark.parametrize("K", [80.0, 100.0, 120.0])
def test_engine_european_matches_black_scholes(backend, cp, K):
    res = gpu.european(BS, MARKET, K, 1.0, cp, n_paths=2_000_000, seed=1, backend=backend)
    assert res.backend == backend
    assert within(res, bs_price(S, K, 1.0, r, q, vol, cp))


@pytest.mark.parametrize("backend", ENGINE_BACKENDS)
@pytest.mark.parametrize("cp, K", [("call", 100.0), ("call", 119.0), ("put", 80.0)])
def test_engine_heston_european_matches_fourier(backend, cp, K):
    n = 4_000_000 if backend == "gpu" else 200_000
    res = gpu.european(HestonModel(SPX), MARKET, K, 1.0, cp, n_paths=n, seed=2, backend=backend)
    D, F = MARKET.D(1.0), MARKET.F(1.0)
    assert within(res, float(heston_price(F, K, 1.0, SPX, D, cp)[0]), floor=0.02)  # QE discretisation allowance


@pytest.mark.parametrize("backend", ENGINE_BACKENDS)
def test_engine_asians_match_closed_form_and_numpy(backend):
    times = np.linspace(1 / 12, 1.0, 12)
    geo = gpu.asian(BS, MARKET, 100, 1.0, average="geometric", n_paths=2_000_000, seed=3, backend=backend)
    assert within(geo, float(geometric_asian_price(S, 100, r, q, vol, times)))
    arith = gpu.asian(BS, MARKET, 100, 1.0, n_paths=2_000_000, seed=3, backend=backend)
    ref = asian_mc(BS, MARKET, 100, 1.0, n_paths=400_000, rng=3)  # control variate: SE ~ 1e-4
    assert abs(arith.price - ref.price) <= 4 * np.hypot(arith.stderr, ref.stderr)


@pytest.mark.parametrize("backend", ENGINE_BACKENDS)
@pytest.mark.parametrize("cp", ["call", "put"])
def test_engine_bs_barrier_matches_bgk(backend, cp):
    res = gpu.barrier(BS, MARKET, 100, 80, 1.0, cp=cp, n_paths=2_000_000, seed=4, backend=backend)
    exact = barrier_price(S, 100, barrier_shift_discrete(80, S, vol, 1 / 252), 1.0, r, q, vol, "down-out", cp)
    assert within(res, float(exact), floor=0.005)  # BGK shift: method error of a few 1e-3


@needs_gpu
def test_gpu_heston_barrier_close_to_euler_library():
    model = HestonModel(SPX)
    res = gpu.barrier(model, MARKET, 100, 80, 1.0, n_paths=4_000_000, seed=5, backend="gpu")
    assert within(res, 8.0927, floor=4 * 0.0092)  # QE reference of the handoff (JavaScript of the demo page)
    euler = barrier_mc(model, MARKET, 100, 80, 1.0, n_paths=100_000, rng=5)
    assert abs(res.price - euler.price) < 0.1  # scheme effect (QE against full-truncation Euler) ~ 0.02


@needs_gpu
def test_gpu_and_cpu_reference_agree():
    kw = dict(n_paths=400_000, seed=6)
    g = gpu.barrier(HestonModel(SPX), MARKET, 100, 80, 1.0, backend="gpu", **kw)
    c = gpu.barrier(HestonModel(SPX), MARKET, 100, 80, 1.0, backend="cpu", **kw)
    assert abs(g.price - c.price) <= 4 * np.hypot(g.stderr, c.stderr)  # different generators: independent runs


@needs_gpu
def test_gpu_price_depends_only_on_seed():
    a = gpu.european(HestonModel(SPX), MARKET, 100, 1.0, n_paths=200_000, seed=7, backend="gpu")
    b = gpu.european(HestonModel(SPX), MARKET, 100, 1.0, n_paths=200_000, seed=7, backend="gpu")
    c = gpu.european(HestonModel(SPX), MARKET, 100, 1.0, n_paths=200_000, seed=8, backend="gpu")
    assert a.price == b.price and a.price != c.price


def test_numpy_backend_is_the_library():
    res = gpu.european(BS, MARKET, 100, 1.0, n_paths=20_000, seed=9, backend="numpy")
    ref = european_mc(BS, MARKET, 100, 1.0, n_paths=20_000, rng=9)
    assert res.backend == "numpy" and (res.price, res.stderr) == (ref.price, ref.stderr)


def test_auto_falls_back_on_cases_outside_the_engine():
    res = gpu.barrier(BS, MARKET, 100, 120, 1.0, kind="up-out", n_paths=20_000, seed=10)
    ref = barrier_mc(BS, MARKET, 100, 120, 1.0, kind="up-out", n_paths=20_000, rng=10)
    assert res.backend == "numpy" and res.price == ref.price
    curved = Market(S, np.array([0.5, 1.0]), np.exp(-np.array([0.01, 0.06])), None)
    assert gpu.european(BS, curved, 100, 1.0, n_paths=20_000, seed=10).backend == "numpy"


def test_explicit_engine_backend_rejects_unsupported_cases():
    with pytest.raises(ValueError, match="down-out only"):
        gpu.barrier(BS, MARKET, 100, 120, 1.0, kind="up-out", backend="gpu")
    with pytest.raises(ValueError, match="Black-Scholes only"):
        gpu.asian(HestonModel(SPX), MARKET, 100, 1.0, backend="cpu")
    lv = LocalVolModel.__new__(LocalVolModel)  # any model outside the engine
    with pytest.raises(ValueError, match="not in the engine"):
        gpu.european(lv, MARKET, 100, 1.0, backend="gpu")
    with pytest.raises(ValueError, match="backend"):
        gpu.european(BS, MARKET, 100, 1.0, backend="cuda")


@needs_engine
def test_engine_errors_become_exceptions():
    with pytest.raises(ValueError, match="0 < H < spot"):
        gpu.barrier(BS, MARKET, 100, 120, 1.0, backend="cpu")
    with pytest.raises(gpu.EngineError, match="Heston parameters"):
        gpu.european(HestonModel(HestonParams(0.04, 1.5, 0.04, 0.5, -1.5)), MARKET, 100, 1.0, backend="cpu")
