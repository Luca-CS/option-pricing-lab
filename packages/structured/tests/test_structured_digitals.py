"""Digitals as call spreads: found in the script, the ramp of a comparison, closed forms, the delta bound and the daily barrier."""

from typing import Any

import numpy as np
import pytest
from _paths import SPX_CALIBRATION
from _quotes import desk_of, some
from _structured_refs import flat_context
from scipy.stats import norm

from optlab.core.blackscholes import black_price
from optlab.structured import PricingContext, preset, quote
from optlab.structured.compiler import compile_sheet
from optlab.structured.costs import CostInputs
from optlab.structured.digitals import find_digitals
from optlab.structured.pricer import price_many
from optlab.structured.quote import wrapper
from optlab.structured.scenario import Scenario
from optlab.structured.script.evaluator import Evaluator, Ramp, _ramped
from optlab.structured.script.timeline import Clock, build_timeline

SPX = PricingContext.from_calibration(SPX_CALIBRATION)
BS = flat_context()
PATHS = 20_000
DELTA: dict[str, Any] = {"coupon": 1.0, "autocall": 1.0, "barrier": 5.0, "upside": 1.0}


def inputs(spread=0.005, side="sell", delta=None):
    return CostInputs(side=side, min_spread=spread, delta_max=delta or DELTA)


def compiled(sheet):
    return compile_sheet(sheet, SPX.valuation)


# --- what is found ---
def test_a_phoenix_has_its_coupons_triggers_and_final_barrier():
    p = compiled(preset("phoenix"))
    found = find_digitals(p, {"cpn": 0.02})
    kinds = [d.kind for d in found]
    assert kinds.count("coupon") == 8 and kinds.count("autocall") == 7 and kinds.count("barrier") == 1
    coupon = next(d for d in found if d.kind == "coupon")
    assert (
        coupon.level == 0.7 and coupon.jump == 0.02 and coupon.width(0.005, DELTA) == pytest.approx(0.02)
    )  # 2 % at 1 % per 1 %
    final = next(d for d in found if d.kind == "barrier")
    assert (
        final.level == 0.6 and final.jump == pytest.approx(0.4) and final.width(0.005, DELTA) == pytest.approx(0.08)
    )  # 8 points at 5 % per 1 %
    assert next(d for d in found if d.kind == "autocall").jump == 0.02
    assert coupon.width(0.05, DELTA) == 0.05  # the minimum spread binds


def test_a_snowball_jumps_by_what_the_call_pays_and_a_step_down_moves_the_level():
    p = compiled(preset("athena", **{"autocall.step_down": 0.05}))
    calls = [d for d in find_digitals(p, {"cpn": 0.02}) if d.kind == "autocall"]
    assert [d.jump for d in calls[:3]] == pytest.approx([0.02, 0.04, 0.06])
    assert [d.level for d in calls[:3]] == pytest.approx([1.0, 0.95, 0.90])


def test_a_barrier_on_the_strike_has_no_jump_and_a_daily_barrier_is_a_shift():
    assert not find_digitals(compiled(preset("reverse_convertible")), {"cpn": 0.02})
    brc = find_digitals(compiled(preset("barrier_reverse_convertible")), {"cpn": 0.02})
    assert (
        [d.kind for d in brc] == ["daily"]
        and brc[0].width(0.01, DELTA) == 0.01
        and brc[0].ramp("down", 0.01, DELTA).shift
    )


def test_an_observation_already_passed_is_not_a_digital():
    running = preset(
        "phoenix", **{"general.strike_date": "2026-01-27", "wrapper.solve": "upfront", "coupon.rate": 0.06}
    )
    found = find_digitals(compiled(running))
    assert all(d.date > SPX.valuation.isoformat() for d in found) and len(found) < 16


# --- the ramp ---
@pytest.mark.parametrize(
    "op,side,want",
    [
        (">=", "up", [0, 0, 0.5, 1, 1]),
        (">=", "down", [0, 0.5, 1, 1, 1]),
        ("<", "up", [1, 1, 0.5, 0, 0]),
        ("<", "down", [1, 0.5, 0, 0, 0]),
    ],
)
def test_a_ramp_lies_on_one_side_of_the_level(op, side, want):
    a = np.array([0.8, 0.9, 1.05, 1.1, 1.2])
    if side == "down":
        a = np.array([0.8, 0.95, 1.0, 1.1, 1.2])
    assert _ramped(op, a, 1.0, Ramp(side, 0.1)) == pytest.approx(want)


