"""SLV on a basket: each name's leverage from its own marginal, vanillas per name, identical names, common random numbers with local vol, the provision."""

from dataclasses import replace

import numpy as np
import pytest

from optlab.blackscholes import black_price
from optlab.market import Market
from optlab.models import LocalVolModel, simulate
from optlab.structured import PricingContext, preset, quote
from optlab.structured.costs import CostInputs
from optlab.structured.multiasset import Basket, BasketContext, simulate_basket

SPX = PricingContext.from_calibration()
TIMES = np.array([0.25, 0.5, 1.0])


def scaled_surface():
    """A second name: the SPX surface with its volatilities a fifth higher."""
    from optlab.structured.surfaces import BumpedSurface

    return BumpedSurface(SPX.surface, 0.04)


@pytest.fixture(scope="module")
def ctx():
    market = Market.flat(100.0, 0.03, 0.01)
    return BasketContext(
        ("SPX", "AAA"),
        {"SPX": SPX.market, "AAA": market},
        {"SPX": SPX.surface, "AAA": scaled_surface()},
        np.array([[1.0, 0.7], [0.7, 1.0]]),
        SPX.valuation,
        {"SPX": SPX},
    )


def black_call(market, surface, K, T):
    F = float(market.F(T))
    vol = float(surface.iv(np.log(K / F), T))
    return float(black_price(F, K, T, vol, 1.0, "call"))


@pytest.mark.slow
def test_each_name_reprices_its_vanillas_within_four_standard_errors(ctx):
    paths = simulate_basket(ctx.model("slv", 1.0), TIMES, 400_000, 7)
    for name, surface in (("SPX", SPX.surface), ("AAA", ctx.vols["AAA"])):
        market = ctx.markets[name]
        for j, T in enumerate(TIMES):
            s = paths[name][:, j]
            half = len(s) // 2
            F = float(market.F(T))
            assert s.mean() == pytest.approx(F, rel=2e-3)
            for K in (0.95 * F, F, 1.05 * F):
                pay = np.maximum(s - K, 0.0)
                pay = 0.5 * (pay[:half] + pay[half:])
                se = pay.std(ddof=1) / np.sqrt(half)
                want = black_call(market, surface, K, T)
                # within the error and a bias of the particle leverage of about 5 bp of vol at one year (reports/pricing.md)
                assert abs(pay.mean() - want) <= 4 * se + 0.02, (name, T, K, pay.mean(), want, se)


def test_identical_names_at_a_correlation_of_one_have_the_same_paths_and_the_single_name_price():
    two = BasketContext(
        ("SPX", "BBB"),
        {"SPX": SPX.market, "BBB": SPX.market},
        {"SPX": SPX.surface, "BBB": SPX.surface},
        np.ones((2, 2)),
        SPX.valuation,
        {"SPX": SPX, "BBB": SPX},
    )
    paths = simulate_basket(two.model("slv", 2.0), TIMES, 20_000, 3)
    assert np.allclose(paths["SPX"], paths["BBB"], rtol=1e-12)
    independent = simulate_basket(two.model("slv", 2.0, vol_corr="independent"), TIMES, 20_000, 3)
    assert not np.allclose(
        independent["SPX"], independent["BBB"], rtol=1e-3
    )  # the variances differ: why the residuals share the correlation
    sheet = preset("phoenix", **{"general.maturity_months": 12})
    wo = preset(
        "phoenix",
        **{
            "general.maturity_months": 12,
            "general.basket": "worst_of",
            "general.underlyings": ["SPX", "BBB"],
            "wrapper.solve": "upfront",
            "coupon.rate": 0.06,
        },
    )
    a = quote(wo, two, model="slv", control=None, n_paths=100_000, batch=25_000)
    b = quote(
        sheet.replace(**{"wrapper.solve": "upfront", "coupon.rate": 0.06}),
        SPX,
        model="slv",
        control=None,
        n_paths=100_000,
        batch=25_000,
    )
    assert abs(a.result.price - b.result.price) <= 4 * np.hypot(a.result.stderr, b.result.stderr)


