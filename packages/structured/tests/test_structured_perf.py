"""Performance paths: each one must give the same numbers as the reference path (bit for bit, or within 4 SE for
the options that change the estimator)."""

import json
from typing import Any, cast

import numpy as np
import pytest
from _paths import SPX_CALIBRATION
from _quotes import some

from optlab.core.models import Leverage, LocalVolModel
from optlab.core.svi import SVISurface
from optlab.structured import PricingContext, compile_sheet, preset, price
from optlab.structured.surfaces import FastSVISurface

SPX = PricingContext.from_calibration(SPX_CALIBRATION)
SURFACE = json.loads(SPX_CALIBRATION.read_text())["surface"]


def test_fast_surface_derivatives_are_bit_identical():
    base, fast = SVISurface.from_dict(SURFACE), FastSVISurface.from_dict(SURFACE)
    k = np.linspace(-3, 1.5, 2001)
    times = [*base.maturities, 1 / 365, 0.3, 1.2, 2.5, *np.linspace(0.003, 2.4, 40)]
    for T in times:
        for a, b in zip(base.derivatives(k, T), fast.derivatives(k, T)):
            assert np.array_equal(a, b)
        assert np.array_equal(base.local_var(k, T), fast.local_var(k, T))
    T = np.full_like(k, 0.7)  # array times take the general branch
    for a, b in zip(base.derivatives(k, T), fast.derivatives(k, T)):
        assert np.array_equal(a, b)


def test_local_vol_prices_do_not_move_with_the_fast_surface():
    assert isinstance(SPX.surface, FastSVISurface)
    p = compile_sheet(
        preset("phoenix", **{"downside.observation": "daily", "general.maturity_months": 12}), SPX.valuation
    )
    slow = SVISurface.from_dict(SURFACE)
    a = price(p.program, LocalVolModel(slow), SPX.market, n_paths=4_000, seed=3, valuation=SPX.valuation)
    b = price(p.program, LocalVolModel(SPX.surface), SPX.market, n_paths=4_000, seed=3, valuation=SPX.valuation)
    assert a.value.price == b.value.price and a.value.stderr == b.value.stderr
    assert pytest.approx(a.price) == b.price


# --- batches on a process pool, Greeks on one simulation ---


@pytest.mark.slow
def test_process_pool_gives_the_same_numbers_bit_for_bit():
    from optlab.structured import engine
    from optlab.structured.portfolio import Book, Position
    from optlab.structured.pricer import price_many

    p = compile_sheet(preset("phoenix", **{"general.maturity_months": 12}), SPX.valuation)
    kw: dict[str, Any] = dict(n_paths=40_000, seed=7, batch=10_000, valuation=SPX.valuation)
    seq = price_many(p.program, SPX.model("slv"), SPX.market, [{}, {"cpn": 0.03}], **kw)
    par = price_many(p.program, SPX.model("slv"), SPX.market, [{}, {"cpn": 0.03}], workers=2, **kw)
    for a, b in zip(seq, par):
        assert np.array_equal(some(a.pairs), some(b.pairs)) and np.array_equal(some(a.termination), some(b.termination))
        assert a.expected_flows == b.expected_flows
    assert par[0].timings["workers"] == 2
    book = Book(
        [
            Position("a", preset("phoenix", **{"general.maturity_months": 12}), 2.0),
            Position("b", preset("barrier_reverse_convertible", **{"wrapper.solve": "upfront"}), -1.0),
        ]
    )
    r1 = book.price(SPX, model="lv", n_paths=20_000, batch=10_000)
    r2 = book.price(SPX, model="lv", n_paths=20_000, batch=10_000, workers=2)
    assert np.array_equal(some(r1.pairs), some(r2.pairs)) and r1.value == r2.value
    engine.shutdown_pool()


