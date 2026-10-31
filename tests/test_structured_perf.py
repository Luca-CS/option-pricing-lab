"""Performance paths: each one must give the same numbers as the reference path (bit for bit, or within 4 SE for
the options that change the estimator)."""

import json

import numpy as np
import pytest

from optlab.models import LocalVolModel
from optlab.structured import PricingContext, compile_sheet, preset, price
from optlab.structured.context import DEFAULT_CALIBRATION
from optlab.structured.surfaces import FastSVISurface
from optlab.svi import SVISurface

SPX = PricingContext.from_calibration()
SURFACE = json.loads(DEFAULT_CALIBRATION.read_text())["surface"]


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
    p = compile_sheet(preset("phoenix", **{"downside.observation": "daily", "general.maturity_months": 12}),
                      SPX.valuation)
    slow = SVISurface.from_dict(SURFACE)
    a = price(p.program, LocalVolModel(slow), SPX.market, n_paths=4_000, seed=3, valuation=SPX.valuation)
    b = price(p.program, LocalVolModel(SPX.surface), SPX.market, n_paths=4_000, seed=3, valuation=SPX.valuation)
    assert a.value.price == b.value.price and a.value.stderr == b.value.stderr
    assert pytest.approx(a.price) == b.price


# --- batches on a process pool, Greeks on one simulation ---

def test_process_pool_gives_the_same_numbers_bit_for_bit():
    from optlab.structured import engine
    from optlab.structured.portfolio import Book, Position
    from optlab.structured.pricer import price_many
    p = compile_sheet(preset("phoenix", **{"general.maturity_months": 12}), SPX.valuation)
    kw = dict(n_paths=40_000, seed=7, batch=10_000, valuation=SPX.valuation)
    seq = price_many(p.program, SPX.model("slv"), SPX.market, [{}, {"cpn": 0.03}], **kw)
    par = price_many(p.program, SPX.model("slv"), SPX.market, [{}, {"cpn": 0.03}], workers=2, **kw)
    for a, b in zip(seq, par):
        assert np.array_equal(a.pairs, b.pairs) and np.array_equal(a.termination, b.termination)
        assert a.expected_flows == b.expected_flows
    assert par[0].timings["workers"] == 2
    book = Book([Position("a", preset("phoenix", **{"general.maturity_months": 12}), 2.0),
                 Position("b", preset("barrier_reverse_convertible", **{"wrapper.solve": "upfront"}), -1.0)])
    r1 = book.price(SPX, model="lv", n_paths=20_000, batch=10_000)
    r2 = book.price(SPX, model="lv", n_paths=20_000, batch=10_000, workers=2)
    assert np.array_equal(r1.pairs, r2.pairs) and r1.value == r2.value
    engine.shutdown_pool()


def test_scaled_paths_are_the_bumped_market_paths():
    from optlab.models import simulate
    times = [0.25, 0.5, 1.0]
    for name in ("bs", "heston", "lv", "slv"):
        m = SPX.model(name, 1.0)
        base = simulate(m, SPX.market, times, 2_000, np.random.default_rng(1))
        bumped = simulate(m, SPX.market.scaled(101.0), times, 2_000, np.random.default_rng(1))
        assert np.max(np.abs(bumped / (1.01 * base) - 1)) < 1e-13


def test_one_simulation_delta_equals_the_bumped_market_delta():
    from optlab.structured.pricer import price_many
    p = compile_sheet(preset("phoenix", **{"general.maturity_months": 12}), SPX.valuation)
    m, kw = SPX.model("lv"), dict(n_paths=10_000, seed=4, batch=5_000, valuation=SPX.valuation, smoothing=0.005,
                                  initial_fixing=100.0)
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
            assert np.array_equal(sc.extreme("SPX", window, func), ref(sc.fixings("SPX", window), axis=1))


def test_daily_knock_in_prices_do_not_move_with_the_window_shortcut(monkeypatch):
    from optlab.structured.pricer import price_many
    from optlab.structured.scenario import Scenario
    p = compile_sheet(preset("barrier_reverse_convertible", **{"wrapper.solve": "upfront"}), SPX.valuation)
    kw = dict(n_paths=20_000, seed=3, batch=10_000, valuation=SPX.valuation)
    fast = price_many(p.program, SPX.model("bs"), SPX.market, [{}], **kw)[0]
    monkeypatch.delattr(Scenario, "extreme")  # the evaluator falls back to fixings(...).min(axis=1)
    slow = price_many(p.program, SPX.model("bs"), SPX.market, [{}], **kw)[0]
    assert np.array_equal(fast.pairs, slow.pairs)


# --- forward control variates (opt-in) ---

@pytest.mark.parametrize("model", ["lv", "slv"])
def test_forward_controls_reduce_the_variance_without_moving_the_price(model):
    from optlab.structured.pricer import price_many
    p = compile_sheet(preset("phoenix"), SPX.valuation)
    kw = dict(n_paths=60_000, seed=8, batch=20_000, valuation=SPX.valuation)
    plain = price_many(p.program, SPX.model(model), SPX.market, [{}], **kw)[0]
    ctrl = price_many(p.program, SPX.model(model), SPX.market, [{}], control_forwards=True, **kw)[0]
    assert ctrl.timings["variance_ratio"] > 1.2
    assert ctrl.stderr < plain.stderr
    assert abs(ctrl.price - plain.price) <= 4 * plain.stderr


def test_the_coupon_solver_stays_exact_with_forward_controls():
    from optlab.structured.pricer import price_many
    from optlab.structured.quote import quote
    q = quote(preset("phoenix", **{"general.maturity_months": 12}), SPX, control=None, n_paths=40_000,
              batch=10_000, control_forwards=True)
    cpn = q.coupon_rate * q.product.coupon_scale
    again = price_many(q.product.program, SPX.model("slv"), SPX.market, [{"cpn": cpn}], n_paths=40_000,
                       seed=q.decision.seed, batch=10_000, valuation=SPX.valuation, control_forwards=True)[0]
    assert again.price == pytest.approx(98.0, abs=1e-8)
