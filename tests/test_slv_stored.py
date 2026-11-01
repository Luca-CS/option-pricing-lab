"""The SLV model loaded from results/ is the one that was validated: every time step of the leverage is stored."""

import numpy as np

from optlab.structured import PricingContext
from optlab.validation import mc_smile


def test_stored_leverage_has_every_calibration_step():
    ctx = PricingContext.from_calibration()
    dt = np.diff(ctx.slv.leverage.times) * 365
    assert len(ctx.slv.leverage.times) > 300 and dt.max() < 4.0  # was 114 steps, 8.5 days apart


def test_stored_slv_reprices_three_month_vanillas():
    ctx = PricingContext.from_calibration()
    T = 0.25
    F = float(ctx.market.F(T))
    strikes = np.array([0.85, 1.0]) * F
    iv, se = mc_smile(ctx.slv, ctx.market, T, strikes, n_paths=1_000_000, rng=777)
    err = (iv - ctx.surface.iv(np.log(strikes / F), T)) * 100
    assert abs(err.mean()) < 0.10, (err, se * 100)  # 0.185 vol point with the decimated leverage
