"""R2: Greeks and their variants against closed forms, the roll, worst-of cross gamma and correlation, aggregation."""

import datetime as dt
import json

import numpy as np
import pytest
from _risk_refs import (
    FLAT,
    S0,
    VALUATION,
    VOL,
    Q,
    R,
    book,
    bs_cross,
    day,
    digital,
    engine_name,
    flat_ctx,
    vanilla,
    within,
    worst_put_shifted,
)
from optlab.core import Market
from optlab.core.blackscholes import bs_greeks, bs_price
from optlab.risk import RiskGroup, RiskSettings
from optlab.risk.greeks import Bumps, GreekOptions, compute
from optlab.structured.context import PricingContext
from optlab.structured.multiasset import BasketContext
from optlab.structured.portfolio import Book, Position

ENGINE = engine_name()
FAST = GreekOptions(kxt="none", curves=False, cross=False, sticky_strike=False, theta=False)


@pytest.fixture(scope="module")
def vanillas():
    """A call and a put, one year, replicated on the flat surface: every Greek without noise."""
    ctx = flat_ctx()
    b = book(vanilla(100.0, 365), vanilla(95.0, 365, "put"), quantities=[1.0, -2.0])
    return compute([RiskGroup("SPX", b, ctx, 1)], options=GreekOptions(kxt="single"))


@pytest.mark.slow
@pytest.mark.parametrize("i, K, kind, q", [(0, 100.0, "call", 1.0), (1, 95.0, "put", -2.0)])
def test_vanilla_greeks_match_black_scholes(vanillas, i, K, kind, q):
    T = 1.0
    ref = bs_greeks(S0, K, T, R, Q, VOL, kind)
    x = bs_cross(S0, K, T, R, Q, VOL)
    g = vanillas.positions[i]["greeks"]
    c = q * 100 / S0  # N / S0 units of the payoff per position
    np.testing.assert_allclose(vanillas.positions[i]["value"], c * bs_price(S0, K, T, R, Q, VOL, kind), rtol=1e-7)
    np.testing.assert_allclose(g["delta|SPX|"][0], c * ref["delta"] * S0 * 0.01, rtol=2e-3)
    np.testing.assert_allclose(g["gamma|SPX|"][0], c * ref["gamma"] * (S0 * 0.01) ** 2, rtol=2e-3)
    np.testing.assert_allclose(g["vega|SPX|"][0], c * ref["vega"] * 0.01, rtol=1e-3)
    np.testing.assert_allclose(g["volga|SPX|"][0], c * x["volga"] * 1e-4, rtol=2e-2, atol=2e-6)
    np.testing.assert_allclose(g["vanna|SPX|"][0], c * x["vanna"] * S0 * 1e-4, rtol=2e-2)
    np.testing.assert_allclose(g["rho|USD|"][0], c * ref["rho"] * 1e-4, rtol=2e-3)
    # epsilon: dV/dq = -S T e^{-qT} N(phi d1) phi
    from scipy.stats import norm

    sd = VOL * np.sqrt(T)
    d1 = (np.log(S0 / K) + (R - Q + 0.5 * VOL**2) * T) / sd
    phi = 1.0 if kind == "call" else -1.0
    np.testing.assert_allclose(
        g["epsilon|SPX|"][0], c * -phi * S0 * T * np.exp(-Q * T) * norm.cdf(phi * d1) * 1e-4, rtol=2e-3
    )
    # theta: one business day (Tuesday to Wednesday, 1/365), market frozen
    th = vanillas.positions[i]["theta"]
    np.testing.assert_allclose(th["theta"], c * ref["theta"] / 365, rtol=1e-2)
    np.testing.assert_allclose(th["carry"] + th["decay"] + th["events"], th["theta"], rtol=1e-12)
    np.testing.assert_allclose(th["carry"], -g["delta|SPX|"][0] * (np.exp((R - Q) / 365) - 1) / 0.01, rtol=1e-2)
    # on a flat surface every delta rule agrees, the weighted vega is the vega (at one year), and the nodes add up
    np.testing.assert_allclose(g["delta_sticky_strike|SPX|"][0], g["delta|SPX|"][0], rtol=1e-9)
    np.testing.assert_allclose(g["vega_net|SPX|"][0], g["vega|SPX|"][0], rtol=5e-3)  # one-sided nodes: + volga / 2
    np.testing.assert_allclose(g["vega_weighted|SPX|"][0], g["vega_net|SPX|"][0], rtol=1e-9)
    np.testing.assert_allclose(g["vega_weighted_direct|SPX|"][0], g["vega|SPX|"][0], rtol=5e-3)
    np.testing.assert_allclose(g["vega_relative|SPX|"][0], g["vega|SPX|"][0] * VOL, rtol=2e-3)
    np.testing.assert_allclose(g["delta_notional|SPX|"][0], 100 * ref["delta"] * np.sign(q), rtol=2e-3)


