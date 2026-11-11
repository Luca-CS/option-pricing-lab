"""Products built from term sheets, against closed forms (Black-Scholes world) and the SPX smile."""

import numpy as np
import pytest
from _paths import SPX_CALIBRATION
from _structured_refs import S0, VOL, flat_context, rc_european

from optlab.core.blackscholes import black_price
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.presets import preset
from optlab.structured.pricer import price
from optlab.structured.quote import quote
from optlab.structured.replication import nodes, quadrature, replication_result
from optlab.structured.script.timeline import Clock, build_timeline

BS_CTX = flat_context()
SPX = PricingContext.from_calibration(SPX_CALIBRATION)
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
    ref = rc_european(
        100.0, cpn, pays, T, pays[-1], sheet.get("downside.strike"), sheet.get("downside.barrier"), ctx.market
    )
    return p, tl, ref


# --- test 2: reverse convertible with a European barrier ---


@pytest.mark.parametrize("barrier", [1.0, 0.7])
def test_reverse_convertible_matches_closed_form(barrier):
    sheet = preset(
        "reverse_convertible", **{"downside.barrier": barrier, "coupon.rate": 0.06, "wrapper.solve": "upfront"}
    )
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
    again = price(
        q.product.program,
        BS_CTX.model("bs"),
        BS_CTX.market,
        n_paths=40_000,
        seed=q.decision.seed,
        batch=10_000,
        params={"cpn": cpn},
        valuation=q.product.valuation,
    )
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


# --- test 3: barrier reverse convertible, daily knock-in ---


def test_barrier_reverse_convertible_matches_bgk_shifted_closed_form():
    from _structured_refs import FLAT, Q, R

    from optlab.core import barrier_price, barrier_shift_discrete

    sheet = preset(
        "barrier_reverse_convertible", **{"downside.barrier": 0.8, "coupon.rate": 0.06, "wrapper.solve": "upfront"}
    )
    p = compile_sheet(sheet, BS_CTX.valuation)
    tl, pays = times(p)
    (fix,) = tl.windows.values()
    T = float(tl.event_times[-1])
    H = barrier_shift_discrete(80.0, S0, VOL, T / (len(fix) - 1))
    dip = barrier_price(S0, 100.0, H, T, R, Q, VOL, "down-in", "put") / float(FLAT.D(T))
    cpn = p.program.param_values["cpn"]
    ref = 100 * cpn * sum(float(FLAT.D(t)) for t in pays) + float(FLAT.D(pays[-1])) * (100 - dip)
    mc = price(p.program, BS_CTX.model("bs"), FLAT, n_paths=N_MC, seed=31, batch=25_000, valuation=p.valuation)
    assert within(mc, ref, floor=0.005), (mc.price, mc.stderr, ref)
    # the daily barrier costs more than the same barrier observed at maturity only
    euro = rc_reference(sheet.replace(**{"downside.observation": "european"}))[2]
    assert ref < euro


def test_brc_policy_is_local_vol_with_an_slv_control():
    q = quote(preset("barrier_reverse_convertible"), SPX, n_paths=40_000, batch=20_000)
    assert (q.decision.method, q.decision.model, q.decision.control) == ("monte_carlo", "lv", "slv")
    assert q.result.price == pytest.approx(98.0, abs=1e-10)
    assert q.coupon_rate > 0 and q.control is not None


# --- geared put (low strike put geared at 1 / K) ---


@pytest.mark.parametrize("K", [0.8, 0.6])
def test_geared_put_matches_closed_form(K):
    from _structured_refs import FLAT, put_fwd

    sheet = preset(
        "reverse_convertible",
        **{"downside.type": "geared_put", "downside.strike": K, "coupon.rate": 0.03, "wrapper.solve": "upfront"},
    )
    p = compile_sheet(sheet, BS_CTX.valuation)
    tl, pays = times(p)
    T = float(tl.event_times[-1])
    cpn = p.program.param_values["cpn"]
    ref = 100 * cpn * sum(float(FLAT.D(t)) for t in pays) + float(FLAT.D(pays[-1])) * (
        100 - 100 / K * put_fwd(K * S0, T) / S0
    )
    mc = price(p.program, BS_CTX.model("bs"), FLAT, n_paths=N_MC, seed=41, valuation=p.valuation)
    assert within(mc, ref), (mc.price, mc.stderr, ref)
    rep = replication_result(p.program, tl, BS_CTX.surface, FLAT, T)
    assert rep.price == pytest.approx(ref, abs=1e-8)


