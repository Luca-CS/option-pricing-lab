"""Defaults by group and size, what a sheet overrides, the most prudent of a worst-of's groups, and bumps on a basket."""

import copy
import time

import numpy as np
import pytest
from scipy.stats import multivariate_normal, norm

from optlab.app.api import ApiError, Desk
from optlab.app.report import quote_report
from optlab.market import Market
from optlab.marketdata.universe import UNIVERSE, instrument
from optlab.structured import PricingContext, TermSheetError, describe, preset, quote
from optlab.structured.costs import CostInputs
from optlab.structured.defaults import STARTING, merged, resolve, validate_table
from optlab.structured.multiasset import BasketContext, moved_correlation

SPX = PricingContext.from_calibration()
PATHS = 20_000


def within(got, want, se, n=4.0):
    assert abs(got - want) <= n * se, f"{got} against {want}, {abs(got - want) / se:.1f} SE"


def worst_of(*names, **kw):
    return preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": list(names), **kw})


# --- groups and tiers ---
def test_every_loaded_name_has_a_group_with_defaults():
    assert instrument("SPX").group == "index_us" and instrument("SX5E").group == "index_eu" and instrument("AAPL").group == "equity_us"
    for i in UNIVERSE:
        if i.enabled:
            assert i.group in STARTING, i.id


def test_the_size_picks_the_tier():
    small = resolve(None, preset("phoenix", **{"general.notional": 5e6}))
    middle = resolve(None, preset("phoenix", **{"general.notional": 10e6 + 1}))
    assert small.tiers["index_us"]["index"] == 0 and middle.tiers["index_us"]["index"] == 1
    assert small.params["vol"]["value"] == STARTING["index_us"]["tiers"][0]["vol"]
    assert middle.params["vol"]["value"] == STARTING["index_us"]["tiers"][1]["vol"]
    assert small.params["vol"]["starting"] and not small.missing and not small.alerts
    assert "corr" not in small.params  # a single name has no correlation


def test_above_the_last_tier_it_alerts_and_asks_for_the_values():
    big = preset("phoenix", **{"general.notional": 1e9})
    got = resolve(None, big)
    assert got.tiers["index_us"] is None and "above the last tier" in got.alerts[0]
    assert {"vol", "skew", "dividend", "repo", "spot", "slv_mix", "min_spread", "delta_max_autocall"} <= set(got.missing)
    with pytest.raises(ValueError, match="no default"):
        got.inputs()
    filled = big.replace(**{f"costs.{k}": 0.02 for k in got.missing})
    done = resolve(None, filled)
    assert not done.missing and done.inputs().bumps["vol"] == 0.02 and done.params["vol"]["overridden"]


def test_an_override_replaces_the_default_with_its_sign():
    s = preset("phoenix", **{"costs.vol": 0.01, "costs.side": "buy"})
    got = resolve(None, s)
    p = got.params["vol"]
    assert p["value"] == 0.01 and p["overridden"] and p["default"] == STARTING["index_us"]["tiers"][0]["vol"]
    assert got.params["skew"]["overridden"] is False
    i = got.inputs()
    assert i.side == "buy" and i.bumps["vol"] == 0.01 and i.defaults["vol"] == p["default"]


def test_the_section_is_driven_by_the_schema():
    single = next(sec for sec in describe("autocall")["sections"] if sec["name"] == "costs")
    names = [f["name"] for f in single["fields"]]
    assert "corr" not in names and {"side", "vol", "skew", "dividend", "repo", "spot"} <= set(names)
    assert all(f["optional"] for f in single["fields"] if f["name"] != "side")
    wo = next(sec for sec in describe("autocall", worst_of("SPX", "NDX"))["sections"] if sec["name"] == "costs")
    assert "corr" in [f["name"] for f in wo["fields"]]
    with pytest.raises(TermSheetError, match="costs.vol"):
        preset("phoenix", **{"costs.vol": 5.0})
    with pytest.raises(TermSheetError, match="not used"):
        preset("phoenix", **{"costs.corr": 0.01})


# --- the most prudent of the groups ---
def test_one_sign_takes_the_largest_in_size():
    got = resolve(None, worst_of("SPX", "AAPL"))
    us, eq = STARTING["index_us"]["tiers"][0], STARTING["equity_us"]["tiers"][0]
    assert got.params["vol"]["value"] == min(us["vol"], eq["vol"]) and got.params["vol"]["conflict"] is None
    assert got.params["spot"]["value"] == max(us["spot"], eq["spot"]) and set(got.params["vol"]["by_group"]) == {"index_us", "equity_us"}


