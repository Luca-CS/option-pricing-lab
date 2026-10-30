"""Products built from term sheets, against closed forms (Black-Scholes world) and the SPX smile."""

import numpy as np
import pytest
from _structured_refs import S0, VOL, flat_context, rc_european

from optlab.blackscholes import black_price
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.presets import preset
from optlab.structured.pricer import price
from optlab.structured.quote import quote
from optlab.structured.replication import nodes, quadrature, replication_result
from optlab.structured.script.timeline import Clock, build_timeline

BS_CTX = flat_context()
SPX = PricingContext.from_calibration()
N_MC = 100_000


def within(res, exact, n_se=4.0, floor=0.0):
    return abs(res.price - exact) <= n_se * res.stderr + floor


def times(product):
    tl = build_timeline(product.program, Clock(product.valuation))
    return tl, [t for t in tl.pay_times if t is not None]


def rc_reference(sheet, ctx=BS_CTX, cpn=None):
    p = compile_sheet(sheet, ctx.valuation)
    tl, pays = times(p)
    cpn = p.program.param_values["cpn"] if cpn is None else cpn
    T = float(tl.event_times[-1])
    ref = rc_european(100.0, cpn, pays, T, pays[-1], sheet.get("downside.strike"), sheet.get("downside.barrier"),
                      ctx.market)
    return p, tl, ref


# --- test 2: reverse convertible with a European barrier ---

@pytest.mark.parametrize("barrier", [1.0, 0.7])
def test_reverse_convertible_matches_closed_form(barrier):
    sheet = preset("reverse_convertible", **{"downside.barrier": barrier, "coupon.rate": 0.06,
                                             "wrapper.solve": "upfront"})
    p, tl, ref = rc_reference(sheet)
    mc = price(p.program, BS_CTX.model("bs"), BS_CTX.market, n_paths=N_MC, seed=21, valuation=p.valuation)
    assert within(mc, ref), (mc.price, mc.stderr, ref)
    rep = replication_result(p.program, tl, BS_CTX.surface, BS_CTX.market, float(tl.event_times[-1]))
    assert rep.price == pytest.approx(ref, abs=2e-5)
    assert rep.numerical_error < 1e-5


def test_quadrature_matches_the_forward_and_the_smile():
    T = 1.0
    spots, prob = quadrature(SPX.surface, SPX.market, T)
    assert prob.sum() == pytest.approx(1.0, abs=1e-12)
    assert np.dot(prob, spots) == pytest.approx(float(SPX.market.F(T)), rel=1e-12)
    F, D = float(SPX.market.F(T)), float(SPX.market.D(T))
    for K in (70.0, 90.0, 100.0, 115.0):
        iv = float(SPX.surface.iv(np.log(K / F), T))
        payoff = lambda s, K=K: np.maximum(K - s, 0.0)  # noqa: E731
        nodes_k, w = nodes(payoff, SPX.surface, SPX.market, T)
        put = np.dot(w, payoff(nodes_k)) * D
        assert put == pytest.approx(float(black_price(F, K, T, iv, D, "put")), abs=1e-9)


def test_european_policy_on_spx_agrees_with_local_vol_monte_carlo():
    q = quote(preset("reverse_convertible", **{"downside.barrier": 0.7}), SPX, n_paths=N_MC)
    assert q.decision.method == "replication"
    assert q.result.price == pytest.approx(q.target, abs=1e-10)
    assert abs(q.control_gap_se) < 4


# --- test 9: coupon solver ---

def test_solved_coupon_reprices_to_the_target_on_the_same_paths():
    sheet = preset("reverse_convertible", **{"downside.barrier": 0.7, "downside.observation": "daily"})
    q = quote(sheet, BS_CTX, model="bs", control=None, n_paths=40_000, batch=10_000)
    assert q.result.price == pytest.approx(98.0, abs=1e-10)
    cpn = q.coupon_rate * q.product.coupon_scale
    again = price(q.product.program, BS_CTX.model("bs"), BS_CTX.market, n_paths=40_000, seed=q.decision.seed,
                  batch=10_000, params={"cpn": cpn}, valuation=q.product.valuation)
    assert again.price == pytest.approx(98.0, abs=1e-8)
    assert 0 < q.coupon_rate_se < 0.01


def test_solved_coupon_matches_the_closed_form_inversion():
    sheet = preset("reverse_convertible", **{"downside.barrier": 0.8})
    q = quote(sheet, BS_CTX, control=None)
    p, tl, ref0 = rc_reference(sheet, cpn=0.0)
    _, _, ref1 = rc_reference(sheet, cpn=1.0)
    cpn = (98.0 - ref0) / (ref1 - ref0)
    assert q.coupon_rate == pytest.approx(cpn / p.coupon_scale, abs=1e-6)


def test_swap_coupon_mode_targets_minus_the_margin_and_upfront_mode_adds_it():
    sheet = preset("reverse_convertible", **{"wrapper.format": "swap"})
    q = quote(sheet, BS_CTX, control=None)
    assert q.result.price == pytest.approx(-2.0, abs=1e-10)
    note = quote(preset("reverse_convertible"), BS_CTX, control=None)
    # the note investor also lends the notional to the issuer: the coupon gap pays the interest on it
    _, pays = times(q.product)
    annuity = 100 * q.product.coupon_scale * sum(float(BS_CTX.market.D(t)) for t in pays)
    interest = 100 * (1 - float(BS_CTX.market.D(pays[-1])))
    assert note.coupon_rate - q.coupon_rate == pytest.approx(interest / annuity, rel=1e-9)
    up = quote(sheet.replace(**{"wrapper.solve": "upfront", "coupon.rate": q.coupon_rate}), BS_CTX, control=None)
    assert up.upfront == pytest.approx(0.0, abs=1e-8)  # leg value -2 % plus the 2 % margin


def test_issuer_spread_raises_the_solved_coupon():
    base = quote(preset("reverse_convertible"), BS_CTX, control=None)
    wide = quote(preset("reverse_convertible", **{"wrapper.issuer_spread": 0.01}), BS_CTX, control=None)
    assert wide.coupon_rate > base.coupon_rate


def test_quote_table_reads_like_a_term_sheet():
    q = quote(preset("reverse_convertible"), BS_CTX, control=None)
    text = q.table()
    assert "coupon solved" in text and "policy: european -> replication" in text and "2027-11-03" in text
    assert S0 == 100.0 and VOL == 0.2