def test_gearing_makes_the_loss_continuous_at_the_strike():
    sheet = preset("reverse_convertible", **{"downside.type": "geared_put", "downside.strike": 0.8})
    p = compile_sheet(sheet, BS_CTX.valuation)
    from optlab.structured.scenario import Scenario
    from optlab.structured.script.evaluator import Evaluator

    tl, _ = times(p)
    perf = np.array([0.4, 0.79999, 0.8, 1.2])
    paths = np.tile(100 * perf[:, None], (1, len(tl.sim_times)))
    out = Evaluator(p.program, tl).run(Scenario("SPX", tl.sim_times, paths, 100.0))
    red = out.flows[(tl.pay_times[-1], "redemption")]
    np.testing.assert_allclose(red, [50.0, 100 - 100 * 0.00001 / 0.8, 100.0, 100.0])


# --- test 4: upside (call, call spread, digital) and the airbag ---


def upside_reference(sheet, T, T_pay):
    from _structured_refs import FLAT, call_fwd, prob_below, put_fwd

    g = sheet.get
    up, kind = 0.0, g("upside.type")
    if kind == "call":
        up = g("upside.participation") * call_fwd(g("upside.strike") * S0, T) / S0
    elif kind == "call_spread":
        up = g("upside.participation") * (call_fwd(g("upside.strike") * S0, T) - call_fwd(g("upside.cap") * S0, T)) / S0
    elif kind == "digital":
        up = g("upside.digital_amount") * (1 - prob_below(g("upside.strike") * S0, T))
    loss = 0.0
    if g("downside.type") == "geared_put":
        K = g("downside.strike")
        loss = put_fwd(K * S0, T) / S0 / K
    return 100 * float(FLAT.D(T_pay)) * (1 + up - loss)


@pytest.mark.parametrize(
    "name, changes",
    [
        ("airbag", {}),
        ("airbag", {"downside.strike": 0.7, "upside.participation": 0.8}),
        ("capital_protected", {}),
        (
            "capital_protected",
            {"upside.type": "call_spread", "upside.strike": 1.0, "upside.cap": 1.4, "upside.participation": 1.0},
        ),
        ("capital_protected", {"upside.type": "digital", "upside.strike": 1.05, "upside.digital_amount": 0.12}),
    ],
)
def test_upside_products_match_closed_forms(name, changes):
    sheet = preset(name, **changes)
    p = compile_sheet(sheet, BS_CTX.valuation)
    tl, pays = times(p)
    T = float(tl.event_times[-1])
    ref = upside_reference(sheet, T, pays[-1])
    mc = price(p.program, BS_CTX.model("bs"), BS_CTX.market, n_paths=N_MC, seed=51, valuation=p.valuation)
    assert within(mc, ref), (mc.price, mc.stderr, ref)
    q = quote(sheet, BS_CTX, control=None)
    assert q.decision.method == "replication" and q.coupon_rate is None
    assert q.upfront == pytest.approx(ref, abs=1e-8)


@pytest.mark.slow
def test_airbag_quote_on_spx_in_swap_format():
    sheet = preset("airbag", **{"wrapper.format": "swap"})
    q = quote(sheet, SPX, n_paths=N_MC)
    assert q.decision.method == "replication"
    assert q.upfront == pytest.approx(q.result.price + 2.0, abs=1e-12)  # leg value plus the margin
    assert abs(q.control_gap_se) < 4


# --- tests 5 to 7: autocalls against the backward quadrature ---


def mc_bs(sheet, n_paths=N_MC, seed=61, batch=25_000):
    p = compile_sheet(sheet, BS_CTX.valuation)
    return price(
        p.program, BS_CTX.model("bs"), BS_CTX.market, n_paths=n_paths, seed=seed, batch=batch, valuation=p.valuation
    )


@pytest.mark.slow
def test_quadrature_reference_reproduces_the_rc_closed_form():
    from _structured_refs import autocall_reference

    sheet = preset("reverse_convertible", **{"downside.barrier": 0.7, "coupon.rate": 0.06})
    assert autocall_reference(sheet) == pytest.approx(rc_reference(sheet)[2], abs=2e-4)