def test_a_shift_moves_the_level_and_stays_hard():
    a = np.array([0.85, 0.95, 1.05, 1.15])
    assert _ramped("<", a, 1.0, Ramp("down", 0.1, True)) == pytest.approx([1, 0, 0, 0])
    assert _ramped("<", a, 1.0, Ramp("up", 0.1, True)) == pytest.approx([1, 1, 1, 0])


# --- the closed form ---
def digital_sheet(months=12, amount=0.1):
    return preset(
        "capital_protected",
        **{
            "general.maturity_months": months,
            "upside.type": "digital",
            "upside.strike": 1.0,
            "upside.digital_amount": amount,
        },
    )


def test_an_upside_digital_costs_the_call_spread_on_the_side_against_the_desk():
    sheet = digital_sheet()
    q = quote(sheet, BS, control=None, costs=inputs(0.005, "sell", {**DELTA, "upside": 2.0}))
    p, line = q.product, desk_of(q).line("upside")
    tl = build_timeline(p.program, Clock(p.valuation, p.calendar))
    T = float(tl.event_times[-1])
    pay = float(tl.pay_times[-1])
    D, F, K, W, vol = float(BS.market.D(pay)), float(BS.market.F(T)), 100.0, 5.0, 0.2  # width 0.1 / 2.0 = 5 points
    call = lambda k: float(black_price(F, k, T, vol, 1.0, "call"))  # noqa: E731
    d2 = (np.log(F / K) - 0.5 * vol**2 * T) / (vol * np.sqrt(T))
    hard = 100.0 * 0.1 * D * float(norm.cdf(d2))
    spread = 100.0 * 0.1 * D * (call(K - W) - call(K)) / W  # below the strike: pays more, against the seller
    assert line.detail[0]["side"] == "down" and line.detail[0]["width"] == pytest.approx(0.05)
    assert line.cost == pytest.approx(spread - hard, abs=1e-3) and line.cost > 0 and line.se == 0.0
    bought = quote(sheet, BS, control=None, costs=inputs(0.005, "buy", {**DELTA, "upside": 2.0}))
    assert desk_of(bought).line("upside").detail[0]["side"] == "up" and desk_of(bought).line("upside").cost > 0


def test_the_delta_of_the_payoff_stays_under_the_limit():
    """% of notional per 1 % of spot, on the payoff itself: unbounded for the digital, at the limit for its spread."""
    grid = np.linspace(80.0, 120.0, 8001)
    N = 100.0
    for delta_max in (0.5, 1.0, 2.0):
        for sheet, kind in ((digital_sheet(), "upside"),):
            p = compiled(sheet)
            found = find_digitals(p)
            tl = build_timeline(p.program, Clock(p.valuation, p.calendar))
            limits = {**DELTA, kind: delta_max}
            r = found[0].ramp("down", 0.0, limits)
            k = next(i for i, ev in enumerate(p.program.events) if str(ev.when.value) == found[0].date)
            paths = np.tile(grid[:, None], (1, len(tl.sim_times)))

            def pv(ramps):
                ev = Evaluator(p.program, tl, {}, ramps=ramps, check_redemption=False)  # noqa: B023 (called within this iteration only)
                flows = ev.run(Scenario("SPX", tl.sim_times, paths, 100.0))  # noqa: B023 (called within this iteration only)
                return sum(np.broadcast_to(f, grid.shape) for f in flows.flows.values())

            def slope(v):
                return (
                    np.max(np.abs(np.diff(v) / np.diff(grid))) * 100.0 / N
                )  # % of notional per 1 % of spot at a spot of 100

            assert slope(pv({})) > 10 * delta_max
            assert slope(pv({(k, found[0].line): r})) == pytest.approx(delta_max, rel=1e-2)


