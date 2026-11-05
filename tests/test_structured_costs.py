"""The desk's lines on a single name: the bumped surface, the bumped forward, costs on common random numbers against closed forms."""

import numpy as np
import pytest
from scipy.stats import norm

from optlab.models import LocalVolModel, simulate
from optlab.structured import PricingContext, preset, quote
from optlab.structured.compiler import compile_sheet
from optlab.structured.costs import CostInputs, forward_bumped
from optlab.structured.pricer import price_many
from optlab.structured.quote import wrapper
from optlab.structured.script.timeline import Clock, build_timeline
from optlab.structured.surfaces import BumpedSurface, surface_arbitrage

SPX = PricingContext.from_calibration()
PATHS = 40_000
SKEW = 0.01


def within(got, want, se, n=4.0):
    assert abs(got - want) <= n * se, f"{got} against {want}, {abs(got - want) / se:.1f} SE"


# --- the surface ---
@pytest.mark.parametrize("vol,skew", [(0.01, 0.0), (0.0, 0.02), (-0.005, -0.01)])
def test_bumped_surface_derivatives_are_exact(vol, skew):
    s = BumpedSurface(SPX.surface, vol, skew)
    k, T, h = np.linspace(-0.4, 0.3, 15), 0.8, 1e-4
    w, wk, wkk, wT = s.derivatives(k, T)
    assert np.allclose(wk, (s.w(k + h, T) - s.w(k - h, T)) / (2 * h), atol=1e-6)
    assert np.allclose(wkk, (s.w(k + h, T) - 2 * w + s.w(k - h, T)) / h**2, atol=1e-3)
    assert np.allclose(wT, (s.w(k, T + h) - s.w(k, T - h)) / (2 * h), atol=1e-5)


def test_skew_moves_the_90_110_gap_at_one_year_and_scales_with_maturity():
    gap = lambda s, T: float(s.iv(np.log(0.9), T) - s.iv(np.log(1.1), T))  # noqa: E731
    bumped = BumpedSurface(SPX.surface, 0.0, 0.02)
    assert gap(bumped, 1.0) - gap(SPX.surface, 1.0) == pytest.approx(0.02, abs=1e-10)
    assert gap(bumped, 4.0) - gap(SPX.surface, 4.0) == pytest.approx(0.01, abs=1e-10)  # sqrt(1 / T)
    assert float(bumped.iv(0.0, 1.0)) == pytest.approx(float(SPX.surface.iv(0.0, 1.0)), abs=1e-12)  # rotates around the forward
    assert float(bumped.theta(1.0)) == pytest.approx(float(SPX.surface.theta(1.0)), abs=1e-12)


def test_a_parallel_shift_is_the_shifted_surface():
    from optlab.structured.greeks import ShiftedSurface
    k = np.linspace(-0.5, 0.4, 11)
    for a, b in zip(BumpedSurface(SPX.surface, 0.01).derivatives(k, 1.3), ShiftedSurface(SPX.surface, 0.01).derivatives(k, 1.3)):
        assert np.allclose(a, b, atol=1e-13)


def test_reasonable_bumps_add_no_arbitrage_and_an_absurd_one_is_seen():
    assert surface_arbitrage(SPX.surface, 2.0)["butterfly_free"]
    for vol, skew in ((0.02, 0.0), (0.0, 0.02), (0.0, -0.02), (-0.01, 0.01)):
        got = surface_arbitrage(BumpedSurface(SPX.surface, vol, skew), 2.0)
        assert got["butterfly_free"] and got["calendar_free"], (vol, skew, got)
    assert not surface_arbitrage(BumpedSurface(SPX.surface, 0.0, 0.5), 2.0)["butterfly_free"]


# --- the forward ---
def test_forward_bumps_are_exact_and_keep_the_discounts():
    m = SPX.market
    T = np.array([0.25, 1.0, 2.0])
    carried = np.log(m.spot / (m.F(T) * m.D(T)))
    assert np.allclose(forward_bumped(m, dividend=0.1).F(T), m.F(T) * np.exp(-0.1 * carried), rtol=1e-10)
    assert np.allclose(forward_bumped(m, repo=0.001).F(T), m.F(T) * np.exp(-0.001 * T), rtol=1e-10)
    assert np.allclose(forward_bumped(m, spot=0.01).F(T), 1.01 * m.F(T), rtol=1e-12)
    assert np.allclose(forward_bumped(m, repo=0.001, dividend=0.1).D(T), m.D(T))
    assert forward_bumped(m, spot=0.01).spot == pytest.approx(1.01 * m.spot)


