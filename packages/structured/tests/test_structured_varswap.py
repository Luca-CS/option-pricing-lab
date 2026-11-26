"""Variance and volatility swaps and their corridor versions: the realised variance, the script's window, the replication on
the smile, and the quote of the presets (fair strike solved, booked into the sheet)."""

import datetime as dt

import numpy as np
import pytest
from _paths import SPX_CALIBRATION
from scipy.special import gammaln

from optlab.core import BlackScholesModel, Market
from optlab.structured import preset
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.pricer import price
from optlab.structured.quote import quote
from optlab.structured.script.lexer import ScriptError
from optlab.structured.script.parser import parse
from optlab.structured.script.printer import to_text
from optlab.structured.script.validate import validate
from optlab.structured.varswap import ANNUAL, fair_variance, realised_variance

CTX = PricingContext.from_calibration(SPX_CALIBRATION)
SIGMA, N_STEPS = 0.2, 252


def within(x, ref, se, k=4.0):
    assert abs(x - ref) <= k * se + 1e-12, f"{x} vs {ref}: {abs(x - ref) / se:.1f} SE"


def test_the_realised_variance_of_a_window_and_of_its_corridor():
    f = np.array([[100.0, 101.0, 99.0, 103.0]])
    r2 = np.log(f[0, 1:] / f[0, :-1]) ** 2
    assert realised_variance(f)[0] == pytest.approx(ANNUAL / 3 * r2.sum())
    # a return counts in the corridor when the fixing before it is inside: 100 and 99 are, 101 is not
    assert realised_variance(f, 0.0, 100.0)[0] == pytest.approx(ANNUAL / 3 * (r2[0] + r2[2]))
    assert realised_variance(f, 100.5, None)[0] == pytest.approx(ANNUAL / 3 * r2[1])
    with pytest.raises(ValueError, match="two fixings"):
        realised_variance([[100.0]])


def test_the_replication_reads_a_flat_vol_back_and_a_corridor_takes_its_share():
    m = Market.flat(100.0, 0.02, 0.01)
    assert fair_variance(SIGMA, m, 1.0) == pytest.approx(SIGMA**2, rel=1e-5)  # trapezoids on 4,001 points
    whole = fair_variance(CTX.surface, CTX.market, 1.0)
    down = fair_variance(CTX.surface, CTX.market, 1.0, upper=float(CTX.market.F(1.0)))
    up = fair_variance(CTX.surface, CTX.market, 1.0, lower=float(CTX.market.F(1.0)))
    assert down + up == pytest.approx(whole, rel=1e-3)  # the two corridors share the log contract
    assert down > up  # the skew: the downside variance is the larger part
    atm = float(CTX.surface.iv(0.0, 1.0))
    assert np.sqrt(whole) > atm  # the smile's wings add to the at-the-money variance


VAR_SCRIPT = """underlying SPX
event t=0
  S0 = SPX
end
event t=1 pay t=1
  v = variance(SPX, t=0, t=1, n={n}{bounds})
  pay {payoff} as upside
  pay 0 as redemption
end
"""


def _script(payoff: str, bounds: str = "", n: int = N_STEPS) -> str:
    return VAR_SCRIPT.format(payoff=payoff, bounds=bounds, n=n)


def test_a_variance_window_parses_prints_and_checks_its_bounds():
    text = _script("v", ", 0.9 * S0, 1.1 * S0")
    program = validate(parse(text))
    assert validate(parse(to_text(program))) == program
    with pytest.raises(ScriptError, match="corridor"):
        validate(
            parse(
                _script("lowest(SPX, t=0, t=1, n=10)").replace(
                    "lowest(SPX, t=0, t=1, n=10)", "lowest(SPX, t=0, t=1, n=10, 90, 110)"
                )
            )
        )
    with pytest.raises(ScriptError, match="before it is assigned"):
        validate(parse(_script("v", ", 0.9 * S1, 1.1 * S0")))


def test_a_var_swap_under_black_scholes_pays_its_expected_variance_and_a_vol_swap_its_convexity():
    m = Market.flat(100.0, 0.0, 0.0)
    model = BlackScholesModel(SIGMA)
    var = price(_script("v"), model, m, n_paths=40_000, seed=7).value
    dt_ = 1.0 / N_STEPS
    drift = -0.5 * SIGMA**2 * dt_
    expected = ANNUAL * (SIGMA**2 * dt_ + drift**2)  # each of the n equal steps, annualised on 252
    within(var.price, expected, var.stderr)
    vol = price(_script("sqrt(v)"), model, m, n_paths=40_000, seed=7).value
    # sqrt of a scaled chi-square with n degrees of freedom (the drift's non-centrality is far below the noise)
    scale = np.sqrt(ANNUAL * SIGMA**2 * dt_ / N_STEPS)
    chi = np.sqrt(2.0) * np.exp(gammaln((N_STEPS + 1) / 2) - gammaln(N_STEPS / 2))
    within(vol.price, scale * chi, vol.stderr)
    assert vol.price < SIGMA  # the convexity: E[sqrt(v)] < sqrt(E[v])


