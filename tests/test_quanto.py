"""Quanto: the adjusted forward against closed forms and a change of measure, through the whole pricing chain."""

import datetime as dt

import numpy as np
import pytest
from _structured_refs import (flat_surface, quanto_call_by_change_of_measure, quanto_call_undiscounted, quanto_forward, worst_digital,
                              worst_put)

from optlab import BlackScholesModel, Market
from optlab.blackscholes import black_price
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.multiasset import BasketContext
from optlab.structured.presets import preset
from optlab.structured.pricer import price
from optlab.structured.quanto import integrated_vol, payment_warnings, quanto_market
from optlab.structured.quote import quote
from optlab.structured.schema import TermSheet, TermSheetError
from optlab.structured.script.timeline import Clock, build_timeline

VAL = dt.date(2026, 10, 27)
S0, R_USD, R_EUR, Q_EUR, Q_USD = 100.0, 0.04, 0.02, 0.03, 0.01
VOL_S, VOL_US, VOL_FX = 0.22, 0.2, 0.08
N_MC = 200_000


def usd_discount(t):
    return np.exp(-R_USD * np.asarray(t, dtype=float))


def sx5e_in_usd(rho, vol=VOL_S):
    return quanto_market(Market.flat(S0, R_EUR, Q_EUR), usd_discount, rho, VOL_FX, vol)


def single_context(rho):
    """SX5E paid in dollars in a Black-Scholes world."""
    spx = PricingContext.from_calibration()
    ctx = PricingContext(sx5e_in_usd(rho), flat_surface(VOL_S), spx.heston, spx.slv, VAL, "SX5E", payment_currency="USD")
    ctx.model = lambda name, T=1.0: BlackScholesModel(VOL_S) if name in ("bs", "lv") else spx.model(name, T)
    return ctx


def basket_context(rho_fx, rho=0.6):
    markets = {"SPX": Market.flat(S0, R_USD, Q_USD), "SX5E": sx5e_in_usd(rho_fx)}
    return BasketContext(("SPX", "SX5E"), markets, {"SPX": VOL_US, "SX5E": VOL_S}, np.array([[1, rho], [rho, 1]]), VAL, "USD")


def times(p):
    tl = build_timeline(p.program, Clock(VAL))
    return float(tl.event_times[-1]), [t for t in tl.pay_times if t is not None]


def within(res, ref, se=4):
    assert abs(res.price - ref) <= se * res.stderr, (res.price, res.stderr, ref)


# --- the forward ---

@pytest.mark.parametrize("rho", [-0.5, 0.0, 0.4])
def test_the_market_has_the_closed_form_forward_and_the_payment_curve(rho):
    q = sx5e_in_usd(rho)
    for T in (0.25, 1.0, 3.0):
        assert float(q.F(T)) == pytest.approx(quanto_forward(S0, T, R_EUR, Q_EUR, VOL_S, VOL_FX, rho), rel=1e-12)
        assert float(q.D(T)) == pytest.approx(np.exp(-R_USD * T), rel=1e-12)
    assert q.spot == S0


def test_zero_correlation_or_zero_fx_vol_only_changes_the_discounting():
    base = Market.flat(S0, R_EUR, Q_EUR)
    for rho, vol_fx in ((0.0, VOL_FX), (0.5, 0.0)):
        q = quanto_market(base, usd_discount, rho, vol_fx, VOL_S)
        assert float(q.F(2.0)) == pytest.approx(float(base.F(2.0)), rel=1e-12)


def test_a_flat_surface_integrates_like_a_flat_vol_and_a_term_structure_like_its_forward_vols():
    T = np.array([0.1, 0.5, 1.0, 2.0, 5.0])
    np.testing.assert_allclose(integrated_vol(T, flat_surface(VOL_S)), VOL_S * T, rtol=1e-10)
    # total variance 0.04 T to one year, then 0.09 per year: forward vols of 0.2 then 0.3
    from optlab import SVISurface
    mats = np.array([0.5, 1.0, 2.0, 3.0])
    total = np.array([0.02, 0.04, 0.13, 0.22])
    surface = SVISurface(mats, [[w, 0.0, 0.0, 0.0, 0.1] for w in total])
    assert integrated_vol(1.0, surface)[0] == pytest.approx(0.2, rel=1e-10)
    assert integrated_vol(3.0, surface)[0] == pytest.approx(0.2 + 2 * 0.3, rel=1e-10)