def test_slv_on_the_spot_draws_of_local_vol_has_the_same_law_and_common_numbers():
    from dataclasses import replace

    times, seed = np.array([0.5, 1.0, 2.0]), 77
    lv = simulate(LocalVolModel(SPX.surface), SPX.market, times, 40_000, seed)
    plain = simulate(SPX.slv, SPX.market, times, 40_000, seed)
    driven = simulate(replace(SPX.slv, spot_driven=True), SPX.market, times, 40_000, seed)
    def corr(a, b):
        return float(np.corrcoef(np.log(a[:, -1]), np.log(b[:, -1]))[0, 1])
    assert corr(driven, lv) > 0.85 and abs(corr(plain, lv)) < 0.05
    for j, K in enumerate((95.0, 100.0, 105.0)):  # the same vanilla, within the error of the two estimates
        a, b = np.maximum(driven[:, j] - K, 0.0), np.maximum(plain[:, j] - K, 0.0)
        within(a.mean(), b.mean(), np.hypot(a.std(), b.std()) / np.sqrt(len(a)))
    assert np.array_equal(simulate(SPX.slv, SPX.market, times, 40_000, seed), plain)  # the default keeps its numbers


# --- the costs ---
def sheet(name="phoenix", **kw):
    return preset(name, **kw)


def lv_quote(s, costs=None, n_paths=PATHS, **kw):
    return quote(s, SPX, model="lv", control=None, n_paths=n_paths, batch=10_000, costs=costs, **kw)


def test_no_bump_is_the_plain_quote_bit_for_bit():
    plain = lv_quote(sheet())
    q = lv_quote(sheet(), CostInputs())
    assert q.coupon_rate == plain.coupon_rate and q.coupon_rate_se == pytest.approx(plain.coupon_rate_se, rel=1e-12)
    assert q.desk.lines == [] and q.desk.final_price == pytest.approx(98.0, abs=1e-9) and q.desk.base_price == q.desk.final_price


def test_every_bump_has_a_small_error_and_the_client_price_is_the_target():
    bumps = {"vol": 0.005, "skew": SKEW, "dividend": 0.1, "repo": 0.001, "spot": 0.0005}
    q = lv_quote(sheet(), CostInputs(bumps))
    d = q.desk
    assert d.final_price == pytest.approx(q.target, abs=1e-9) and q.target == pytest.approx(98.0)
    assert d.base_price == pytest.approx(q.result.price, abs=1e-12)
    keys = [x.key for x in d.lines]
    assert keys == ["vol", "skew", "dividend", "repo", "spot", "interaction"]
    for x in d.lines:
        assert x.se < 0.5 * q.result.stderr, (x.key, x.se, q.result.stderr)  # common random numbers
    for x in d.lines[:-1]:
        assert abs(x.cost) > 2 * x.se and x.used == bumps[x.key]
    # more dividends and more repo lower the forward: the note is worth less
    assert d.line("dividend").cost < 0 and d.line("repo").cost < 0 and d.line("vol").cost < 0
    # the lines add up to the difference with the base
    assert sum(x.cost for x in d.lines) == pytest.approx(d.final_price - d.base_price, abs=1e-10)
    # the coupon moves against the cost: less value, a higher coupon for the same client price
    assert q.coupon_rate > d.coupon_base and d.coupon_final == q.coupon_rate
    assert d.line("vol").coupon_bp > 0
    # an independent run of the whole bumped market at the solved coupon gives the client price
    p = compile_sheet(sheet(), SPX.valuation)
    market = forward_bumped(SPX.market, dividend=0.1, repo=0.001, spot=0.0005)
    times = build_timeline(p.program, Clock(p.valuation, p.calendar)).sim_times
    again = price_many(p.program, LocalVolModel(BumpedSurface(SPX.surface, 0.005, SKEW)), SPX.market, [{"cpn": q.coupon_rate * p.coupon_scale}],
                       wrapper(sheet(), p), PATHS, q.decision.seed, 10_000, valuation=p.valuation, calendar=p.calendar, initial_fixing=SPX.market.spot,
                       spot_factors=[market.F(times) / SPX.market.F(times)])[0]
    assert again.price == pytest.approx(d.final_price, abs=1e-9)


