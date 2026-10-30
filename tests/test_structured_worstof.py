"""Worst-of products on correlated underlyings."""

import datetime as dt

import numpy as np
import pytest
from _structured_refs import FLAT, R, VOL, autocall_reference, flat_context, worst_digital, worst_put

from optlab import BlackScholesModel, Market, european_mc
from optlab.structured.compiler import compile_sheet
from optlab.structured.multiasset import Basket, BasketContext, correlation_root, simulate_basket
from optlab.structured.presets import preset
from optlab.structured.pricer import price
from optlab.structured.quote import quote
from optlab.structured.schema import TermSheetError
from optlab.structured.script.timeline import Clock, build_timeline

VAL = dt.date(2026, 10, 27)
Q2, VOL2 = 0.03, 0.25
N_MC = 100_000


def context(rho, vol2=VOL2, q2=Q2):
    markets = {"SPX": FLAT, "SX5E": Market.flat(100.0, R, q2)}
    return BasketContext(("SPX", "SX5E"), markets, {"SPX": VOL, "SX5E": vol2}, np.array([[1, rho], [rho, 1]]), VAL)


def worst(name, **changes):
    return preset(name, **{"general.basket": "worst_of", "wrapper.solve": "upfront", **changes})


def mc(sheet, ctx, n_paths=N_MC, seed=13, batch=25_000):
    p = compile_sheet(sheet, VAL)
    basket = ctx.model("bs", p.horizon, p.program.underlying_names)
    return price(p.program, basket, ctx.market, n_paths=n_paths, seed=seed, batch=batch, valuation=VAL), p


def times(p):
    tl = build_timeline(p.program, Clock(VAL))
    return float(tl.event_times[-1]), [t for t in tl.pay_times if t is not None]


# --- simulation ---

def test_one_name_basket_reproduces_simulate_bit_for_bit():
    script = "underlying SPX\nparam K = 100\nevent t=1 pay t=1\n  pay max(SPX - K, 0) as redemption\nend\n"
    bs = BlackScholesModel(VOL)
    basket = Basket(("SPX",), {"SPX": bs}, {"SPX": FLAT}, [[1.0]])
    ref = european_mc(bs, FLAT, 100.0, 1.0, "call", n_paths=20_000, rng=5)
    res = price(script, basket, FLAT, n_paths=20_000, seed=5)
    assert res.value.price == ref.price and res.value.stderr == ref.stderr


def test_correlated_paths_have_the_input_correlation_and_forwards():
    ctx = context(0.6)
    paths = simulate_basket(ctx.model("bs", 1.0), [0.5, 1.0], 200_000, rng=3)
    lr = {n: np.log(p[:, 1] / p[:, 0]) for n, p in paths.items()}
    assert np.corrcoef(lr["SPX"], lr["SX5E"])[0, 1] == pytest.approx(0.6, abs=0.01)
    for n, p in paths.items():
        F = float(ctx.markets[n].F(1.0))
        assert p[:, 1].mean() == pytest.approx(F, abs=4 * p[:, 1].std() / np.sqrt(len(p)) + 1e-9)


def test_correlation_root_handles_singular_matrices_and_rejects_bad_ones():
    L = correlation_root([[1, 1], [1, 1]])
    np.testing.assert_allclose(L @ L.T, [[1, 1], [1, 1]], atol=1e-12)
    with pytest.raises(ValueError, match="positive semi-definite"):
        correlation_root([[1, 0.9, 0], [0.9, 1, 0.9], [0, 0.9, 1]])
    with pytest.raises(ValueError, match="ones on its diagonal"):
        correlation_root([[1, 0.5], [0.5, 2]])


# --- European worst-of against closed forms ---

@pytest.mark.parametrize("rho", [0.0, 0.6, 0.95])
def test_worst_of_digital_matches_the_bivariate_normal(rho):
    sheet = worst("capital_protected", **{"upside.type": "digital", "upside.strike": 1.0,
                                          "upside.digital_amount": 0.1})
    res, p = mc(sheet, context(rho))
    T, pays = times(p)
    ref = 100 * float(FLAT.D(pays[-1])) * (1 + 0.1 * worst_digital(1.0, T, (VOL, VOL2), rho, R, (0.01, Q2)))
    assert abs(res.price - ref) <= 4 * res.stderr, (res.price, res.stderr, ref)


@pytest.mark.parametrize("rho", [0.3, 0.8])
def test_worst_of_reverse_convertible_matches_the_conditional_integral(rho):
    K, B = 1.0, 0.7
    sheet = worst("reverse_convertible", **{"downside.barrier": B, "coupon.rate": 0.08})
    res, p = mc(sheet, context(rho))
    T, pays = times(p)
    vols, qs = (VOL, VOL2), (0.01, Q2)
    loss = worst_put(B, T, vols, rho, R, qs) + (K - B) * (1 - worst_digital(B, T, vols, rho, R, qs))
    ref = 100 * 0.02 * sum(float(FLAT.D(t)) for t in pays) + 100 * float(FLAT.D(pays[-1])) * (1 - loss)
    assert abs(res.price - ref) <= 4 * res.stderr, (res.price, res.stderr, ref)


def test_worst_put_reference_reduces_to_black_scholes():
    from _structured_refs import put_fwd
    # perfectly correlated identical names: the worst is either of them
    assert worst_put(0.9, 1.0, (VOL, VOL), 1.0 - 1e-12, R, (0.01, 0.01)) == pytest.approx(put_fwd(90.0, 1.0) / 100,
                                                                                         abs=1e-6)


# --- autocalls ---

