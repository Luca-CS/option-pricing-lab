"""The compiled path engine (optlab.native) against independent references: closed forms, the SVI surface it is
calibrated to, Fourier, the NumPy simulation on another seed, and the CPU against the GPU on the same draws.
Skipped without the library (cuda/, cmake), the GPU checks without a CUDA device."""

import numpy as np
import pytest
from optlab import native
from optlab.core.blackscholes import black_price
from optlab.core.heston import heston_price
from optlab.core.market import Market
from optlab.core.models import BlackScholesModel, simulate
from optlab.structured import PricingContext, compile_sheet, engine, preset, price_many
from optlab.structured.localcorr import LocalCorrelation, StochasticMix
from optlab.structured.multiasset import Basket, simulate_basket

ENGINE_MARKS = [pytest.mark.native, pytest.mark.skipif(not native.available(), reason="path engine not built (cuda/)")]
GPU_MARKS = [
    pytest.mark.native,
    pytest.mark.gpu,
    pytest.mark.skipif(not native.gpu_available(), reason="no CUDA device"),
]


def _marked(marks):
    def apply(f):
        for m in marks:
            f = m(f)
        return f

    return apply


needs_engine = _marked(ENGINE_MARKS)
needs_gpu = _marked(GPU_MARKS)
BACKENDS = [pytest.param("cpu", marks=ENGINE_MARKS), pytest.param("gpu", marks=GPU_MARKS)]
CTX = PricingContext.from_calibration()
K = np.array([80.0, 90.0, 100.0, 110.0, 120.0])


def calls(S, D, strikes=K):
    """Prices and standard errors of calls on pair averages (rows i and n/2 + i are a pair)."""
    h = len(S) // 2
    pay = D * np.maximum(S[:, None] - strikes, 0.0)
    pay = 0.5 * (pay[:h] + pay[h:])
    return pay.mean(axis=0), pay.std(axis=0, ddof=1) / np.sqrt(h)


@pytest.mark.parametrize("backend", BACKENDS)
def test_black_scholes_calls_match_the_formula(backend):
    m = Market.flat(100.0, 0.04, 0.01)
    S = native.simulate(BlackScholesModel(0.2), m, [1.0], 400_000, seed=1, backend=backend)[:, 0]
    p, se = calls(S, float(m.D(1.0)))
    exact = black_price(float(m.F(1.0)), K, 1.0, 0.2, float(m.D(1.0)))
    assert np.all(np.abs(p - exact) < 4 * se)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("model", ["lv", "slv"])
def test_local_vol_and_slv_reprice_the_surface(backend, model):
    T = 1.0
    S = native.simulate(CTX.model(model, T), CTX.market, [T], 400_000, seed=2, backend=backend)[:, 0]
    F, D = float(CTX.market.F(T)), float(CTX.market.D(T))
    p, se = calls(S, D)
    target = black_price(F, K, T, CTX.surface.iv(np.log(K / F), T), D)
    floor = 0.01 if model == "lv" else 0.03  # Euler bias of the library, the same in NumPy; SLV: its leverage fit
    assert np.all(np.abs(p - target) < 4 * se + floor), (p - target, se)


@pytest.mark.parametrize("backend", BACKENDS)
def test_heston_calls_match_fourier_up_to_the_euler_bias(backend):
    T = 1.0
    S = native.simulate(CTX.model("heston", T), CTX.market, [T], 400_000, seed=3, backend=backend)[:, 0]
    F, D = float(CTX.market.F(T)), float(CTX.market.D(T))
    p, se = calls(S, D)
    exact = np.array([float(heston_price(F, k, T, CTX.heston, D)[0]) for k in K])
    assert np.all(np.abs(p - exact) < 4 * se + 0.03)


@pytest.mark.parametrize("model", ["lv", "heston", "slv"])
@needs_engine
def test_the_engine_and_numpy_give_the_same_prices_on_independent_draws(model):
    T = 2.0
    m = CTX.model(model, T)
    a = calls(simulate(m, CTX.market, [T], 200_000, rng=4)[:, 0], 1.0)
    b = calls(native.simulate(m, CTX.market, [T], 200_000, seed=4, backend="cpu")[:, 0], 1.0)
    assert np.all(np.abs(a[0] - b[0]) < 4 * np.hypot(a[1], b[1]))