@pytest.mark.slow
def test_a_european_barrier_is_the_same_by_replication_and_monte_carlo():
    sheet = preset("reverse_convertible", **{"downside.barrier": 0.8, "wrapper.solve": "upfront", "coupon.rate": 0.05})
    rep = quote(sheet, SPX, control=None, costs=inputs())
    mc = quote(sheet, SPX, model="lv", control=None, n_paths=100_000, batch=25_000, costs=inputs())
    a, b = desk_of(rep).line("barrier"), desk_of(mc).line("barrier")
    assert (
        a.se == 0.0 and a.cost > 0 and b.detail[0]["side"] == a.detail[0]["side"] == "down"
    )  # less often knocked in: against the seller
    assert abs(a.cost - b.cost) < max(4 * b.se, 0.02)


# --- on an autocall ---
@pytest.mark.slow
def test_each_digital_of_an_autocall_has_its_width_side_and_cost_and_the_price_is_on_target():
    q = quote(preset("phoenix"), SPX, model="lv", control=None, n_paths=PATHS, batch=10_000, costs=inputs())
    d = desk_of(q)
    assert d.final_price == pytest.approx(98.0, abs=1e-9)
    assert {x.key for x in d.lines} == {"coupon", "autocall", "barrier", "digital_interaction"}
    assert d.digital_passes <= 3
    cpn = some(q.coupon_rate) * q.product.coupon_scale
    for x in d.lines:
        if x.kind == "digital":
            assert x.cost > 0 and x.se < q.result.stderr and x.note
    coupon = d.line("coupon")
    assert len(coupon.detail) == 8 and all(
        c["width"] == pytest.approx(cpn, rel=2e-2) for c in coupon.detail
    )  # width of the solved coupon
    assert all(c["side"] == "down" for c in coupon.detail)  # paid more often: against the seller
    assert sum(x.cost for x in d.lines) == pytest.approx(d.final_price - d.base_price, abs=1e-10)
    assert some(d.coupon_final) < some(d.coupon_base)


def test_a_daily_barrier_moved_is_the_sheet_with_the_barrier_moved_bit_for_bit():
    sheet = preset(
        "barrier_reverse_convertible", **{"wrapper.solve": "upfront", "coupon.rate": 0.05, "downside.barrier": 0.7}
    )
    q = quote(sheet, SPX, model="lv", control=None, n_paths=PATHS, batch=10_000, costs=inputs(0.01))
    line = desk_of(q).line("daily")
    assert line.detail[0]["side"] == "down" and line.detail[0]["width"] == 0.01 and line.cost > 0
    moved = sheet.replace(**{"downside.barrier": 0.69})
    p = compile_sheet(moved, SPX.valuation)
    again = price_many(
        p.program,
        SPX.model("lv"),
        SPX.market,
        [{}],
        wrapper(moved, p),
        PATHS,
        q.decision.seed,
        10_000,
        valuation=p.valuation,
        calendar=p.calendar,
    )[0]
    assert again.price == pytest.approx(desk_of(q).final_price, abs=1e-9)


@pytest.mark.slow
def test_a_worst_of_and_workers_take_the_digitals_too():
    from optlab.core.market import Market
    from optlab.structured.multiasset import BasketContext

    markets = {n: Market.flat(100.0, 0.02, 0.01) for n in ("SPX", "NDX")}
    ctx = BasketContext(
        ("SPX", "NDX"), markets, {"SPX": 0.3, "NDX": 0.35}, np.array([[1.0, 0.7], [0.7, 1.0]]), SPX.valuation
    )
    s = preset(
        "phoenix",
        **{
            "general.basket": "worst_of",
            "general.underlyings": ["SPX", "NDX"],
            "general.maturity_months": 12,
            "coupon.barrier": 0.8,
            "downside.barrier": 0.8,
        },
    )
    a = quote(s, ctx, control=None, n_paths=4000, batch=2000, costs=inputs())
    b = quote(s, ctx, control=None, n_paths=4000, batch=2000, costs=inputs(), workers=2)
    assert desk_of(a).final_price == pytest.approx(98.0, abs=1e-9) and desk_of(a).line("coupon").cost > 0
    assert a.coupon_rate == b.coupon_rate and desk_of(a).line("coupon").cost == desk_of(b).line("coupon").cost
