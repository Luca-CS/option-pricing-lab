"""The light exotics in the risk engine: a variance swap's Greeks, its EOD run and the cascade of a day (synthetic lake)."""

import datetime as dt

import numpy as np
import pytest
from _stores import risk_store

from optlab.marketdata.replay import struck_sheet
from optlab.marketdata.synthetic import synthetic_lake
from optlab.risk import RiskSettings
from optlab.risk import pnl as P
from optlab.structured import preset
from optlab.structured.trade import Trade

SETTINGS = RiskSettings(n_paths=4_000, batch=4_000, engine="numpy")
A, B = "2020-03-12", "2020-03-13"


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    return synthetic_lake(tmp_path_factory.mktemp("exotics") / "lake", "2020-01-01", "2020-03-31")[0]


def test_a_variance_swap_goes_through_the_eod_run_and_the_cascade(lake, tmp_path):
    """A swap on the realised variance of every close: long vega on the investor's side, and the cascade of a day adding
    up to its P&L, the closes it crossed counted in its variance."""
    store = risk_store(tmp_path)
    strike = dt.date(2020, 1, 15)
    sheet = struck_sheet(preset("variance_swap", **{"general.maturity_months": 6, "general.underlying": "SPX"}), strike)
    trade = Trade.from_term_sheet("VAR", sheet, 1.0, strike, strike)
    e = P.eod(lake, [trade], A, store, SETTINGS)
    (pos,) = e["positions"]
    assert pos["greeks"]["vega|SPX|"][0] > 0 and np.isfinite(pos["value"])
    day = P.explain_day(lake, [trade], A, B, store, SETTINGS)
    assert sum(v[0] for v in day["steps"].values()) == pytest.approx(day["total"], abs=1e-9)
    assert abs(day["groups"][0]["reconciliation_gap"]) < 0.01


def test_a_lizard_phoenix_goes_through_the_eod_run_and_the_cascade(lake, tmp_path):
    """The lizard's window of closes is read like a daily knock-in: its Greeks and a day's cascade add up."""
    store = risk_store(tmp_path)
    strike = dt.date(2020, 1, 15)
    sheet = preset(
        "lizard_phoenix",
        **{"general.underlying": "SPX", "general.notional": 100.0, "wrapper.solve": "upfront", "coupon.rate": 0.07},
    )
    trade = Trade.from_term_sheet("LIZ", struck_sheet(sheet, strike), -1.0, strike, strike)
    e = P.eod(lake, [trade], A, store, SETTINGS)
    assert np.isfinite(e["positions"][0]["value"]) and "delta|SPX|" in e["positions"][0]["greeks"]
    day = P.explain_day(lake, [trade], A, B, store, SETTINGS)
    assert sum(v[0] for v in day["steps"].values()) == pytest.approx(day["total"], abs=1e-9)


@pytest.mark.parametrize("name", ["accumulator", "funded_decumulator"])
def test_an_accumulator_goes_through_the_eod_run_and_the_cascade(lake, tmp_path, name):
    """An event a day: the closes the cascade's time step crosses are the frozen spot, its spot step the real ones."""
    store = risk_store(tmp_path)
    strike = dt.date(2020, 2, 3)
    sheet = struck_sheet(preset(name, **{"general.maturity_months": 3, "general.underlying": "SPX"}), strike)
    trade = Trade.from_term_sheet("ACC", sheet, 1.0, strike, strike)
    e = P.eod(lake, [trade], A, store, SETTINGS)
    (pos,) = e["positions"]
    assert np.isfinite(pos["value"]) and pos["greeks"]["delta|SPX|"][0] != 0
    day = P.explain_day(lake, [trade], A, B, store, SETTINGS)
    assert sum(v[0] for v in day["steps"].values()) == pytest.approx(day["total"], abs=1e-9)
    assert abs(day["groups"][0]["reconciliation_gap"]) < 0.01


@pytest.mark.parametrize("name", ["variance_swap", "accumulator"])
def test_a_light_exotic_is_struck_on_a_past_date_and_replayed_on_its_closes(lake, name):
    """The replay solves what the sheet solves on the strike date's market (a variance swap's strike), fixes it into the
    trade, then values it on the real closes: the P&L it explains adds up to the value and the cash."""
    from optlab.marketdata.replay import ReplayConfig, replay, strike_trade

    cfg = ReplayConfig(n_paths=2_000, batch=2_000, reval_every=10, greeks=False)
    sheet = preset(name, **{"general.maturity_months": 3, "general.underlying": "SPX"})
    trade, info = strike_trade(lake, name.upper(), sheet, "2020-01-15", -1.0, cfg)
    fixed = trade.term_sheet()
    assert fixed is not None and fixed.get("wrapper.solve") == "upfront"
    if name == "variance_swap":
        assert info["coupon"] > 0 and fixed.get("variance.strike") == pytest.approx(info["coupon"] ** 0.5)
    r = replay(lake, trade, end="2020-03-13", cfg=cfg)
    assert len(r.daily) > 3 and abs(r.reconciliation["pnl_gap"]) < 1e-9


def test_the_stress_scenarios_and_the_spot_vol_grid_revalue_a_book_of_light_exotics():
    """The default library and the spot-vol grid on one group holding a variance swap, an accumulator and a lizard: the
    vol spike makes the investor's variance swap worth more, every scenario prices every position."""
    from _paths import SPX_CALIBRATION

    from optlab.risk import RiskGroup
    from optlab.risk import scenarios as sc
    from optlab.risk.shocks import RiskEnv
    from optlab.structured.context import PricingContext
    from optlab.structured.portfolio import Book, Position

    ctx = PricingContext.from_calibration(SPX_CALIBRATION)
    book = Book(
        [
            Position("VAR", preset("variance_swap", **{"general.maturity_months": 6}), 1.0),
            Position("ACC", preset("accumulator", **{"general.maturity_months": 3}), -1.0),
            Position("LIZ", preset("lizard_phoenix", **{"wrapper.solve": "upfront", "coupon.rate": 0.05}), -1.0),
        ]
    )
    env = RiskEnv(betas={"SPX": [-0.5] * 8})  # the realistic scenarios' spot-vol betas
    groups = [RiskGroup("SPX", book, ctx)]
    rep = sc.run(groups, sc.defaults(), SETTINGS, env, spots=(-0.1, 0.0), vols=(0.0, 0.05)).to_dict()
    var = next(p for p in rep["positions"] if p["name"] == "VAR")
    assert var["pnl"]["Vol spike"][0] > 0  # vols up: the realised variance the investor holds is worth more
    assert all(set(p["pnl"]) == set(var["pnl"]) for p in rep["positions"])
    assert np.isfinite(np.asarray(rep["grid"]["pnl"], dtype=float)).all()