def test_scaled_paths_are_the_bumped_market_paths():
    from optlab.core.models import simulate

    times = [0.25, 0.5, 1.0]
    for name in ("bs", "heston", "lv", "slv"):
        m = SPX.model(name, 1.0)
        base = simulate(m, SPX.market, times, 2_000, np.random.default_rng(1))
        bumped = simulate(m, SPX.market.scaled(101.0), times, 2_000, np.random.default_rng(1))
        assert np.max(np.abs(bumped / (1.01 * base) - 1)) < 1e-13


def test_one_simulation_delta_equals_the_bumped_market_delta():
    from optlab.structured.pricer import price_many

    p = compile_sheet(preset("phoenix", **{"general.maturity_months": 12}), SPX.valuation)
    kw: dict[str, Any]
    m, kw = (
        SPX.model("lv"),
        dict(n_paths=10_000, seed=4, batch=5_000, valuation=SPX.valuation, smoothing=0.005, initial_fixing=100.0),
    )
    up, down = price_many(p.program, m, SPX.market, [{}, {}], spot_factors=[1.01, 0.99], **kw)
    up_m = price_many(p.program, m, SPX.market.scaled(101.0), [{}], **kw)[0]
    down_m = price_many(p.program, m, SPX.market.scaled(99.0), [{}], **kw)[0]
    assert (up.price - down.price) == pytest.approx(up_m.price - down_m.price, abs=1e-10)


def test_window_extremes_without_copies_are_bit_identical():
    from optlab.structured.scenario import Scenario

    rng = np.random.default_rng(2)
    times = np.linspace(0.01, 1.0, 100)
    paths = 100 * np.exp(0.2 * rng.standard_normal((50, 100)).cumsum(axis=1) * 0.1)
    sc = Scenario("SPX", times, paths, 100.0, {-0.5: 90.0, -0.2: 130.0})
    for window in (times[10:60], times[::3], np.r_[-0.5, -0.2, 0.0, times[:40]], np.r_[-0.5, 0.0]):
        for func, ref in (("min", np.min), ("max", np.max)):
            assert np.array_equal(some(sc.extreme("SPX", window, func)), ref(sc.fixings("SPX", window), axis=1))


def test_daily_knock_in_prices_do_not_move_with_the_window_shortcut(monkeypatch):
    from optlab.structured.pricer import price_many
    from optlab.structured.scenario import Scenario

    p = compile_sheet(preset("barrier_reverse_convertible", **{"wrapper.solve": "upfront"}), SPX.valuation)
    kw: dict[str, Any] = dict(n_paths=20_000, seed=3, batch=10_000, valuation=SPX.valuation)
    fast = price_many(p.program, SPX.model("bs"), SPX.market, [{}], **kw)[0]
    monkeypatch.delattr(Scenario, "extreme")  # the evaluator falls back to fixings(...).min(axis=1)
    slow = price_many(p.program, SPX.model("bs"), SPX.market, [{}], **kw)[0]
    assert np.array_equal(some(fast.pairs), some(slow.pairs))


# --- forward control variates (opt-in) ---


@pytest.mark.slow
@pytest.mark.parametrize("model", ["lv", "slv"])
def test_forward_controls_reduce_the_variance_without_moving_the_price(model):
    from optlab.structured.pricer import price_many

    p = compile_sheet(preset("phoenix"), SPX.valuation)
    kw: dict[str, Any] = dict(n_paths=60_000, seed=8, batch=20_000, valuation=SPX.valuation)
    plain = price_many(p.program, SPX.model(model), SPX.market, [{}], **kw)[0]
    ctrl = price_many(p.program, SPX.model(model), SPX.market, [{}], control_forwards=True, **kw)[0]
    assert ctrl.timings["variance_ratio"] > 1.2
    assert ctrl.stderr < plain.stderr
    assert abs(ctrl.price - plain.price) <= 4 * plain.stderr


