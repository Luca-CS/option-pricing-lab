"""An issuer callable in the risk engine: the base's exercise policy is kept by every shock, so its Greeks are
smooth (the envelope theorem), and its vega has the sign of the note's."""

import numpy as np
from _risk_refs import VALUATION, flat_ctx

from optlab.risk import RiskGroup, RiskSettings
from optlab.risk.greeks import GreekOptions, compute
from optlab.structured.portfolio import Book, Position
from optlab.structured.presets import preset

SETTINGS = RiskSettings(model="lv", n_paths=20_000, engine="numpy")
OPTIONS = GreekOptions(kxt="none", curves=False, sticky_strike=False, realistic=False, cross=False, theta=False)


def _callable():
    return preset(
        "callable_phoenix",
        **{
            "general.strike_date": VALUATION,
            "general.maturity_months": 24,
            "coupon.rate": 0.06,
            "issuer_call.first_call_months": 6,
            "wrapper.solve": "upfront",
        },
    )


def test_a_callable_s_greeks_keep_the_base_s_policy_and_are_measured():
    sheet = _callable()
    rep = compute(
        [RiskGroup("SPX", Book([Position("sold", sheet, -1.0)]), flat_ctx(), 11)], options=OPTIONS, settings=SETTINGS
    )
    g = {p["name"]: p for p in rep.positions}["sold"]["greeks"]
    for key in ("delta|SPX|", "vega|SPX|", "gamma|SPX|"):
        value, se = g[key]
        assert np.isfinite(value) and np.isfinite(se)
    assert abs(g["delta|SPX|"][0]) > 4 * g["delta|SPX|"][1]
    assert g["vega|SPX|"][1] < 0.25 * abs(g["vega|SPX|"][0]) + 1e-9  # frozen policy: no regression noise in the bump