@needs_gpu
@pytest.mark.parametrize("model", ["lv", "heston", "slv"])
def test_cpu_and_gpu_agree_on_the_same_draws(model):
    T = 2.0
    m = CTX.model(model, T)
    c = native.simulate(m, CTX.market, [0.5, T], 200_000, seed=5, backend="cpu")
    g = native.simulate(m, CTX.market, [0.5, T], 200_000, seed=5, backend="gpu")
    rel = np.abs(g / c - 1.0)
    if model == "heston":  # the rounding decides whether a variance near zero is truncated: 3 % of the paths part
        assert np.median(rel) < 1e-5 and np.mean(rel > 1e-3) < 0.06
    else:  # measured: median 5e-8, worst 2e-5
        assert np.median(rel) < 1e-6 and rel.max() < 1e-4
    h = len(c) // 2
    pay = lambda S: 0.5 * (np.maximum(S[:h] - 100, 0) + np.maximum(S[h:] - 100, 0))  # noqa: E731
    diff = pay(c[:, 1]) - pay(g[:, 1])
    price = pay(c[:, 1]).mean()  # the rounding is systematic, not noise: a relative tolerance on top of 4 SE
    assert abs(diff.mean()) < 4 * diff.std(ddof=1) / np.sqrt(h) + 1e-5 * price


@needs_engine
def test_a_price_depends_on_the_seed_and_the_number_of_paths_only(monkeypatch):
    product = compile_sheet(preset("phoenix"), CTX.valuation)
    model = CTX.model("lv", product.horizon)

    def one():
        (r,) = price_many(
            product.program,
            model,
            CTX.market,
            [{product.coupon_param: 0.02}],
            n_paths=40_000,
            seed=6,
            valuation=CTX.valuation,
            calendar=product.calendar,
            engine="cpu",
        )
        return r

    whole = one()
    monkeypatch.setattr(engine, "NATIVE_BATCH_BYTES", 1)  # 4096 pairs per batch
    split = one()
    assert split.timings["batches"] > 1 and whole.timings["batches"] == 1
    assert split.price == whole.price and split.stderr == whole.stderr


@needs_engine
def test_a_phoenix_on_the_engine_matches_numpy():
    product = compile_sheet(preset("phoenix"), CTX.valuation)
    for name in ("lv", "slv"):
        model = CTX.model(name, product.horizon)
        kw = dict(n_paths=200_000, valuation=CTX.valuation, calendar=product.calendar)
        (a,) = price_many(product.program, model, CTX.market, [{product.coupon_param: 0.02}], seed=7, **kw)
        (b,) = price_many(
            product.program, model, CTX.market, [{product.coupon_param: 0.02}], seed=8, engine="cpu", **kw
        )
        assert b.timings["engine"] == "cpu" and a.timings["engine"] == "numpy"
        assert abs(a.price - b.price) < 4 * np.hypot(a.stderr, b.stderr)


def basket(mixer=None):
    names = ("SPX", "AAA", "BBB")
    corr = np.array([[1.0, 0.8, 0.6], [0.8, 1.0, 0.7], [0.6, 0.7, 1.0]])
    lv = CTX.model("lv", 1.0)
    return Basket(names, {n: lv for n in names}, {n: CTX.market for n in names}, corr, mixer=mixer)


@needs_engine
def test_a_basket_keeps_its_correlation_and_its_worst_of_price():
    b = basket()
    flat = Basket(b.names, {n: BlackScholesModel(0.2) for n in b.names}, b.markets, b.corr)
    x = native.simulate(flat, None, [1.0], 200_000, seed=9, backend="cpu")
    x = np.column_stack([np.log(x[n][:, 0]) for n in b.names])  # Gaussian under Black-Scholes: the input correlation
    assert np.allclose(np.corrcoef(x.T), b.corr, atol=0.01)
    nat = native.simulate(b, None, [1.0], 200_000, seed=9, backend="cpu")
    ref = simulate_basket(b, [1.0], 200_000, 9)
    worst = lambda p: np.min(np.column_stack([p[n][:, 0] for n in b.names]), axis=1)  # noqa: E731
    a, sa = calls(worst(ref), 1.0, [90.0])
    c, sc = calls(worst(nat), 1.0, [90.0])
    assert abs(a[0] - c[0]) < 4 * np.hypot(sa[0], sc[0])


