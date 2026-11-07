"""simulate applies each step tile by tile: the paths must equal the untiled loop bit for bit."""

import numpy as np
import pytest
from optlab.core import BlackScholesModel, HestonModel, HestonParams, Market, simulate
from optlab.structured import PricingContext

SPX = PricingContext.from_calibration()
MODELS = {
    "bs": BlackScholesModel(0.2),
    "heston": HestonModel(HestonParams(0.04, 1.5, 0.04, 0.5, -0.7)),
    "lv": SPX.model("lv"),
    "slv": SPX.model("slv"),
}


@pytest.mark.parametrize("name", list(MODELS))
@pytest.mark.parametrize("n_paths, tile", [(6_000, 1_000), (5_001, 700), (2_000, 25_000), (12_000, 4_096)])
def test_tiles_do_not_change_a_bit(name, n_paths, tile):
    market = SPX.market if name in ("lv", "slv") else Market.flat(100.0, 0.03, 0.01)
    times = [0.1, 0.5, 1.0]
    a = simulate(MODELS[name], market, times, n_paths, rng=9, tile=None)
    b = simulate(MODELS[name], market, times, n_paths, rng=9, tile=tile)
    assert np.array_equal(a, b)


@pytest.mark.parametrize("name", ["heston", "slv"])
def test_tiles_with_variance_output_and_without_antithetics(name):
    market = SPX.market if name == "slv" else Market.flat(100.0, 0.03, 0.01)
    for kw in ({"return_variance": True}, {"antithetic": False}):
        a = simulate(MODELS[name], market, [0.25, 0.75], 3_000, rng=4, tile=None, **kw)
        b = simulate(MODELS[name], market, [0.25, 0.75], 3_000, rng=4, tile=512, **kw)
        for x, y in zip(a if isinstance(a, tuple) else (a,), b if isinstance(b, tuple) else (b,)):
            assert np.array_equal(x, y)
