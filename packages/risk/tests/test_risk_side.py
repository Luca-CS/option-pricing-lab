"""The desk's side: a sold autocall (negative quantity) is short delta and long vega for the desk, a bought one the
opposite, in the Greeks and in the scenario grid, on the same simulation. The coupon is under the fair one (the note is
worth less than par to the investor), as when the desk sells it with a margin: a coupon far above it makes the early
call costly to the investor and turns the note's delta negative."""

import numpy as np
from _risk_refs import VALUATION, flat_ctx

from optlab.risk import RiskGroup, RiskSettings
from optlab.risk import scenarios as sc
from optlab.risk.greeks import GreekOptions, compute
from optlab.structured.portfolio import Book, Position
from optlab.structured.presets import preset

SETTINGS = RiskSettings(model="lv", n_paths=6_000, engine="numpy")


def _autocall():
    return preset("phoenix", **{"general.strike_date": VALUATION, "general.maturity_months": 24, "coupon.rate": 0.04})


def test_a_sold_autocall_is_short_delta_and_long_vega_for_the_desk():
    sheet = _autocall()
    book = Book([Position("sold", sheet, -1.0), Position("bought", sheet, 1.0)])  # one shared simulation
    rep = compute(
        [RiskGroup("SPX", book, flat_ctx(), 11)],
        options=GreekOptions(kxt="none", curves=False, sticky_strike=False, realistic=False, cross=False, theta=False),
        settings=SETTINGS,
    )
    sold, bought = ({p["name"]: p for p in rep.positions}[k]["greeks"] for k in ("sold", "bought"))
    for key in ("delta|SPX|", "vega|SPX|"):
        assert abs(sold[key][0]) > 4 * sold[key][1], key  # measured, not noise
        np.testing.assert_allclose(sold[key][0], -bought[key][0], rtol=1e-12)  # one simulation, opposite signs
    assert sold["delta|SPX|"][0] < 0 < sold["vega|SPX|"][0]


def test_a_sold_autocall_loses_in_a_crash_and_gains_on_a_vol_spike():
    sheet = _autocall()
    grid = {}
    for name, q in (("sold", -1.0), ("bought", 1.0)):
        rep = sc.run(
            [RiskGroup(name, Book([Position(name, sheet, q)]), flat_ctx(), 11)],
            [],
            settings=SETTINGS,
            spots=(-0.3, 0.0),
            vols=(0.0, 0.15),
        )
        g = rep.grid
        assert g is not None
        grid[name] = g
    g = grid["sold"]
    i_down, i0, j0, j_up = g["spots"].index(-0.30), g["spots"].index(0.0), g["vols"].index(0.0), g["vols"].index(0.15)
    crash, spike = g["pnl"][i_down][j0][0], g["pnl"][i0][j_up][0]
    assert crash > 0 > -crash  # short delta: the desk gains when the spot falls
    assert spike > 0  # long vega: the desk gains when the vol rises
    np.testing.assert_allclose(crash, -grid["bought"]["pnl"][i_down][j0][0], rtol=1e-12)