def test_identical_names_with_correlation_one_give_the_single_name_autocall():
    ctx = context(1.0, vol2=VOL, q2=0.01)
    sheet = worst("phoenix", **{"coupon.rate": 0.08, "coupon.memory": True})
    res, _ = mc(sheet, ctx)
    single = preset("phoenix", **{"wrapper.solve": "upfront", "coupon.rate": 0.08, "coupon.memory": True})
    ref = autocall_reference(single)
    assert abs(res.price - ref) <= 4 * res.stderr, (res.price, res.stderr, ref)


def test_identical_names_daily_knock_in_gives_the_single_name_autocall():
    ctx = context(1.0, vol2=VOL, q2=0.01)
    changes = {"coupon.rate": 0.08, "coupon.memory": False, "general.maturity_months": 12,
               "downside.observation": "daily", "downside.barrier": 0.7}
    res, p = mc(worst("phoenix", **changes), ctx, n_paths=60_000, batch=20_000)
    assert "worst(lowest(SPX" in p.script
    ref = autocall_reference(preset("phoenix", **{"wrapper.solve": "upfront", **changes}))
    assert abs(res.price - ref) <= 4 * res.stderr, (res.price, res.stderr, ref)


def test_lower_correlation_makes_the_worst_of_autocall_cheaper():
    sheet = worst("phoenix", **{"coupon.rate": 0.08})
    low, _ = mc(sheet, context(0.3), seed=17)
    high, _ = mc(sheet, context(0.9), seed=17)
    assert high.price - low.price > 4 * np.hypot(high.stderr, low.stderr)


# --- builder, policy and quote ---

def test_worst_of_fields_and_script():
    sheet = worst("phoenix", **{"general.underlyings": ("SPX", "SX5E", "NKY")})
    assert sheet.underlyings == ("SPX", "SX5E", "NKY") and sheet.get("general.underlying") is None
    text = compile_sheet(sheet, VAL).script
    assert "perf = worst(SPX / S0_SPX, SX5E / S0_SX5E, NKY / S0_NKY)" in text
    with pytest.raises(TermSheetError, match="2 to 5 distinct names"):
        worst("phoenix", **{"general.underlyings": ("SPX", "SPX")})
    with pytest.raises(TermSheetError, match="identifiers"):
        worst("phoenix", **{"general.underlyings": ("SPX", "S&P")})


def test_worst_of_quote_uses_the_basket_policy():
    spx = BasketContext.with_spx({"SX5E": (100.0, 0.03, 0.03, 0.22)}, [[1, 0.7], [0.7, 1]])
    q = quote(worst("phoenix", **{"wrapper.solve": "coupon"}), spx, n_paths=20_000, batch=10_000)
    assert (q.decision.model, q.decision.control) == ("lv", "bs")
    assert "correlation is an input" in q.decision.reason
    assert any("flat volatility" in w for w in q.warnings)
    assert q.result.price == pytest.approx(98.0, abs=1e-10)
    single = quote(preset("phoenix"), flat_context(), model="bs", control=None, n_paths=20_000, batch=10_000)
    assert q.coupon_rate > single.coupon_rate * 0.5  # a worst-of pays for the correlation risk
    with pytest.raises(ValueError, match="needs a BasketContext"):
        quote(worst("phoenix"), flat_context())


def test_worst_of_running_product_needs_every_fixing():
    sheet = worst("reverse_convertible", **{"general.strike_date": dt.date(2026, 4, 27)})
    with pytest.raises(KeyError, match="no fixing of SX5E"):
        quote(sheet, context(0.5), control=None, n_paths=2_000, batch=1_000, fixings={"SPX": {"2026-04-27": 100.0}})


# --- books mixing single names and baskets ---

def test_basket_book_with_one_position_is_the_product_price_bit_for_bit():
    from optlab.structured.portfolio import Book, Position
    ctx = context(0.6)
    sheet = worst("phoenix", **{"coupon.rate": 0.08, "general.maturity_months": 12})
    book = Book([Position("wo", sheet, 1.0)]).price(ctx, model="bs", n_paths=40_000, seed=3, batch=10_000)
    alone, _ = mc(sheet, ctx, n_paths=40_000, seed=3, batch=10_000)
    assert book.value == alone.value.price and book.stderr == alone.value.stderr


def test_book_mixes_single_name_and_worst_of_positions_on_one_basket_simulation():
    from optlab.structured.portfolio import Book, Position
    ctx = context(0.6)
    wo = worst("phoenix", **{"coupon.rate": 0.08, "general.maturity_months": 12})
    rc = preset("reverse_convertible", **{"wrapper.solve": "upfront", "coupon.rate": 0.06})
    res = Book([Position("wo", wo, 2.0), Position("rc", rc, -1.0), Position("wo short", wo, -2.0)]).price(
        ctx, n_paths=40_000, seed=5, batch=10_000)
    assert res.model == "lv" and any("flat volatility" in w for w in res.warnings)  # flat names stay BS
    assert res.positions[0].value == -res.positions[2].value
    assert res.value == pytest.approx(res.positions[1].value, abs=1e-9)
    from _structured_refs import rc_european
    p = compile_sheet(rc, VAL)
    T, pays = times(p)
    ref = rc_european(100.0, 0.015, pays, T, pays[-1], 1.0, 1.0)  # the preset knocks in at the strike
    assert abs(res.positions[1].unit_price - ref) <= 4 * res.positions[1].unit_stderr
    from optlab.structured.portfolio import Book as B2
    with pytest.raises(ValueError, match="BasketContext"):
        B2([Position("wo", wo, 1.0)]).price(flat_context(), model="bs", n_paths=2_000)
