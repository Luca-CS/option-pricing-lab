"""Issuer callables under stochastic rates: the hybrid's deflated payments, the deterministic limit, and the issuer's
call on a note on rates alone against a Hull-White PDE (the policy at most 4 SE plus 2 bp above it, never below)."""

import numpy as np
import pytest
from _callable_refs import hw_callable_bond
from _structured_refs import FLAT, VOL

from optlab.core import BlackScholesModel
from optlab.core.rates import HullWhite1F, HybridModel
from optlab.structured.compiler import compile_sheet
from optlab.structured.presets import preset
from optlab.structured.pricer import price_many
from optlab.structured.script.nodes import IssuerCall
from optlab.structured.script.timeline import Clock, build_timeline

VALUATION = "2026-10-27"
HW = HullWhite1F(0.03, 0.008)


def product_of(name, **changes):
    sheet = preset(name, **{"general.pay_lag": 0, "issuer_call.notice_days": 0, "wrapper.solve": "upfront", **changes})
    return compile_sheet(sheet, VALUATION)


def run(product, model, seed=3, n=100_000):
    (res,) = price_many(
        product.program, model, FLAT, [{}], n_paths=n, seed=seed, valuation=product.valuation, calendar=product.calendar
    )
    return res


def test_rates_of_no_volatility_give_the_deterministic_price():
    """sigma_r of 1e-6 through the hybrid's code: the deflated flows, the policy with the rate factor in its state."""
    product = product_of("callable_phoenix")
    base = run(product, BlackScholesModel(VOL))
    hybrid = run(product, HybridModel(BlackScholesModel(VOL), HullWhite1F(0.03, 1e-6), 0.0))
    assert hybrid.pairs is not None and base.pairs is not None  # noqa: S101 (narrows the type: Monte Carlo keeps its pairs)
    paired = (hybrid.pairs - base.pairs) / product.sheet.get("general.notional") * 100
    se = paired.std(ddof=1) / np.sqrt(len(paired))
    assert abs(hybrid.price - base.price) <= 4 * se + 1e-3, (hybrid.price, base.price, se)


def test_the_fixed_coupon_note_s_call_against_the_hull_white_pde():
    """A 5-year note paying a fixed yearly coupon above the curve, callable at par every year from one year: under
    deterministic curves the issuer calls at the first date; the rates' volatility adds to its option."""
    product = product_of("callable_fixed_coupon_note", **{"coupon.rate": 0.045})
    timeline = build_timeline(product.program, Clock(product.valuation, product.calendar))
    times = [t for t in timeline.event_times if t > 0]
    T = times[-1]
    coupons = [(t, 0.045) for t in times]
    calls = {
        t: 1.0
        for ev, t in zip(product.program.events, timeline.event_times)
        if any(isinstance(s, IssuerCall) for s in ev.body)
    }
    ref = 100 * hw_callable_bond(coupons, calls, T, FLAT, HW.a, HW.sigma)
    plain = 100 * hw_callable_bond(coupons, {}, T, FLAT, HW.a, HW.sigma)
    det = run(product, BlackScholesModel(VOL))
    res = run(product, HybridModel(BlackScholesModel(VOL), HW, 0.0))
    assert ref - 4 * res.stderr <= res.price <= ref + 4 * res.stderr + 0.02, (res.price, ref, res.stderr)
    assert plain - ref > 0.3  # the issuer's option on the rates
    assert det.price > ref + 0.3  # deterministic curves miss the option on the rates' moves (0.80 here)


def test_a_funded_swap_under_stochastic_rates_is_refused():
    product = product_of("callable_reverse_convertible")
    from optlab.structured.cashflows import FundedSwap

    fmt = FundedSwap(0.0, ((0.0, 1.0, 1.0, 1.0),), tuple(range(len(product.program.events))))
    with pytest.raises(ValueError, match="funded swap under stochastic rates"):
        price_many(
            product.program,
            HybridModel(BlackScholesModel(VOL), HW, 0.0),
            FLAT,
            [{}],
            fmt=fmt,
            n_paths=2_000,
            valuation=product.valuation,
            calendar=product.calendar,
        )


@pytest.mark.slow
def test_the_stochastic_rates_gap_is_small_and_rises_with_the_correlation():
    """The provision's gap (hybrid less local vol, on the base's spot normals, each on its own policy): under 0.3 % of
    notional at 80 bp, and higher at rho +0.4 than at -0.4 by more than 4 paired SEs."""
    from _structured_refs import flat_context

    product = product_of("callable_phoenix")
    ctx = flat_context()
    base = run(product, ctx.model("lv", 3.0), seed=21, n=60_000)
    gaps = {}
    for rho in (0.4, -0.4):
        ctx.rates = {"sigma": 0.008, "a": 0.03, "rho": rho, "particles": 40_000}
        hybrid = run(product, ctx.rates_model(3.0), seed=21, n=60_000)
        assert hybrid.pairs is not None and base.pairs is not None  # noqa: S101 (narrows the type: Monte Carlo keeps its pairs)
        gaps[rho] = (hybrid.pairs - base.pairs) / 100 * 100
    for g in gaps.values():
        assert abs(g.mean()) < 0.3
    diff = gaps[0.4] - gaps[-0.4]
    assert diff.mean() > 4 * diff.std(ddof=1) / np.sqrt(len(diff)), (gaps[0.4].mean(), gaps[-0.4].mean())


def test_a_note_on_the_rates_alone_is_priced_on_the_hybrid_when_the_market_has_one():
    """A fixed-coupon callable pays on the rates alone (its state reads the spot, its payments do not): the hybrid is
    its base where the market holds a rates model; a Phoenix keeps local vol and its provisions."""
    from optlab.structured.policy import decide
    from optlab.structured.profile import profile

    def prof(name):
        product = product_of(name)
        return profile(product.program, build_timeline(product.program, Clock(product.valuation, product.calendar)))

    fcn, phoenix = prof("callable_fixed_coupon_note"), prof("callable_phoenix")
    assert not fcn.pays_on_spot and phoenix.pays_on_spot
    assert decide(fcn, 5.0, has_rates=True).model == "hybrid"
    without = decide(fcn, 5.0, has_rates=False)
    assert without.model == "lv" and any("rates alone" in w for w in without.warnings)
    assert decide(phoenix, 5.0, has_rates=True).model == "lv"
    assert "rates" in decide(phoenix, 5.0, has_rates=True).provisions
    import datetime as dt

    from _paths import SPX_CALIBRATION
    from _structured_refs import flat_surface

    from optlab.structured.context import PricingContext
    from optlab.structured.quote import quote

    spx = PricingContext.from_calibration(SPX_CALIBRATION)
    ctx = PricingContext(FLAT, flat_surface(), spx.heston, spx.slv, dt.date.fromisoformat(VALUATION))
    ctx.rates = {"sigma": 0.008, "a": 0.03, "rho": 0.0, "particles": 20_000}

    q = quote(preset("callable_fixed_coupon_note"), ctx, n_paths=20_000, control=None)
    assert q.decision.model == "hybrid" and q.coupon_rate is not None