def test_opposite_signs_are_candidates_and_an_override_settles_them():
    table = {"groups": copy.deepcopy(STARTING)}
    table["groups"]["equity_us"]["tiers"][0]["vol"] = 0.01
    got = resolve(table, worst_of("SPX", "AAPL"))
    assert got.params["vol"]["conflict"] == (STARTING["index_us"]["tiers"][0]["vol"], 0.01)
    assert got.inputs().candidates["vol"] == (STARTING["index_us"]["tiers"][0]["vol"], 0.01)
    again = resolve(table, worst_of("SPX", "AAPL", **{"costs.vol": 0.002}))
    assert not again.inputs().candidates and again.inputs().bumps["vol"] == 0.002


def test_a_stored_table_is_validated_and_completed():
    with pytest.raises(ValueError, match="rising"):
        validate_table({"index_us": {"tiers": [{"max_notional": 2e6}, {"max_notional": 1e6}]}})
    with pytest.raises(ValueError, match="outside"):
        validate_table({"index_us": {"tiers": [{"max_notional": 2e6, "vol": 5.0}]}})
    with pytest.raises(ValueError, match="unknown"):
        validate_table({"index_us": {"tiers": [{"max_notional": 2e6, "gamma": 1.0}]}})
    old = {"groups": {"index_us": {"label": "US", "currency": "USD", "tiers": [{"max_notional": 1e6, "vol": 0.01}]}}}
    m = merged(old)
    assert m["index_us"]["tiers"][0]["vol"] == 0.01 and m["index_us"]["tiers"][0]["skew"] == STARTING["index_us"]["tiers"][0]["skew"]
    assert "equity_us" in m


# --- bumps on a basket ---
def two_names(rho=0.5, vol=0.2):
    markets = {n: Market.flat(100.0, 0.02, 0.01) for n in ("SPX", "NDX")}
    return BasketContext(("SPX", "NDX"), markets, {"SPX": vol, "NDX": vol}, np.array([[1.0, rho], [rho, 1.0]]), SPX.valuation)


def bivariate_digital(F, K, vol, T, rho, D):
    d = (np.log(F / K) - 0.5 * vol**2 * T) / (vol * np.sqrt(T))
    return D * float(multivariate_normal.cdf([d, d], cov=[[1, rho], [rho, 1]]))


def test_moved_correlation_is_repaired_and_keeps_the_diagonal():
    c = moved_correlation([[1.0, 0.9, 0.1], [0.9, 1.0, 0.1], [0.1, 0.1, 1.0]], 0.4)
    assert np.allclose(np.diag(c), 1.0) and np.linalg.eigvalsh(c).min() > -1e-9 and c[0, 1] <= 0.99


def test_the_correlation_line_of_a_worst_of_digital_is_the_bivariate_normal():
    ctx = two_names()
    s = preset("reverse_convertible", **{"general.basket": "worst_of", "general.underlyings": ["SPX", "NDX"], "general.maturity_months": 12,
                                         "wrapper.solve": "upfront", "coupon.type": "none", "downside.barrier": 1.0, "downside.strike": 1.0})
    q = quote(s, ctx, control=None, n_paths=200_000, batch=25_000, costs=CostInputs({"corr": 0.1}))
    # the redemption pays N - N max(1 - worst, 0): its correlation effect is checked with the same simulation against the digital below
    line = q.desk.line("corr")
    assert line.cost > 0 and line.se < q.result.stderr / 2  # more correlation, a better worst-of
    T = 1.0
    D, F = float(ctx.market.D(T)), float(ctx.market.F(T))
    gain = bivariate_digital(F, 100.0, 0.2, T, 0.6, D) - bivariate_digital(F, 100.0, 0.2, T, 0.5, D)
    script = ("underlying SPX\nunderlying NDX\nparam N = 100\nevent t=1.0 pay t=1.0\n  if worst(SPX, NDX) >= 100 then\n"
              "    pay N as upside\n  end\n  pay N as redemption\nend\n")
    from types import SimpleNamespace

    from optlab.structured.cashflows import Swap
    from optlab.structured.costs import _Lines
    from optlab.structured.policy import PricingDecision
    from optlab.structured.profile import profile
    from optlab.structured.script.parser import parse
    from optlab.structured.script.timeline import Clock, build_timeline
    from optlab.structured.script.validate import validate
    program = validate(parse(script))
    prof = profile(program, build_timeline(program, Clock(ctx.valuation)))
    d = PricingDecision("basket", "monte_carlo", "lv", None, "", 400_000, 25_000, 3)
    lines = _Lines(SimpleNamespace(program=program, valuation=ctx.valuation, calendar=None, horizon=T), ctx, Swap(), d, prof, [{}])
    runs = lines.run({"base": {}, "corr": {"corr": 0.1}})
    pairs = runs["corr"].results[0].pairs - runs["base"].results[0].pairs
    within(float(pairs.mean()), 100.0 * gain, float(pairs.std(ddof=1)) / np.sqrt(len(pairs)))