def test_one_name_as_a_basket_is_the_same_law_as_the_single_name():
    one = Basket(("SPX",), {"SPX": replace(SPX.slv, spot_driven=True)}, {"SPX": SPX.market}, np.ones((1, 1)))
    alone = simulate(replace(SPX.slv, spot_driven=True), SPX.market, TIMES, 40_000, 5)
    together = simulate_basket(one, TIMES, 40_000, 5)["SPX"]
    assert abs(together[:, -1].mean() - alone[:, -1].mean()) < 0.5
    assert abs(together[:, -1].std() - alone[:, -1].std()) < 0.5


@pytest.mark.slow
def test_the_spot_draws_are_those_of_local_vol(ctx):
    lv = simulate_basket(ctx.model("lv", 1.0), TIMES, 40_000, 9)
    driven = simulate_basket(ctx.model("slv", 1.0, spot_driven=True), TIMES, 40_000, 9)
    plain = simulate_basket(ctx.model("slv", 1.0), TIMES, 40_000, 9)
    corr = float(np.corrcoef(np.log(driven["SPX"][:, -1]), np.log(lv["SPX"][:, -1]))[0, 1])
    assert corr > 0.8 and float(np.corrcoef(np.log(plain["SPX"][:, -1]), np.log(lv["SPX"][:, -1]))[0, 1]) < corr - 0.3
    again = simulate_basket(ctx.model("lv", 1.0), TIMES, 40_000, 9)
    assert np.array_equal(lv["SPX"], again["SPX"])  # local vol takes no other draw


@pytest.mark.slow
def test_the_worst_of_has_its_slv_provision_on_common_numbers(ctx):
    s = preset(
        "phoenix",
        **{"general.basket": "worst_of", "general.underlyings": ["SPX", "AAA"], "general.maturity_months": 12},
    )
    q = quote(s, ctx, n_paths=40_000, batch=10_000, costs=CostInputs(mix=0.5))
    line = q.desk.line("slv")
    assert (
        q.decision.model == "lv"
        and q.decision.provisions == ("slv", "local_corr")
        and line.kind == "model"
        and line.used == 0.5
    )
    assert q.desk.final_price == pytest.approx(98.0, abs=1e-9)
    assert q.control.price - q.result.price == pytest.approx(line.gap, abs=1e-9)
    assert line.se < 0.8 * np.hypot(q.control.stderr, q.result.stderr)
    assert (
        ctx.name_context("AAA").leverage_flat and ctx.name_context("AAA").slv_cache
    )  # one leverage kept per name, here the one calibrated on first use
    keys = set(ctx.name_context("AAA").slv_cache)
    quote(s, ctx, n_paths=4000, batch=4000, costs=CostInputs(mix=0.5))
    assert set(ctx.name_context("AAA").slv_cache) == keys


def test_a_flat_name_stays_black_scholes_under_slv():
    market = Market.flat(100.0, 0.03, 0.01)
    c = BasketContext(
        ("SPX", "FLAT"),
        {"SPX": SPX.market, "FLAT": market},
        {"SPX": SPX.surface, "FLAT": 0.25},
        np.eye(2),
        SPX.valuation,
        {"SPX": SPX},
    )
    m = c.model("slv", 1.0)
    from optlab.models import BlackScholesModel, SLVModel

    assert isinstance(m.models["FLAT"], BlackScholesModel) and isinstance(m.models["SPX"], SLVModel)


def test_local_vol_stays_what_it_was_for_a_single_name():
    assert isinstance(SPX.model("lv"), LocalVolModel)


def test_an_unknown_vol_correlation_is_refused(ctx):
    with pytest.raises(ValueError, match="vol_corr"):
        Basket(("SPX",), {"SPX": SPX.slv}, {"SPX": SPX.market}, np.ones((1, 1)), "half")
