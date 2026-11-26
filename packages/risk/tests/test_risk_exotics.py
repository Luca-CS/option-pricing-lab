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
