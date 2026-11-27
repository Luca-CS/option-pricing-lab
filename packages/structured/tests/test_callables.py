"""Issuer-callable products: the mask, the compiler's call dates and notice, the presets, the coupon solved by
refitting the policy, and the compiled products against the PDE with memory and knock-in layers (4 SE plus the
policy's bias)."""

import numpy as np
import pytest
from _callable_refs import callable_layers
from _structured_refs import FLAT, VOL, Q, R, flat_context

from optlab.core import BlackScholesModel
from optlab.structured.compiler import compile_sheet
from optlab.structured.costs import CostInputs
from optlab.structured.presets import preset
from optlab.structured.pricer import price_many
from optlab.structured.profile import profile
from optlab.structured.quote import quote
from optlab.structured.schema import TermSheetError, describe
from optlab.structured.script.nodes import IssuerCall
from optlab.structured.script.timeline import Clock, build_timeline

VALUATION = "2026-10-27"
CALLABLES = (
    "callable_reverse_convertible",
    "callable_barrier_reverse_convertible",
    "callable_phoenix",
    "callable_fixed_coupon_note",
)
BIAS = 0.10  # % of notional: the policy's bias allowed on top of 4 SE


def times_of(product):
    timeline = build_timeline(product.program, Clock(product.valuation, product.calendar))
    return timeline


@pytest.mark.parametrize("name", CALLABLES)
def test_each_callable_preset_compiles_with_its_calls_and_state(name):
    product = compile_sheet(preset(name), VALUATION)
    assert product.program.has_issuer_call
    assert product.program.states
    assert "call pay" in product.script
    prof = profile(product.program, times_of(product))
    assert prof.product_class == "issuer_callable"


def test_the_notice_date_comes_before_each_call_and_holds_the_state():
    product = compile_sheet(preset("callable_phoenix"), VALUATION)
    assert product.program.states == ("notice_perf", "missed")
    events = product.script.split("\nevent ")
    notices = [e for e in events if "notice_perf =" in e]
    calls = [e for e in events if "call pay" in e]
    assert len(notices) == len(calls) == 8  # quarterly from one year to the date before maturity, 3 years
    no_notice = compile_sheet(preset("callable_phoenix", **{"issuer_call.notice_days": 0}), VALUATION)
    assert no_notice.program.states == ("perf", "missed")


def test_a_daily_knock_in_is_part_of_the_state():
    product = compile_sheet(preset("callable_barrier_reverse_convertible"), VALUATION)
    assert product.program.states == ("notice_perf", "notice_breached")


def test_the_call_dates_are_checked():
    with pytest.raises(TermSheetError, match="must be a call date"):
        preset("callable_phoenix", **{"issuer_call.first_call_months": 13})
    with pytest.raises(TermSheetError, match="must be a call date"):
        preset("callable_phoenix", **{"issuer_call.first_call_months": 36})


def test_the_builder_describes_the_issuer_call():
    d = describe("callable")
    names = [s["name"] for s in d["sections"]]
    assert "issuer_call" in names
    assert "autocall" not in names


def _flat_sheet(name, **changes):
    """No pay lag and no notice, so the PDE's dates are the script's."""
    return preset(name, **{"general.pay_lag": 0, "issuer_call.notice_days": 0, "wrapper.solve": "upfront", **changes})


def _pde(product, coupon, barrier, final, memory=False, ki=None):
    timeline = times_of(product)
    pays = [t for t in timeline.event_times if t > 0]
    T = pays[-1]
    calls = {}
    for ev, t in zip(product.program.events, timeline.event_times):
        if any(isinstance(s, IssuerCall) for s in ev.body):
            calls[t] = 1.0
    daily = None
    if ki is not None:
        daily = [t for t in timeline.sim_times if 0 < t <= T]
    return 100 * callable_layers(
        T, pays, coupon, barrier, calls, final, memory=memory, ki=ki, daily=daily, r=R, q=Q, vol=VOL
    )


def test_a_callable_phoenix_with_memory_against_the_pde():
    sheet = _flat_sheet("callable_phoenix", **{"coupon.rate": 0.09})
    product = compile_sheet(sheet, VALUATION)
    (res,) = price_many(
        product.program,
        BlackScholesModel(VOL),
        FLAT,
        [{}],
        n_paths=100_000,
        seed=5,
        valuation=product.valuation,
        calendar=product.calendar,
    )
    ref = _pde(product, 0.09 / 4, 0.7, lambda p, k: np.where(p < 0.6, p, 1.0), memory=True)
    assert ref - 4 * res.stderr < res.price < ref + 4 * res.stderr + BIAS, (res.price, ref, res.stderr)


def test_a_callable_brc_with_a_daily_knock_in_against_the_pde():
    sheet = _flat_sheet("callable_barrier_reverse_convertible", **{"coupon.rate": 0.08})
    product = compile_sheet(sheet, VALUATION)
    (res,) = price_many(
        product.program,
        BlackScholesModel(VOL),
        FLAT,
        [{}],
        n_paths=100_000,
        seed=6,
        valuation=product.valuation,
        calendar=product.calendar,
    )
    ref = _pde(product, 0.02, 0.0, lambda p, k: np.where(k, np.minimum(p, 1.0), 1.0), ki=0.7)
    assert ref - 4 * res.stderr < res.price < ref + 4 * res.stderr + BIAS, (res.price, ref, res.stderr)


def test_the_coupon_is_solved_by_refitting_the_policy():
    ctx = flat_context()
    q = quote(preset("callable_phoenix"), ctx, n_paths=40_000, control=None)
    passes = q.result.timings["solve_passes"]
    assert 2 <= len(passes) <= 6
    assert abs(passes[-1] - passes[-2]) < 1e-5
    # the solved coupon prices the note at its target under a policy fitted at that coupon
    product = compile_sheet(preset("callable_phoenix"), ctx.valuation)
    (at,) = price_many(
        product.program,
        ctx.model("lv", 3.0),
        ctx.market,
        [{"cpn": passes[-1]}],
        n_paths=40_000,
        seed=q.decision.seed,
        batch=q.decision.batch,
        valuation=product.valuation,
        calendar=product.calendar,
    )
    assert abs(at.price - 98.0) < 4 * at.stderr


def test_the_desk_price_of_a_callable_refits_each_line_at_the_solved_coupon():
    """Every line of the desk price fits its own policy at the coupon (the base's, SLV's, the overvol's), and the coupon
    is solved again until it stops moving; the client price is then the target."""
    ctx = flat_context()
    q = quote(
        preset("callable_phoenix"),
        ctx,
        model="lv",
        control=None,
        n_paths=20_000,
        batch=10_000,
        costs=CostInputs({"vol": 0.005}),
    )
    desk = q.desk
    assert desk is not None and len(desk.call_passes) >= 2
    assert abs(desk.call_passes[-1] - desk.call_passes[-2]) < 1e-5 * 0.25
    assert abs(desk.final_price - 98.0) < 1e-6
    assert any(line.key == "vol" for line in desk.lines)