def test_vol_and_forward_bumps_reach_every_name_of_a_basket():
    ctx = two_names()
    s = worst_of("SPX", "NDX")
    q = quote(s, ctx, control=None, n_paths=PATHS, batch=10_000, costs=CostInputs({"vol": -0.005, "dividend": -0.1, "repo": -0.001, "spot": 0.0005, "corr": 0.01}))
    d = q.desk
    assert d.final_price == pytest.approx(98.0, abs=1e-9)
    assert {x.key for x in d.lines} == {"vol", "corr", "dividend", "repo", "spot", "interaction"}
    assert d.line("dividend").cost > 0 and d.line("repo").cost > 0 and d.line("spot").cost > 0
    assert sum(x.cost for x in d.lines) == pytest.approx(d.final_price - d.base_price, abs=1e-10)
    assert not any("arbitrage" in w for w in q.warnings)


def test_with_one_name_moved_the_worst_of_lines_stay_on_common_numbers_and_flat_names_are_said():
    ctx = two_names()
    q = quote(worst_of("SPX", "NDX"), ctx, control=None, n_paths=4000, batch=4000, costs=CostInputs({"skew": 0.01}))
    assert any("flat volatility" in w for w in q.warnings)


def test_opposite_signs_are_measured_and_the_one_against_the_desk_stays():
    ctx = two_names()
    s = worst_of("SPX", "NDX")
    pick = quote(s, ctx, control=None, n_paths=PATHS, batch=10_000, costs=CostInputs({"vol": 0.01}, "sell", {"vol": 0.01}, {"vol": (-0.01, 0.01)}))
    assert pick.desk.line("vol").used == -0.01 and pick.desk.line("vol").cost > 0  # a lower vol raises the note: against the seller
    assert "disagree" in pick.desk.line("vol").note
    bought = quote(s, ctx, control=None, n_paths=PATHS, batch=10_000, costs=CostInputs({"vol": 0.01}, "buy", {"vol": 0.01}, {"vol": (-0.01, 0.01)}))
    assert bought.desk.line("vol").used == 0.01 and bought.desk.line("vol").cost > 0


# --- the desk ---
@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    d = Desk(store=tmp_path_factory.mktemp("costs") / "desk.sqlite")
    yield d
    d.jobs.shutdown()


def wait(desk, job_id, timeout=300):
    t0 = time.perf_counter()
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        assert time.perf_counter() - t0 < timeout
        time.sleep(0.05)
    assert j["state"] == "done", j.get("error")
    return j["result"]


def body(sheet):
    d = sheet.to_dict(json_safe=True)
    t = d.pop("type")
    return {"type": t, "values": d}


def test_the_form_loads_the_defaults_of_a_sheet(desk):
    f = desk.form(body(preset("phoenix", **{"general.notional": 20e6, "costs.vol": -0.02})))
    p = f["costs"]["params"]
    assert p["skew"]["default"] == STARTING["index_us"]["tiers"][1]["skew"] and p["skew"]["starting"] and not p["skew"]["overridden"]
    assert p["vol"]["overridden"] and p["vol"]["value"] == -0.02 and p["vol"]["default"] == STARTING["index_us"]["tiers"][1]["vol"]
    assert f["costs"]["tiers"]["index_us"]["index"] == 1
    assert next(sec for sec in f["form"]["sections"] if sec["name"] == "costs")