def test_book_greeks_add_up_and_serialise(vanillas):
    r = vanillas
    for k in ("delta|SPX|", "vega|SPX|", "rho|USD|", "theta|*|"):
        np.testing.assert_allclose(r.book[k][0], sum(p["greeks"][k][0] for p in r.positions), rtol=1e-12)
    assert r.gross["vega_node_gross|SPX|"] >= abs(r.book["vega_net|SPX|"][0]) - 1e-12
    text = json.dumps(r.to_dict())
    assert "vega_node|SPX|100%@1y" in text and r.units["delta"].startswith("currency per +1 %")


def test_sticky_strike_and_moneyness_deltas_on_a_smile():
    """Replicated vanilla on the SPX smile: sticky strike keeps the implied vol at K, sticky moneyness reads it at K / F'."""
    spx = PricingContext.from_calibration()
    days, K = 365, 95.0
    T = days / 365
    b = book(vanilla(K, days, "put", valuation=spx.valuation))
    rep = compute(
        [RiskGroup("SPX", b, spx, 1)], options=GreekOptions(kxt="none", curves=False, cross=False, theta=False)
    )
    g = rep.positions[0]["greeks"]
    F, D = float(spx.market.F(T)), float(spx.market.D(T))
    r, qy = -np.log(D) / T, -np.log(F * D / spx.market.spot) / T

    def bs(S, vol):
        return 100 / S0 * bs_price(S, K, T, r, qy, vol, "put")

    S = spx.market.spot
    iv_fixed = float(spx.surface.iv(np.log(K / F), T))
    up, dn = S * 1.01, S * 0.99
    ss = (bs(up, iv_fixed) - bs(dn, iv_fixed)) / 2
    sm = (
        bs(up, float(spx.surface.iv(np.log(K / (F * 1.01)), T)))
        - bs(dn, float(spx.surface.iv(np.log(K / (F * 0.99)), T)))
    ) / 2
    np.testing.assert_allclose(g["delta_sticky_strike|SPX|"][0], ss, rtol=1e-4)
    np.testing.assert_allclose(g["delta|SPX|"][0], sm, rtol=1e-4)
    assert (
        g["delta|SPX|"][0] > g["delta_sticky_strike|SPX|"][0]
    )  # a put on a downward skew: the smile moving with the spot lifts its vol as it rises


@pytest.mark.slow
def test_smoothed_digital_greeks_under_monte_carlo_match_its_call_spread():
    """The digital is smoothed as a call spread over K (1 +- 0.25 %) (the evaluator's centred ramp): its Monte Carlo Greeks match
    that spread's closed forms within 4 SE, and the smoothing bias on the price is reported."""
    n = 200_000 if ENGINE != "numpy" else 40_000
    eps, K, T = 0.005, 100.0, 1.0
    ctx = flat_ctx()
    b = book(digital(K, 365))
    rep = compute(
        [RiskGroup("SPX", b, ctx, 3)],
        options=FAST,
        settings=RiskSettings(model="lv", n_paths=n, smoothing=eps, engine=ENGINE),
    )
    g = rep.positions[0]["greeks"]
    w = K * eps

    def spread(S, vol):
        return 100 * (bs_price(S, K - w / 2, T, R, Q, vol) - bs_price(S, K + w / 2, T, R, Q, vol)) / w

    delta = (spread(S0 * 1.01, VOL) - spread(S0 * 0.99, VOL)) / 2
    vega = (spread(S0, VOL + 0.01) - spread(S0, VOL - 0.01)) / 2
    gamma = spread(S0 * 1.01, VOL) - 2 * spread(S0, VOL) + spread(S0 * 0.99, VOL)
    within(g["delta|SPX|"][0], delta, g["delta|SPX|"][1], floor=0.002 * abs(delta))
    within(g["vega|SPX|"][0], vega, g["vega|SPX|"][1], floor=0.003 * abs(vega))
    within(g["gamma|SPX|"][0], gamma, g["gamma|SPX|"][1], floor=0.01 * abs(gamma))
    bias, bias_se = rep.positions[0]["smoothing_bias"]
    exact = (
        100
        * np.exp(-R * T)
        * float(
            __import__("scipy.stats").stats.norm.cdf((np.log(S0 / K) + (R - Q - 0.5 * VOL**2) * T) / (VOL * np.sqrt(T)))
        )
    )
    within(bias, spread(S0, VOL) - exact, bias_se, floor=1e-3)


@pytest.mark.slow
def test_an_autocall_the_day_before_its_observation_drops_the_event_in_the_roll():
    """Observed tomorrow at 100 % and paid tomorrow: at a frozen spot of 100 the roll calls it, its value becomes cash."""
    ctx = flat_ctx()
    tomorrow, end = day(VALUATION, 1), day(VALUATION, 365)
    script = (
        f"underlying SPX\nparam N = 100\nevent {tomorrow} pay {tomorrow}\n  if SPX >= 100 then\n    pay N as redemption\n"
        f"    terminate\n  end\nend\nevent {end} pay {end}\n  pay N * SPX / 100 as redemption\nend\n"
    )
    b = Book([Position("ac", script, -1.0)])
    rep = compute(
        [RiskGroup("SPX", b, ctx, 2)],
        options=GreekOptions(kxt="none", curves=False, cross=False, sticky_strike=False),
        settings=RiskSettings(model="lv", n_paths=20_000, engine="numpy"),
    )
    th = rep.positions[0]["theta"]
    assert th["events_cash"] == pytest.approx(-100.0, abs=1e-9)  # the desk pays the redemption tomorrow
    value = rep.positions[0]["value"]
    assert th["theta"] == pytest.approx(-100.0 - value, abs=1e-9)  # nothing left, the cash is all there is
    assert -100.0 < value < -95.0


