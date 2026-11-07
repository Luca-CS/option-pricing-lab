"""R3: the spot ladder under three vol rules, and the spot-vol betas of the lake."""

import numpy as np
import pytest

from _risk_refs import Q, R, S0, VOL, book, engine_name, flat_ctx, vanilla, within
from optlab.blackscholes import bs_greeks, bs_price
from optlab.risk import RiskGroup, RiskSettings, Shock
from optlab.risk.ladder import POINTS, LadderOptions, ladder
from optlab.risk.shocks import RiskEnv, name_moves
from optlab.risk.store import RiskStore
from optlab.structured.context import PricingContext

BETAS = [-0.9, -0.8, -0.7, -0.5, -0.45, -0.4, -0.35, -0.3]  # vol per unit log return, 1m to 5y


def test_every_point_of_a_replicated_vanilla_is_the_closed_form_at_the_shocked_market():
    ctx = flat_ctx()
    b = book(vanilla(100.0, 365))
    env = RiskEnv(betas={"SPX": BETAS})
    rep = ladder([RiskGroup("SPX", b, ctx, 1)], LadderOptions(tenors=False), env=env)
    j1 = 3  # the one-year tenor: a one-year vanilla sees its move exactly
    for p in POINTS:
        S = S0 * (1 + p)
        for rule, vol in (
            ("sticky_moneyness", VOL),
            ("sticky_strike", VOL),
            ("realistic", VOL + BETAS[j1] * np.log1p(p)),
        ):
            v, se = rep.book[f"value@{rule}@{p:+.4f}"]
            np.testing.assert_allclose(v, bs_price(S, 100.0, 1.0, R, Q, vol), rtol=1e-6)
            assert se == 0
            d = rep.book[f"delta@{rule}@{p:+.4f}"][0]
            np.testing.assert_allclose(d, bs_greeks(S, 100.0, 1.0, R, Q, vol)["delta"] * S * 0.01, rtol=3e-3)
    assert rep.book["pnl@realistic@+0.0000"] == [0.0, 0.0]


def test_sticky_strike_ladder_keeps_the_implied_vol_at_the_strike_on_a_smile():
    spx = PricingContext.from_calibration()
    K, days = 95.0, 365
    T = days / 365
    b = book(vanilla(K, days, "put", valuation=spx.valuation))
    points = (
        -0.2,
        -0.1,
        -0.05,
        0.0,
        0.05,
        0.1,
    )  # at -30 % the sticky-strike smile is in butterfly arbitrage (next test)
    rep = ladder(
        [RiskGroup("SPX", b, spx, 1)],
        LadderOptions(points=points, rules=("sticky_strike", "sticky_moneyness"), greeks=False),
    )
    F, D = float(spx.market.F(T)), float(spx.market.D(T))
    r, qy = -np.log(D) / T, -np.log(F * D / spx.market.spot) / T
    iv_K = float(spx.surface.iv(np.log(K / F), T))
    for p in points:
        S = spx.market.spot * (1 + p)
        ss = rep.book[f"value@sticky_strike@{p:+.4f}"][0]
        sm = rep.book[f"value@sticky_moneyness@{p:+.4f}"][0]
        np.testing.assert_allclose(ss, bs_price(S, K, T, r, qy, iv_K, "put"), rtol=1e-5)
        np.testing.assert_allclose(
            sm, bs_price(S, K, T, r, qy, float(spx.surface.iv(np.log(K / (F * (1 + p))), T)), "put"), rtol=1e-5
        )
        if p < 0:
            assert sm < ss  # the smile follows the spot down: the put's strike moves up the moneyness, to a lower vol