def test_single_date_autocall_is_a_digital_plus_a_put():
    from _structured_refs import FLAT, prob_below, put_fwd

    from optlab.structured.dates import schedule

    sheet = preset(
        "phoenix",
        **{
            "general.maturity_months": 6,
            "autocall.frequency_months": 6,
            "coupon.frequency_months": 6,
            "coupon.rate": 0.1,
            "coupon.memory": False,
            "wrapper.solve": "upfront",
        },
    )
    ((obs, pay),) = schedule(BS_CTX.valuation, 6, 6)
    p = compile_sheet(sheet, BS_CTX.valuation)
    tl, _ = times(p)
    T, Tp = float(tl.event_times[-1]), tl.pay_times[-1]
    digital = 1 - prob_below(70.0, T)  # coupon if perf >= 70 %
    loss = (put_fwd(60.0, T) + 40.0 * prob_below(60.0, T)) / S0  # PDI K 100 %, B 60 %
    ref = float(FLAT.D(Tp)) * 100 * (0.05 * digital + 1 - loss)
    mc = mc_bs(sheet)
    assert within(mc, ref), (mc.price, mc.stderr, ref)
    assert compile_sheet(sheet, BS_CTX.valuation).script.count("terminate") == 0


@pytest.mark.slow
@pytest.mark.parametrize(
    "name, changes",
    [
        ("athena", {"coupon.rate": 0.08}),
        ("phoenix", {"coupon.rate": 0.08, "coupon.memory": False}),
        ("phoenix", {"coupon.rate": 0.08, "coupon.memory": True}),
        ("phoenix", {"coupon.rate": 0.08, "autocall.step_down": 0.05, "autocall.first_call": 2}),
        ("phoenix", {"coupon.rate": 0.12, "coupon.frequency_months": 1, "general.maturity_months": 12}),
    ],
)
def test_autocall_matches_backward_quadrature(name, changes):
    from _structured_refs import autocall_reference

    sheet = preset(name, **{"wrapper.solve": "upfront", **changes})
    ref = autocall_reference(sheet)
    mc = mc_bs(sheet)
    assert within(mc, ref), (mc.price, mc.stderr, ref)


@pytest.mark.slow
def test_autocall_with_daily_knock_in_matches_backward_quadrature():
    from _structured_refs import autocall_reference

    sheet = preset(
        "phoenix",
        **{
            "wrapper.solve": "upfront",
            "coupon.rate": 0.08,
            "coupon.memory": False,
            "general.maturity_months": 12,
            "downside.observation": "daily",
            "downside.barrier": 0.7,
        },
    )
    ref = autocall_reference(sheet)
    mc = mc_bs(sheet)
    assert within(mc, ref), (mc.price, mc.stderr, ref)
    euro = autocall_reference(sheet.replace(**{"downside.observation": "european"}))
    assert ref < euro


def test_autocall_policy_is_local_vol_and_reports_call_probabilities():
    q = quote(preset("phoenix"), SPX, n_paths=40_000, batch=20_000)
    assert (q.decision.method, q.decision.model, q.decision.control) == ("monte_carlo", "lv", "slv")
    assert q.result.price == pytest.approx(98.0, abs=1e-10)
    assert q.result.termination.sum() > 0.3
    assert 0.25 < q.result.expected_life < 2.0
    assert "forward skew" in q.table()


# --- test 10: Greeks ---


def rc_closed_form(spot, vol, T, pays, cpn, K=1.0, B=0.7):
    """RC value for a spot and a vol, the product struck at S0 = 100 (strike levels fixed)."""
    from _structured_refs import FLAT, Q, R
    from scipy.stats import norm

    F = spot * np.exp((R - Q) * T)
    sd = vol * np.sqrt(T)
    put = float(black_price(F, B * S0, T, vol, 1.0, "put"))
    below = norm.cdf(-(np.log(F / (B * S0)) / sd - 0.5 * sd))
    loss = (put + (K - B) * S0 * below) / S0
    return 100 * cpn * sum(float(FLAT.D(t)) for t in pays) + float(FLAT.D(pays[-1])) * 100 * (1 - loss)


def test_rc_greeks_match_closed_form_differences():
    from optlab.structured.greeks import greeks

    sheet = preset("reverse_convertible", **{"downside.barrier": 0.7, "coupon.rate": 0.06, "wrapper.solve": "upfront"})
    p = compile_sheet(sheet, BS_CTX.valuation)
    tl, pays = times(p)
    T = float(tl.event_times[-1])
    cpn = p.program.param_values["cpn"]
    delta = (rc_closed_form(101, VOL, T, pays, cpn) - rc_closed_form(99, VOL, T, pays, cpn)) / 2
    vega = (rc_closed_form(100, VOL + 0.01, T, pays, cpn) - rc_closed_form(100, VOL - 0.01, T, pays, cpn)) / 2
    rep = greeks(p.program, BS_CTX, "bs", "replication", T_obs=T)
    assert rep.delta == pytest.approx(delta, abs=1e-8) and rep.vega == pytest.approx(vega, abs=1e-8)
    mc = greeks(p.program, BS_CTX, "bs", n_paths=N_MC, seed=71)
    assert mc.smoothing == 0.005
    assert abs(mc.smoothing_bias) <= 4 * mc.smoothing_bias_se + 0.002  # smoothing moves the price by < 0.01 %
    assert abs(mc.delta - delta) <= 4 * mc.delta_se + abs(mc.smoothing_bias)
    assert abs(mc.vega - vega) <= 4 * mc.vega_se + abs(mc.smoothing_bias)