def _two_names(rho):
    q2, v2 = 0.03, 0.25
    markets = {"SPX": FLAT, "SX5E": Market.flat(100.0, R, q2)}
    return (
        BasketContext(("SPX", "SX5E"), markets, {"SPX": VOL, "SX5E": v2}, np.array([[1, rho], [rho, 1]]), VALUATION),
        (VOL, v2),
        (Q, q2),
    )


def test_worst_of_cross_gamma_and_correlation_match_the_bivariate_reference():
    rho, K, T = 0.6, 1.0, 1.0
    ctx, vols, qs = _two_names(rho)
    end = day(VALUATION, 365)
    script = (
        f"underlying SPX\nunderlying SX5E\nparam N = 100\nevent {end} pay {end}\n"
        f"  pay N * max({K} - min(SPX / 100, SX5E / 100), 0) as redemption\nend\n"
    )
    b = Book([Position("wop", script, -1.0)])
    n = 400_000 if ENGINE != "numpy" else 60_000
    rep = compute(
        [RiskGroup("worst-of", b, ctx, 9)],
        options=GreekOptions(kxt="none", curves=False, sticky_strike=False, theta=False),
        settings=RiskSettings(n_paths=n, engine=ENGINE),
    )
    g = rep.positions[0]["greeks"]
    D = np.exp(-R * T)

    def V(a=(1.0, 1.0), r_=rho):
        return -100 * D * worst_put_shifted(K, T, vols, r_, R, qs, a)

    h = 0.01
    cross = (V((1 + h, 1 + h)) - V((1 + h, 1 - h)) - V((1 - h, 1 + h)) + V((1 - h, 1 - h))) / 4
    corr = (V(r_=rho + 0.01) - V(r_=rho - 0.01)) / 2
    d1 = (V((1 + h, 1.0)) - V((1 - h, 1.0))) / 2
    within(rep.positions[0]["value"], V(), rep.positions[0]["value_se"])
    within(g["delta|SPX|"][0], d1, g["delta|SPX|"][1])
    within(g["cross_gamma|SPX~SX5E|"][0], cross, g["cross_gamma|SPX~SX5E|"][1])
    within(g["corr|*|"][0], corr, g["corr|*|"][1])
    within(g["corr|SPX~SX5E|"][0], corr, g["corr|SPX~SX5E|"][1], floor=0.02 * abs(corr))  # one-sided
    assert abs(g["corr|*|"][0]) > 4 * g["corr|*|"][1]  # measured, not noise
    np.testing.assert_allclose(g["delta|*|"][0], g["delta|SPX|"][0] + g["delta|SX5E|"][0], rtol=1e-12)


@pytest.mark.slow
def test_noise_flags_and_units():
    ctx = flat_ctx()
    b = book(vanilla(100.0, 365))
    rep = compute(
        [RiskGroup("SPX", b, ctx, 3)], options=FAST, settings=RiskSettings(model="lv", n_paths=4_000, engine="numpy")
    )
    p = rep.positions[0]
    for k in p["noise"]:
        v, se = p["greeks"][k]
        assert abs(v) < 2 * se
    assert (
        "skew_vega|SPX|" in p["noise"] or abs(p["greeks"]["skew_vega|SPX|"][0]) >= 2 * p["greeks"]["skew_vega|SPX|"][1]
    )
    assert Bumps.from_dict({"spot": 0.02, "junk": 1}).spot == 0.02
    assert dt.date(2026, 10, 27).weekday() == 1  # the roll of the tests is Tuesday to Wednesday


def test_aggregate_views(vanillas):
    from optlab.risk import aggregate

    assert aggregate.names(vanillas) == ["SPX"]
    vals, ses = aggregate.vega_matrix(vanillas, "SPX")
    assert vals.shape == (10, 8) and np.all(ses == 0)
    s = aggregate.vega_summary(vanillas, "SPX")
    np.testing.assert_allclose(s["net"][0], np.nansum(vals), rtol=1e-9)
    assert s["gross"] >= abs(s["net"][0]) and s["gross_over"] == "nodes"
    r = aggregate.rho_summary(vanillas)
    np.testing.assert_allclose(r["net"], r["total"][0], rtol=2e-3)
    np.testing.assert_allclose(r["discount"][0] + r["forward"][0], r["total"][0], rtol=2e-3)
    g = aggregate.gamma_summary(vanillas)
    assert g["gross"] >= abs(g["net"])