def test_the_coupon_solver_stays_exact_with_forward_controls():
    from optlab.structured.pricer import price_many
    from optlab.structured.quote import quote

    q = quote(
        preset("phoenix", **{"general.maturity_months": 12}),
        SPX,
        model="slv",
        control=None,
        n_paths=40_000,
        batch=10_000,
        control_forwards=True,
    )
    cpn = some(q.coupon_rate) * q.product.coupon_scale
    again = price_many(
        q.product.program,
        SPX.model("slv"),
        SPX.market,
        [{"cpn": cpn}],
        n_paths=40_000,
        seed=q.decision.seed,
        batch=10_000,
        valuation=SPX.valuation,
        control_forwards=True,
    )[0]
    assert again.price == pytest.approx(98.0, abs=1e-8)


# --- randomised quasi-Monte Carlo (opt-in) ---


def test_brownian_bridge_increments_are_independent_standard_normals():
    from optlab.structured.qmc import bridge_normals, bridge_order

    rng = np.random.default_rng(0)
    t = np.r_[0, np.cumsum(rng.uniform(0.001, 0.01, 37))]
    assert sorted(i for i, _, _ in bridge_order(t)) == list(range(1, 38))
    inc = bridge_normals(rng.standard_normal((100_000, 37)), t)
    assert abs(inc.mean()) < 0.01 and abs(inc.std() - 1) < 0.01
    assert np.abs(np.corrcoef(inc[:, :5].T) - np.eye(5)).max() < 0.02


@pytest.mark.parametrize("model", ["bs", "lv"])
def test_rqmc_agrees_with_monte_carlo_and_cuts_the_variance(model):
    from optlab.structured.pricer import price_many
    from optlab.structured.qmc import price_rqmc

    p = compile_sheet(preset("phoenix", **{"general.maturity_months": 12}), SPX.valuation)
    m = SPX.model(model, 1.0)
    q = price_rqmc(p.program, m, SPX.market, m=11, replications=8, seed=5, valuation=SPX.valuation)
    mc = price_many(p.program, m, SPX.market, [{}], n_paths=q.n_paths, seed=5, batch=8_192, valuation=SPX.valuation)[0]
    assert abs(q.price - mc.price) <= 4 * np.hypot(q.stderr, mc.stderr)
    assert (mc.stderr / q.stderr) ** 2 > 3
    assert q.method == "rqmc" and q.n_paths == 8 * 2**12


def test_rqmc_needs_a_power_of_two():
    from optlab.structured.qmc import SobolNormals

    with pytest.raises(ValueError, match="power of two"):
        SobolNormals(1000, np.linspace(0, 1, 5), 1, 0)


# --- grid-interpolated models (opt-in) ---


def test_uniform_leverage_and_tabulated_local_vol_interpolate_closely():
    from optlab.structured.fastmodels import TabulatedLocalVol, UniformLeverage

    lev, ul = cast(Leverage, SPX.slv.leverage), UniformLeverage(SPX.slv.leverage)
    for i in range(0, len(lev.times), 10):
        xs = np.linspace(lev.x_grids[i][0] - 0.1, lev.x_grids[i][-1] + 0.1, 2000)
        assert np.max(np.abs(ul(lev.times[i], xs) / lev(lev.times[i], xs) - 1)) < 1e-3
    tab = TabulatedLocalVol(SPX.surface)
    for t in (0.02, 0.5, 1.5):
        sd = float(np.sqrt(SPX.surface.w(0.0, t)))
        xs = np.linspace(-4 * sd, 3 * sd, 2001)
        assert np.max(np.abs(tab.inst_var(t, xs, None) / SPX.surface.local_var(xs, t) - 1)) < 2e-4


@pytest.mark.parametrize("model", ["lv", "slv"])
def test_fast_models_price_within_a_small_fraction_of_the_error(model):
    from optlab.structured.pricer import price_many

    p = compile_sheet(preset("phoenix", **{"general.maturity_months": 12}), SPX.valuation)
    kw: dict[str, Any] = dict(n_paths=40_000, seed=12, batch=10_000, valuation=SPX.valuation)
    ref = price_many(p.program, SPX.model(model), SPX.market, [{}], **kw)[0]
    fast = price_many(p.program, SPX.model(model, fast=True), SPX.market, [{}], **kw)[0]
    assert abs(fast.price - ref.price) < 0.25 * ref.stderr