def test_slv_greeks_report_delta_but_no_vega():
    from optlab.structured.greeks import greeks

    p = compile_sheet(preset("phoenix", **{"general.maturity_months": 12}), SPX.valuation)
    g = greeks(p.program, SPX, "slv", n_paths=20_000, batch=10_000, params={"cpn": 0.02})
    assert g.vega is None and "recalibration" in g.note
    assert 0 < g.delta < 1.5 and g.delta_se < 0.1


def test_shifted_surface_shifts_implied_vols_and_keeps_local_vol_consistent():
    from optlab.structured.greeks import ShiftedSurface

    s = ShiftedSurface(SPX.surface, 0.01)
    k, T = np.array([-0.3, 0.0, 0.2]), 1.0
    np.testing.assert_allclose(s.iv(k, T), SPX.surface.iv(k, T) + 0.01, rtol=1e-12)
    h = 1e-5
    w, wk, wkk, wT = s.derivatives(k, T)
    np.testing.assert_allclose(wk, (s.w(k + h, T) - s.w(k - h, T)) / (2 * h), rtol=1e-6)
    np.testing.assert_allclose(wT, (s.w(k, T + h) - s.w(k, T - h)) / (2 * h), rtol=1e-5)
    np.testing.assert_allclose(wkk, (s.w(k + h, T) - 2 * w + s.w(k - h, T)) / h**2, rtol=1e-4)


@pytest.mark.slow
def test_quote_with_greeks_uses_the_policy_model():
    q = quote(preset("reverse_convertible", **{"downside.barrier": 0.7}), BS_CTX, control=None, with_greeks=True)
    assert q.greeks.method == "replication" and q.greeks.vega < 0 < q.greeks.delta
    assert "greeks (SURFACE)" in q.table()
    q = quote(
        preset("phoenix", **{"general.maturity_months": 12}),
        SPX,
        control=None,
        with_greeks=True,
        n_paths=20_000,
        batch=10_000,
    )
    assert q.greeks.model == "lv" and q.greeks.vega is not None
    q = quote(
        preset("phoenix", **{"general.maturity_months": 12}),
        SPX,
        model="slv",
        control=None,
        with_greeks=True,
        n_paths=20_000,
        batch=10_000,
    )
    assert q.greeks.model == "slv" and q.greeks.vega is None and "recalibration" in q.table()


def test_quote_script_carries_the_solved_coupon():
    from optlab.structured.script.parser import parse

    q = quote(preset("reverse_convertible"), BS_CTX, control=None)
    cpn = parse(q.script).param_values["cpn"]
    assert cpn == pytest.approx(q.coupon_rate * q.product.coupon_scale, rel=1e-15)
    again = price(q.script, BS_CTX.model("bs"), BS_CTX.market, n_paths=2_000, seed=1, valuation=q.product.valuation)
    assert np.isfinite(again.price)


@pytest.mark.slow
def test_slv_vega_by_recalibration_matches_the_smile_vega_of_a_european_product():
    # a calibrated SLV reprices vanillas, so on a European payoff its vega is the vega of the smile
    from optlab.structured.greeks import greeks

    p = compile_sheet(
        preset("reverse_convertible", **{"downside.barrier": 0.7, "wrapper.solve": "upfront"}), SPX.valuation
    )
    rep = greeks(p.program, SPX, "surface", "replication")
    slv = greeks(p.program, SPX, "slv", n_paths=N_MC, slv_vega=True, n_particles=50_000)
    assert abs(slv.vega - rep.vega) <= 4 * slv.vega_se + 0.01
    assert abs(slv.delta - rep.delta) <= 4 * slv.delta_se + 0.005
    q = quote(
        preset("phoenix", **{"general.maturity_months": 12}),
        SPX,
        model="slv",
        control=None,
        n_paths=20_000,
        batch=10_000,
        with_greeks=True,
    )
    assert q.greeks.vega is None and "slv_vega=True" in q.greeks.note