def test_a_quote_shows_its_lines_and_the_report_too(desk):
    q = wait(desk, desk.quote({"sheet": body(preset("barrier_reverse_convertible", **{"costs.vol": -0.01})), "options": {"precision": "quick", "model": "lv", "control": False}})["job"])
    d = q["desk"]
    assert d["side"] == "sell" and d["final_price"] == pytest.approx(98.0, abs=1e-6)
    vol = next(x for x in d["lines"] if x["key"] == "vol")
    assert vol["used"] == -0.01 and vol["overridden"] and vol["default"] == STARTING["index_us"]["tiers"][0]["vol"] and vol["se"] > 0
    assert any(x["key"] == "interaction" and not x["in_price"] for x in d["lines"])
    saved = desk.saved_quote(q["quote_id"])
    assert saved["result"]["desk"]["final_price"] == d["final_price"]
    html = quote_report(saved)
    assert "Hedging cost" in html and "Volatility, parallel" in html and "(overridden)" in html
    assert q["sheet"]["costs"]["vol"] == -0.01  # the override stays with the sheet


def test_starting_values_are_against_the_seller_of_a_phoenix(desk):
    q = wait(desk, desk.quote({"sheet": body(preset("phoenix", **{"general.notional": 20e6})), "options": {"precision": "quick", "model": "lv", "control": False}})["job"])
    lines = {x["key"]: x for x in q["desk"]["lines"]}
    assert lines["dividend"]["cost"] > 0 and lines["repo"]["cost"] > 0 and lines["vol"]["cost"] > 0 and lines["spot"]["cost"] > 0
    assert q["desk"]["coupon_final"] < q["desk"]["coupon_base"]


def test_a_forced_model_outside_the_base_says_it_has_no_costs(desk):
    q = wait(desk, desk.quote({"sheet": body(preset("phoenix")), "options": {"precision": "quick", "model": "bs", "control": False}})["job"])
    assert q["desk"] is None and any("no hedging costs" in w for w in q["warnings"])
    q = wait(desk, desk.quote({"sheet": body(preset("phoenix")), "options": {"precision": "quick", "model": "lv", "control": False, "costs": False}})["job"])
    assert q["desk"] is None and not any("hedging" in w for w in q["warnings"])


def test_above_the_last_tier_the_quote_is_refused_with_what_is_missing(desk):
    big = body(preset("phoenix", **{"general.notional": 1e9}))
    assert desk.form(big)["costs"]["missing"]
    with pytest.raises(ApiError, match="above the last tier") as exc:
        desk.quote({"sheet": big, "options": {"precision": "quick"}})
    assert exc.value.status == 422 and exc.value.field == "costs.vol"


def test_the_table_is_edited_and_versioned(desk):
    t = desk.cost_defaults()
    assert t["version"] is None and t["versions"] == [] and {f["name"] for f in t["fields"]} >= {"vol", "spot"}
    groups = copy.deepcopy(t["groups"])
    groups["index_us"]["tiers"][0]["vol"] = -0.003
    saved = desk.put_cost_defaults({"groups": groups})
    assert saved["version"] == 1 and saved["groups"]["index_us"]["tiers"][0]["vol"] == -0.003
    f = desk.form(body(preset("phoenix")))
    assert f["costs"]["params"]["vol"]["default"] == -0.003 and not f["costs"]["params"]["vol"]["starting"]
    groups["index_us"]["tiers"][0]["vol"] = -0.004
    again = desk.put_cost_defaults({"groups": groups})
    assert again["version"] == 2 and [v["version"] for v in again["versions"]] == [2, 1]
    assert desk.repo.defaults(1)["groups"]["index_us"]["tiers"][0]["vol"] == -0.003
    bad = copy.deepcopy(groups)
    bad["index_us"]["tiers"][0]["vol"] = 9.0
    with pytest.raises(ApiError, match="outside"):
        desk.put_cost_defaults({"groups": bad})
    assert desk.cost_defaults()["version"] == 2


def test_black_scholes_helper_is_right():  # the reference above reduces to one name at correlation 1
    F, K, vol, T = 101.0, 100.0, 0.2, 1.0
    d = (np.log(F / K) - 0.5 * vol**2 * T) / (vol * np.sqrt(T))
    assert bivariate_digital(F, K, vol, T, 0.999999, 1.0) == pytest.approx(norm.cdf(d), abs=1e-3)