@needs_engine
def test_local_correlation_keeps_each_marginal_and_moves_the_worst_of():
    mixer = LocalCorrelation(sigma=0.15, level=0.0, down=0.3, up=0.1)
    flat, skewed = basket(), basket(mixer)
    a = native.simulate(flat, None, [1.0], 200_000, seed=10, backend="cpu")
    b = native.simulate(skewed, None, [1.0], 200_000, seed=10, backend="cpu")
    ref = simulate_basket(skewed, [1.0], 200_000, 11)
    for n in flat.names:  # each name keeps its law
        pa, sa = calls(a[n][:, 0], 1.0)
        pb, sb = calls(b[n][:, 0], 1.0)
        assert np.all(np.abs(pa - pb) < 4 * np.hypot(sa, sb))
    worst = lambda p: np.min(np.column_stack([p[n][:, 0] for n in flat.names]), axis=1)  # noqa: E731
    pw, sw = calls(worst(b), 1.0, [90.0])
    pr, sr = calls(worst(ref), 1.0, [90.0])
    assert abs(pw[0] - pr[0]) < 4 * np.hypot(sw[0], sr[0])  # the NumPy mixer on other draws


@needs_engine
def test_what_the_engine_does_not_cover_goes_to_numpy_or_fails_when_asked_by_name():
    b = basket(StochasticMix())
    assert native.choose("auto", b) == ("numpy", "StochasticMix is not in the path engine (local correlation only)")
    with pytest.raises(RuntimeError, match="StochasticMix"):
        native.choose("cpu", b)
    assert native.choose("numpy", b) == ("numpy", None)
    with pytest.raises(ValueError, match="engine"):
        native.choose("cuda", b)


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_engine_leverage_reprices_the_surface_like_numpys(backend):
    from optlab.core.slv import calibrate_leverage

    T = 1.0
    kw = dict(mix=0.5, n_particles=200_000, rng=1)
    ours = calibrate_leverage(CTX.heston, CTX.surface, CTX.market, T, engine=backend, **kw)
    ref = calibrate_leverage(CTX.heston, CTX.surface, CTX.market, T, engine="numpy", **kw)
    F, D = float(CTX.market.F(T)), float(CTX.market.D(T))
    target = black_price(F, K, T, CTX.surface.iv(np.log(K / F), T), D)
    gaps = []
    for slv in (ours, ref):  # the same paths for both: only the leverage differs
        p, se = calls(native.simulate(slv, CTX.market, [T], 400_000, seed=12, backend="cpu")[:, 0], D)
        gaps.append(p - target)
        assert np.all(np.abs(p - target) < 4 * se + 0.03)
    assert np.all(np.abs(gaps[0] - gaps[1]) < 0.02)  # the two calibrations miss the surface by the same amounts


def test_numpy_stays_the_default_of_the_leverage():
    from optlab.core.slv import calibrate_leverage

    a = calibrate_leverage(CTX.heston, CTX.surface, CTX.market, 0.2, n_particles=20_000, rng=2)
    b = calibrate_leverage(CTX.heston, CTX.surface, CTX.market, 0.2, n_particles=20_000, rng=2, engine="numpy")
    assert np.array_equal(a.leverage.values, b.leverage.values)


@needs_engine
@pytest.mark.parametrize("name", ["barrier_reverse_convertible", "phoenix"])
def test_daily_windows_reduced_in_the_engine_give_the_same_numbers(name):
    sheet = preset(name, **({"downside.observation": "daily"} if name == "phoenix" else {}))
    product = compile_sheet(sheet, CTX.valuation)
    model = CTX.model("lv", product.horizon)
    sets = [{product.coupon_param: 0.02}] if product.coupon_param else [{}]
    kw = dict(
        n_paths=40_000,
        seed=13,
        valuation=CTX.valuation,
        calendar=product.calendar,
        engine="cpu",
        spot_factors=[1.01] * len(sets),
    )
    (full,) = price_many(product.program, model, CTX.market, sets, reduce=False, **kw)
    (red,) = price_many(product.program, model, CTX.market, sets, **kw)
    assert red.price == pytest.approx(full.price, abs=1e-12) and red.stderr == pytest.approx(full.stderr, abs=1e-12)
    assert (
        red.timings["windows"] == 1 and red.timings["columns"] < full.timings["columns"] / 10
    )  # two columns, not one a day
