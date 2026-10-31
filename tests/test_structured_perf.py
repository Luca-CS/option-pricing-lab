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