def test_under_local_vol_the_var_swap_finds_the_replicated_variance_and_its_corridor_too():
    T = 1.0
    model = CTX.model("lv", T)
    F = float(CTX.market.F(T))
    D = float(CTX.market.D(T))  # the price is discounted, the replicated variance is not
    rep = fair_variance(CTX.surface, CTX.market, T)
    mc = price(_script("v"), model, CTX.market, n_paths=20_000, seed=3).value
    # daily sampling against continuous monitoring: within 2 % of the variance, beyond the noise (measured: 1 %)
    assert abs(mc.price / D - rep) < 0.02 * rep + 4 * mc.stderr / D
    corridor = price(_script("v", f", 0 * S0, {F} + 0 * S0"), model, CTX.market, n_paths=20_000, seed=3).value
    rep_down = fair_variance(CTX.surface, CTX.market, T, upper=F)
    assert abs(corridor.price / D - rep_down) < 0.04 * rep_down + 4 * corridor.stderr / D


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("variance_swap", "variance"),
        ("volatility_swap", "volatility"),
        ("corridor_variance_swap", "variance"),
        ("corridor_volatility_swap", "volatility"),
    ],
)
def test_a_swap_preset_compiles_and_its_quote_solves_the_fair_strike(name, kind):
    sheet = preset(name, **{"general.maturity_months": 6})
    assert sheet.get("variance.type") == kind and sheet.get("wrapper.format") == "swap"
    product = compile_sheet(sheet, dt.date(2024, 1, 2))
    assert "variance(SPX," in product.script and product.coupon_param in ("kvar", "kvol")
    q = quote(sheet, CTX, n_paths=8_000)
    head = q.strike
    assert head is not None and 0.05 < head["value"] < 0.8 and head["se"] > 0
    if name == "variance_swap":  # against the replication: discrete monitoring, the cap and the margin
        T = product.horizon
        assert abs(head["value"] - np.sqrt(fair_variance(CTX.surface, CTX.market, T))) < 0.02
        assert q.reference is not None and q.reference["strike"] == pytest.approx(
            np.sqrt(fair_variance(CTX.surface, CTX.market, T)), rel=1e-9
        )
    if name == "volatility_swap":  # below the variance swap's strike: the convexity
        var = quote(preset("variance_swap", **{"general.maturity_months": 6}), CTX, n_paths=8_000).strike
        assert var is not None and head["value"] < var["value"]


def test_a_var_swap_that_matured_settles_its_payoff_in_the_lifecycle():
    from optlab.structured.calendars import calendar
    from optlab.structured.lifecycle import events

    start = dt.date(2024, 1, 2)
    sheet = preset("variance_swap", **{"general.maturity_months": 3, "general.strike_date": start})
    product = compile_sheet(sheet, start)
    days = calendar("nyse").business_days(start, product.maturity)
    rng = np.random.default_rng(1)
    levels = 100 * np.exp(np.cumsum(np.r_[0.0, 0.012 * rng.standard_normal(len(days) - 1)]))
    fixings = {"SPX": {d: float(x) for d, x in zip(days, levels)}}
    (matured,) = [e for e in events(sheet, fixings, product.maturity) if e.kind == "matured"]
    v = float(realised_variance(levels)[0])
    K, N = sheet.get("variance.strike"), sheet.get("general.notional")
    assert matured.amount == pytest.approx(N * 100 * (min(v, (2.5 * K) ** 2) - K * K) / (2 * K), rel=1e-12)


def test_a_var_swap_quote_with_the_desk_costs_keeps_its_lines_on_the_strike():
    from optlab.structured.defaults import resolve

    sheet = preset("variance_swap", **{"general.maturity_months": 6})
    q = quote(sheet, CTX, n_paths=8_000, costs=resolve(None, sheet).inputs())
    assert q.desk is not None and q.strike is not None
    keys = {line.key for line in q.desk.lines}
    assert "vol" in keys and not any(k.startswith("digital") for k in keys)  # an overvol, no digital to overhedge
    assert q.strike["value"] > 0.05


def test_the_overs_of_a_variance_swap_take_their_sign_against_the_desk():
    """The table's overs assume a client short vol; a variance swap's client is long it. Their signs are measured on the
    product: the desk selling variance adds vol, every over then costs the client."""
    from optlab.structured.defaults import resolve

    sheet = preset("variance_swap", **{"general.maturity_months": 6})
    inputs = resolve(None, sheet).inputs()
    assert {"vol", "skew"} <= set(inputs.candidates)
    q = quote(sheet, CTX, n_paths=8_000, costs=inputs)
    assert q.desk is not None
    lines = {line.key: line for line in q.desk.lines}
    assert lines["vol"].cost > 0 and "measured on this product" in lines["vol"].note
    assert sum(line.cost for line in q.desk.lines if line.kind == "bump") > 0
