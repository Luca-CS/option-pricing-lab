"""R4: hypothetical scenarios as versioned, signed data, replayed against closed forms."""

import numpy as np
import pytest
from _risk_refs import S0, VOL, Q, R, book, flat_ctx, vanilla
from _structured_refs import flat_surface

from optlab.core.blackscholes import bs_price
from optlab.risk import RiskGroup
from optlab.risk import scenarios as sc
from optlab.risk.shocks import RiskEnv
from optlab.risk.store import RiskStore
from optlab.structured.context import PricingContext
from optlab.structured.surfaces import BumpedSurface

BETA_1Y = -0.46
BETAS = [-1.5, -0.96, -0.69, BETA_1Y, -0.37, -0.31, -0.24, -0.16]


def test_defaults_are_starting_values_saved_edited_and_signed(tmp_path):
    store = RiskStore(tmp_path / "risk.sqlite")
    first = sc.load(store)
    assert first["starting"] and first["version"] is None
    assert [s.name for s in first["scenarios"]] == [
        "Equity crash",
        "Grind down",
        "Vol spike",
        "Rally",
        "Dividend cut",
        "Rates up",
    ]
    assert all(s.label == sc.STARTING for s in first["scenarios"])
    with pytest.raises(ValueError, match="signed"):
        sc.save(store, first["scenarios"], "")
    assert sc.save(store, first["scenarios"], "Luca", "starting set") == 1
    edited = [s.to_dict() for s in first["scenarios"]]
    edited[1]["shock"]["spot"] = {"*": -0.12}
    edited[1]["label"] = "desk"
    assert sc.save(store, edited, "Luca", "grind down to -12 %") == 2
    now = sc.load(store)
    assert now["version"] == 2 and now["signed_by"] == "Luca" and now["scenarios"][1].shock.spot == {"*": -0.12}
    assert [h["version"] for h in sc.history(store)] == [1, 2]
    with pytest.raises(ValueError, match="unique"):
        sc.save(store, edited + [edited[0]], "Luca")


def test_scenarios_replayed_on_a_vanilla_match_the_closed_forms():
    ctx = flat_ctx()
    K, T = 100.0, 1.0
    b = book(vanilla(K, 365))
    rep = sc.run([RiskGroup("SPX", b, ctx, 1)], sc.defaults(), env=RiskEnv(betas={"SPX": BETAS}))
    base = bs_price(S0, K, T, R, Q, VOL)
    got = {s["name"]: s["book"] for s in rep.scenarios}
    np.testing.assert_allclose(got["Grind down"][0], bs_price(S0 * 0.9, K, T, R, Q, VOL + 0.03) - base, rtol=1e-6)
    np.testing.assert_allclose(got["Vol spike"][0], bs_price(S0 * 0.95, K, T, R, Q, VOL + 0.10) - base, rtol=1e-6)
    np.testing.assert_allclose(got["Rally"][0], bs_price(S0 * 1.1, K, T, R, Q, VOL - 0.03) - base, rtol=1e-6)
    np.testing.assert_allclose(got["Dividend cut"][0], bs_price(S0, K, T, R, Q * 0.5, VOL) - base, rtol=1e-6)
    np.testing.assert_allclose(got["Rates up"][0], bs_price(S0, K, T, R + 0.01, Q, VOL) - base, rtol=1e-6)
    # the crash: realistic vol at one year, the skew rotation read at the strike's new moneyness, dividends, rates
    r2, q2 = R - 0.005, Q * 0.7
    F = S0 * 0.8 * np.exp((r2 - q2) * T)
    k = np.log(K / F)
    skew = float(BumpedSurface(flat_surface(VOL), 0.0, 0.02).iv(k, T)) - VOL
    vol = VOL + BETA_1Y * np.log(0.8) + skew
    np.testing.assert_allclose(got["Equity crash"][0], bs_price(S0 * 0.8, K, T, r2, q2, vol) - base, rtol=1e-5)
    g = rep.grid
    i, j = g["spots"].index(-0.10), g["vols"].index(0.05)
    np.testing.assert_allclose(g["pnl"][i][j][0], bs_price(S0 * 0.9, K, T, R, Q, VOL + 0.05) - base, rtol=1e-6)
    assert all(s["arbitrage"] == [] for s in rep.scenarios)  # a flat surface stays clean


def test_shocked_surfaces_are_checked_for_arbitrage():
    spx = PricingContext.from_calibration()
    crash = sc.Scenario("Vol collapse", "vols -15 points", sc.Shock(vol={"*": sc.VolMove(-0.15)}))
    rep = sc.run(
        [RiskGroup("SPX", book(vanilla(100.0, 365, valuation=spx.valuation)), spx, 1)],
        [crash] + sc.defaults()[1:3],
        spot_vol_grid=False,
    )
    flags = {s["name"]: s for s in rep.scenarios}
    assert flags["Vol collapse"]["arbitrage"] == ["SPX:SPX"]  # the short wing vols go negative
    assert flags["Grind down"]["arbitrage"] == []
    assert (
        flags["Vol collapse"]["flags"]["SPX:SPX"]["butterfly_free"] is False
        or flags["Vol collapse"]["flags"]["SPX:SPX"]["calendar_free"] is False
    )