def test_a_vanilla_put_costs_what_black_scholes_says():
    """European put at 90 %: the cost of each bump against the closed form on the bumped smile."""
    T, K = 1.0, 90.0
    m = SPX.market
    script = (f"underlying SPX\nparam N = 100\nevent t={T} pay t={T}\n  pay max({K} - SPX, 0) as upside\n"
              "  pay N as redemption\nend\n")
    def black_put(F, vol):
        d1 = (np.log(F / K) + 0.5 * vol**2 * T) / (vol * np.sqrt(T))
        return float(m.D(T)) * (K * norm.cdf(-(d1 - vol * np.sqrt(T))) - F * norm.cdf(-d1))
    F = float(m.F(T))
    def vol(surface, F_):
        return float(surface.iv(np.log(K / F_), T))
    base = black_put(F, vol(SPX.surface, F))
    from optlab.structured.costs import _Lines
    from optlab.structured.policy import PricingDecision
    from optlab.structured.profile import profile
    from optlab.structured.script.parser import parse
    from optlab.structured.script.validate import validate
    from optlab.structured.cashflows import Swap
    from types import SimpleNamespace
    program = validate(parse(script))
    clock = Clock(SPX.valuation)
    prof = profile(program, build_timeline(program, clock))
    bumps = {"vol": 0.01, "skew": 0.02, "dividend": 0.2, "repo": 0.005, "spot": 0.01}
    product = SimpleNamespace(program=program, valuation=SPX.valuation, calendar=None, horizon=T)
    for method, model in (("monte_carlo", "lv"), ("replication", "surface")):
        d = PricingDecision("european", method, model, None, "", 200_000, 25_000, 11)
        lines = _Lines(product, SPX, Swap(), d, prof, [{}])
        single = lines.run({"base": {}, **{k: {k: v} for k, v in bumps.items()}})
        at = single["base"].results[0]
        want = {"vol": black_put(F, vol(SPX.surface, F) + 0.01) - base,
                "skew": black_put(F, vol(BumpedSurface(SPX.surface, 0.0, 0.02), F)) - base}
        for key, value in (("dividend", {"dividend": 0.2}), ("repo", {"repo": 0.005}), ("spot", {"spot": 0.01})):
            F2 = float(forward_bumped(m, **value).F(T))
            want[key] = black_put(F2, vol(SPX.surface, F2)) - base
        for key, cost in want.items():
            r = single[key].results[0]
            if method == "replication":
                assert r.price - at.price == pytest.approx(cost, abs=1e-4), key
            else:
                pairs = r.pairs - at.pairs
                within(float(pairs.mean()), cost, max(float(pairs.std(ddof=1)) / np.sqrt(len(pairs)), 5e-3))


def test_a_european_sheet_has_no_noise_on_its_lines():
    s = preset("reverse_convertible", **{"downside.barrier": 0.8})
    q = quote(s, SPX, control=None, costs=CostInputs({"vol": 0.005, "skew": 0.01}))
    assert q.decision.method == "replication"
    assert all(x.se == 0.0 for x in q.desk.lines) and q.desk.final_se == 0.0
    assert q.desk.final_price == pytest.approx(98.0, abs=1e-9)
    assert q.desk.line("vol").cost < 0 and abs(q.desk.line("interaction").cost) < 0.05


def test_the_buyers_cost_has_the_other_sign_and_the_same_price():
    bumps = {"vol": 0.005}
    sold, bought = lv_quote(sheet(), CostInputs(bumps, "sell")), lv_quote(sheet(), CostInputs(bumps, "buy"))
    assert sold.coupon_rate == bought.coupon_rate  # a bump applies as the trader signed it
    assert sold.desk.line("vol").cost == pytest.approx(-bought.desk.line("vol").cost)
    with pytest.raises(ValueError, match="side"):
        CostInputs({}, "lend")
    with pytest.raises(ValueError, match="bump"):
        CostInputs({"gamma": 0.1})


def test_upfront_mode_is_the_client_price():
    s = sheet(**{"wrapper.solve": "upfront", "coupon.rate": 0.06})
    plain = lv_quote(s)
    q = lv_quote(s, CostInputs({"vol": 0.01, "repo": 0.001}))
    assert q.result.price == plain.result.price and q.upfront == pytest.approx(q.desk.final_price)
    assert q.desk.final_price < plain.result.price and q.desk.line("vol").coupon_bp is None and q.coupon_rate is None


def test_a_swap_and_a_running_product_take_their_lines_too():
    swap = sheet(**{"wrapper.format": "swap"})
    q = lv_quote(swap, CostInputs({"vol": 0.005}))
    assert q.desk.final_price == pytest.approx(-2.0, abs=1e-9)
    running = sheet(**{"wrapper.solve": "upfront", "coupon.rate": 0.06, "general.strike_date": "2026-08-10"})
    fix = {"SPX": {"2026-08-10": 100.0}}
    got = quote(running, SPX, model="lv", control=None, n_paths=PATHS, batch=10_000, fixings=fix, costs=CostInputs({"spot": 0.01}))
    assert got.desk.line("spot").cost > 0 and got.desk.final_price > got.result.price  # the fixing stays: a higher spot is worth more


def test_workers_give_the_same_lines(tmp_path):
    bumps = {"vol": 0.005, "repo": 0.001}
    a = lv_quote(sheet(), CostInputs(bumps), n_paths=20_000)
    b = lv_quote(sheet(), CostInputs(bumps), n_paths=20_000, workers=2)
    assert a.coupon_rate == b.coupon_rate and a.desk.line("vol").cost == b.desk.line("vol").cost


