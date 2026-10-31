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