def test_dividend_steps_and_pillars_survive_the_adjustment():
    pillars = np.array([0.25, 0.5, 0.5001, 1.0])
    forwards = np.array([100.5, 101.0, 98.0, 99.0])
    base = Market(100.0, pillars, np.exp(-0.02 * pillars), forwards)
    q = quanto_market(base, usd_discount, -0.3, VOL_FX, VOL_S)
    ratio = np.asarray(q.F(pillars)) / forwards
    np.testing.assert_allclose(ratio, np.exp(0.3 * VOL_FX * VOL_S * pillars), rtol=1e-12)


def test_bad_inputs_are_refused():
    base = Market.flat(S0, R_EUR, Q_EUR)
    with pytest.raises(ValueError, match="rho"):
        quanto_market(base, usd_discount, 1.5, VOL_FX, VOL_S)
    with pytest.raises(ValueError, match="FX volatility"):
        quanto_market(base, usd_discount, 0.1, -0.1, VOL_S)


# --- the sign, with no quanto formula on the reference side ---

@pytest.mark.parametrize("rho", [-0.6, 0.6])
def test_the_closed_form_agrees_with_a_change_of_measure(rho):
    K, T = 105.0, 2.0
    closed = np.exp(-R_USD * T) * quanto_call_undiscounted(S0, K, T, R_EUR, Q_EUR, VOL_S, VOL_FX, rho)
    pv, se = quanto_call_by_change_of_measure(S0, K, T, R_EUR, R_USD, Q_EUR, VOL_S, VOL_FX, rho)
    assert abs(pv - closed) <= 4 * se, (pv, se, closed)
    # and the library's market prices the same call
    q = sx5e_in_usd(rho)
    lib = float(q.D(T)) * float(black_price(float(q.F(T)), K, T, VOL_S, 1.0, "call"))
    assert abs(pv - lib) <= 4 * se, (pv, se, lib)


# --- through the pricing chain ---

@pytest.mark.parametrize("rho", [-0.6, 0.6])
def test_a_quanto_note_on_one_name_matches_the_closed_form_at_4_se(rho):
    sheet = preset("capital_protected", **{"general.underlying": "SX5E", "general.payment_currency": "USD", "wrapper.solve": "upfront",
                                           "general.pay_lag": 0})
    q = quote(sheet, single_context(rho), model="lv", control=None, n_paths=N_MC, seed=11)
    p = q.product
    T, pays = times(p)
    D, K, part = np.exp(-R_USD * pays[-1]), sheet.get("upside.strike"), sheet.get("upside.participation")
    call = quanto_call_undiscounted(S0, K * S0, T, R_EUR, Q_EUR, VOL_S, VOL_FX, rho)
    within(q.result, 100 * D * (1 + part * call / S0))
    assert not any("FX" in w for w in q.warnings)


def test_a_quanto_note_is_priced_by_replication_too_when_the_policy_chooses_it():
    sheet = preset("capital_protected", **{"general.underlying": "SX5E", "general.payment_currency": "USD", "wrapper.solve": "upfront",
                                           "general.pay_lag": 0})
    rho = -0.4
    q = quote(sheet, single_context(rho), control=None)
    assert q.decision.method == "replication"
    T, pays = times(q.product)
    ref = 100 * np.exp(-R_USD * pays[-1]) * (1 + sheet.get("upside.participation")
                                             * quanto_call_undiscounted(S0, sheet.get("upside.strike") * S0, T, R_EUR, Q_EUR, VOL_S, VOL_FX, rho) / S0)
    assert q.result.price == pytest.approx(ref, abs=1e-3)