def test_monte_carlo_ladder_point_zero_is_the_base_and_deltas_match_black_scholes():
    eng = engine_name()
    n = 200_000 if eng != "numpy" else 40_000
    ctx = flat_ctx()
    b = book(vanilla(100.0, 365))
    rep = ladder(
        [RiskGroup("SPX", b, ctx, 4)],
        LadderOptions(rules=("sticky_moneyness",), tenors=False),
        RiskSettings(model="lv", n_paths=n, engine=eng),
    )
    base = b.price(ctx, model="lv", n_paths=n, seed=4, batch=25_000) if eng == "numpy" else None
    v0 = rep.book["value@sticky_moneyness@+0.0000"]
    if base is not None:
        assert v0[0] == base.value  # the same paths, to the last digit
    for p in POINTS:
        S = S0 * (1 + p)
        d, se = rep.book[f"delta@sticky_moneyness@{p:+.4f}"]
        within(
            d,
            bs_greeks(S, 100.0, 1.0, R, Q, VOL)["delta"] * S * 0.01,
            se,
            floor=bs_greeks(S, 100.0, 1.0, R, Q, VOL)["gamma"] * (0.01 * S) ** 2 * 0.01,
        )
        pnl, pse = rep.book[f"pnl@sticky_moneyness@{p:+.4f}"]
        within(pnl, bs_price(S, 100.0, 1.0, R, Q, VOL) - bs_price(S0, 100.0, 1.0, R, Q, VOL), pse)


def test_the_rule_applies_to_the_ladder_point_only():
    """Greeks around a sticky-strike point bump the spot at sticky moneyness of the shocked market."""
    point = Shock(spot={"*": -0.2}, rule="sticky_strike")
    bumped = point + Shock(spot={"*": 0.01})
    mv = name_moves(bumped, "SPX", "USD")
    assert mv.spot == pytest.approx(0.8 * 1.01 - 1) and mv.strike_shift == pytest.approx(-0.2)
    real = Shock(spot={"*": -0.1}, rule="realistic") + Shock(spot={"SPX": 0.01})
    mv = name_moves(real, "SPX", "USD", RiskEnv(betas={"SPX": BETAS}))
    assert mv.vol.term[0] == pytest.approx(BETAS[0] * np.log(0.9))


@pytest.fixture(scope="module")
def lake():
    from optlab.marketdata.lake import Lake

    lk = Lake()
    try:
        if (
            lk.read("vol_surfaces", where="underlying = 'SPX' AND layer = 'reconstructed'", columns="count(*) AS n")[
                "n"
            ].iloc[0]
            < 600
        ):
            pytest.skip("no reconstructed SPX surfaces in the lake")
    except Exception:  # noqa: BLE001 - no lake on this machine
        pytest.skip("no lake")
    return lk


def test_spot_vol_betas_of_spx_on_the_lake(lake, tmp_path):
    from optlab.risk.ladder import estimate_betas, stored_betas

    rec = estimate_betas(lake, "SPX", "2026-11-04")
    b, se = np.array(rec["betas"]), np.array(rec["se"])
    assert rec["n"] >= 450 and rec["origin"] == ["reconstructed"]
    assert np.all(b < 0) and np.all(np.abs(b) > 4 * se)  # vols rise when the index falls, at every tenor
    assert abs(b[0]) > abs(b[-1])  # more at short tenors
    store = RiskStore(tmp_path / "risk.sqlite")
    first = stored_betas(store, lake, ["SPX"], "2026-11-04")
    again = stored_betas(store, lake, ["SPX"], "2026-11-04")
    assert first["SPX"]["version"] == again["SPX"]["version"] == 1
    assert stored_betas(store, lake, ["SPX"], "2026-11-04", refresh=True)["SPX"]["version"] == 2


def test_a_sticky_strike_crash_point_in_arbitrage_is_reported_and_valued_by_monte_carlo():
    spx = PricingContext.from_calibration()
    b = book(vanilla(95.0, 365, "put", valuation=spx.valuation))
    rep = ladder(
        [RiskGroup("SPX", b, spx, 1)],
        LadderOptions(points=(-0.3, 0.0), rules=("sticky_strike",), greeks=False),
        RiskSettings(n_paths=20_000, engine="numpy"),
    )
    assert rep.groups[0]["model"] == "lv" and "butterfly arbitrage" in rep.groups[0]["warnings"][-1]
    assert rep.book["value@sticky_strike@-0.3000"][1] > 0