def test_an_extreme_bump_warns_and_the_table_prints():
    q = lv_quote(sheet(), CostInputs({"skew": 0.5}), n_paths=4000)
    assert any("arbitrage" in w for w in q.warnings)
    q = lv_quote(sheet(), CostInputs({"vol": 0.005}), n_paths=4000)
    assert "Volatility, parallel" in q.table() and "client price (sell)" in q.table()


# --- provisions of model ---
def policy_quote(s, costs=None, n_paths=PATHS, **kw):
    return quote(s, SPX, control="default", n_paths=n_paths, batch=10_000, costs=costs if costs is not None else CostInputs(), **kw)


def test_an_autocall_is_local_vol_plus_the_slv_gap_when_it_goes_against_the_desk():
    from dataclasses import replace

    q = policy_quote(sheet())
    d = q.desk
    line = d.line("slv")
    assert q.decision.model == "lv" and q.decision.provisions == ("slv",) and line.kind == "model"
    assert line.cost > 4 * line.se and line.gap == pytest.approx(line.cost) and line.used == SPX.slv.mix
    assert d.final_price == pytest.approx(98.0, abs=1e-9) and d.final_price == pytest.approx(d.base_price + line.cost, abs=1e-9)
    assert d.coupon_final < d.coupon_base and line.coupon_bp < 0
    assert q.result.price == pytest.approx(d.base_price)  # the result stays the base
    p = compile_sheet(sheet(), SPX.valuation)
    kw = dict(fmt=wrapper(sheet(), p), n_paths=PATHS, seed=q.decision.seed, batch=10_000, valuation=p.valuation, calendar=p.calendar, initial_fixing=SPX.market.spot)
    cpn = {"cpn": q.coupon_rate * p.coupon_scale}
    alone = price_many(p.program, replace(SPX.slv, spot_driven=True), SPX.market, [cpn], **kw)[0]
    lv = price_many(p.program, SPX.model("lv"), SPX.market, [cpn], **kw)[0]
    assert lv.price == pytest.approx(d.base_price, abs=1e-9) and alone.price - lv.price == pytest.approx(line.cost, abs=1e-9)
    assert line.se < 0.75 * np.hypot(alone.stderr, lv.stderr)  # the two models share their spot draws
    assert q.control.price == pytest.approx(alone.price) and q.decision.control == "slv"  # no second simulation for the control


def test_a_gap_in_the_desks_favour_gives_no_provision_and_the_buyer_takes_the_other_side():
    s = sheet()
    sold, bought = policy_quote(s, CostInputs(side="sell")), policy_quote(s, CostInputs(side="buy"))
    a, b = sold.desk.line("slv"), bought.desk.line("slv")
    assert a.cost > 0 and b.cost == 0.0 and b.gap < 0 and "favour" in b.note
    assert bought.desk.final_price == pytest.approx(bought.desk.base_price, abs=1e-12)  # nothing in the price
    assert bought.coupon_rate == pytest.approx(bought.desk.coupon_base, abs=1e-12)
    assert b.se > 0


def test_the_solve_settles_which_provisions_are_on_and_says_how_many_passes():
    for margin in (0.0, 0.02, 0.1):
        q = policy_quote(sheet(**{"wrapper.margin": margin}), n_paths=20_000)
        assert q.desk.final_price == pytest.approx(100 * (1 - margin), abs=1e-9)
        assert q.desk.passes <= 3


def test_a_european_product_has_no_provision_and_a_forced_model_neither():
    q = quote(preset("reverse_convertible", **{"downside.barrier": 0.8}), SPX, control=None, costs=CostInputs())
    assert q.desk.lines == [] and q.decision.provisions == ()
    q = lv_quote(sheet(), CostInputs())
    assert q.decision.forced and q.decision.provisions == () and q.desk.lines == []


def test_a_mix_of_zero_is_local_vol_within_the_error_of_the_leverage():
    q = policy_quote(sheet(), CostInputs(mix=0.0), n_paths=20_000)
    assert abs(q.desk.line("slv").gap) < 0.25 and q.desk.line("slv").used == 0.0
    other = policy_quote(sheet(), CostInputs(mix=SPX.slv.mix / 2), n_paths=20_000)
    assert other.desk.line("slv").used == SPX.slv.mix / 2


def test_a_mix_and_bumps_add_up_in_the_summary():
    d = policy_quote(sheet(), CostInputs({"vol": -0.005, "repo": -0.001}), n_paths=20_000).desk
    assert [x.key for x in d.lines] == ["slv", "vol", "repo", "interaction"]
    assert sum(x.cost for x in d.lines) == pytest.approx(d.final_price - d.base_price, abs=1e-10)
    assert "Model: SLV" in d.table()