@pytest.mark.parametrize("rho_fx", [-0.5, 0.5])
def test_a_worst_of_spx_sx5e_in_dollars_matches_the_closed_form_at_4_se(rho_fx):
    """The name in euros carries the quanto drift, the name in dollars does not. In dollars r = R_USD, so SX5E's effective yield is
    what brings its drift to r_eur - q - rho sigma sigma."""
    K, B, rho = 1.0, 0.7, 0.6
    sheet = preset("reverse_convertible", **{"general.basket": "worst_of", "general.underlyings": ("SPX", "SX5E"), "wrapper.solve": "upfront",
                                             "downside.barrier": B, "coupon.rate": 0.08, "general.payment_currency": "USD"})
    ctx = basket_context(rho_fx, rho)
    p = compile_sheet(sheet, VAL)
    res = price(p.program, ctx.model("bs", p.horizon, p.program.underlying_names), ctx.market, n_paths=N_MC, seed=13, batch=25_000, valuation=VAL)
    T, pays = times(p)
    q_eff = R_USD - (R_EUR - Q_EUR - rho_fx * VOL_S * VOL_FX)
    vols, qs = (VOL_US, VOL_S), (Q_USD, q_eff)
    loss = worst_put(B, T, vols, rho, R_USD, qs) + (K - B) * (1 - worst_digital(B, T, vols, rho, R_USD, qs))
    D = lambda t: np.exp(-R_USD * t)
    ref = 100 * 0.02 * sum(D(t) for t in pays) + 100 * D(pays[-1]) * (1 - loss)
    within(res, ref)


def test_the_adjustment_moves_a_worst_of_by_many_standard_errors():
    """So the 4 SE test above would catch a missing or wrongly signed adjustment."""
    sheet = preset("reverse_convertible", **{"general.basket": "worst_of", "general.underlyings": ("SPX", "SX5E"), "wrapper.solve": "upfront",
                                             "downside.barrier": 0.7, "coupon.rate": 0.08, "general.payment_currency": "USD"})
    p = compile_sheet(sheet, VAL)

    def run(rho_fx):
        ctx = basket_context(rho_fx)
        return price(p.program, ctx.model("bs", p.horizon, p.program.underlying_names), ctx.market, n_paths=N_MC, seed=13, batch=25_000, valuation=VAL).value

    up, down = run(-0.5), run(0.5)
    assert up.price - down.price > 8 * np.hypot(up.stderr, down.stderr), (up, down)  # rho < 0 lifts the forward, so the note is worth more


def test_the_first_name_gives_the_discount_curve_whichever_it_is():
    a = basket_context(0.3)
    b = BasketContext(("SX5E", "SPX"), a.markets, a.vols, a.corr, VAL, "USD")
    assert float(b.market.D(2.0)) == pytest.approx(float(a.market.D(2.0)), rel=1e-12)


# --- the term sheet ---

def test_the_field_resolves_to_the_first_names_currency():
    assert preset("phoenix").payment_currency == "USD"
    assert preset("phoenix", **{"general.underlying": "SX5E"}).payment_currency == "EUR"
    assert preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": ("SX5E", "SPX")}).payment_currency == "EUR"
    assert preset("phoenix", **{"general.underlying": "SX5E", "general.payment_currency": "USD"}).payment_currency == "USD"
    with pytest.raises(TermSheetError, match="payment_currency"):
        preset("phoenix", **{"general.payment_currency": "JPY"})


def test_a_stored_sheet_without_the_field_stays_native_and_round_trips():
    d = preset("phoenix").to_dict(json_safe=True)
    del d["general"]["payment_currency"]
    sheet = TermSheet.from_dict(d)
    assert sheet.get("general.payment_currency") == "native"
    assert TermSheet.from_dict(sheet.to_dict(json_safe=True)).to_dict() == sheet.to_dict()


def test_a_market_for_another_currency_is_refused():
    sheet = preset("phoenix", **{"general.underlying": "SX5E"})  # native: euros
    with pytest.raises(ValueError, match="pays in EUR, this market was built to pay in USD"):
        payment_warnings(sheet, single_context(0.1))


def test_an_explicit_quanto_on_a_market_without_fx_is_refused_and_a_native_mix_is_flagged():
    flat = BasketContext(("SPX", "SX5E"), basket_context(0.0).markets, {"SPX": 0.2, "SX5E": 0.2}, np.eye(2), VAL)
    mixed = preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": ("SPX", "SX5E")})
    assert any("not adjusted for FX" in w and "SX5E (EUR)" in w for w in payment_warnings(mixed, flat))
    explicit = mixed.replace(**{"general.payment_currency": "USD"})
    with pytest.raises(ValueError, match="needs a quanto adjustment"):
        payment_warnings(explicit, flat)
    same = preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": ("SPX", "NDX")})
    assert payment_warnings(same, flat) == []
