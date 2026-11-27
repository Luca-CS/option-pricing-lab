"""The hedged replay (H4): a trade's life on closes with its delta (and vega) hedged, against the same life naked. On a
synthetic lake: the daily hedge leaves less P&L spread than a weekly one, which leaves less than none; the explain adds
up to the hedged P&L with its slippage; the half-spreads are paid; cash carries its funding and dividends."""

import datetime as dt

import pandas as pd
import pytest

from optlab.marketdata.hedged import HedgeConfig
from optlab.marketdata.lake import Lake
from optlab.marketdata.replay import ReplayConfig, replay, result_json, strike_trade
from optlab.marketdata.synthetic import derive_all, fill_observed
from optlab.structured import preset

STRIKE = dt.date(2019, 7, 1)
END = dt.date(2019, 9, 30)
CFG = ReplayConfig(n_paths=4000, batch=4000, reval_every=1, greeks=True)


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    lake = Lake(tmp_path_factory.mktemp("hedged") / "lake")
    days = pd.bdate_range("2019-01-01", "2019-12-31")
    fill_observed(lake, days)
    derive_all(lake, "2019-01-01", names=("SPX",))
    return lake


@pytest.fixture(scope="module")
def trade(lake):
    sheet = preset("barrier_reverse_convertible", **{"general.maturity_months": 12, "general.notional": 1e7})
    t, _ = strike_trade(lake, "BRC-H", sheet, STRIKE, -1.0, CFG)
    return t


@pytest.mark.slow
def test_hedging_more_often_leaves_less_pnl_spread_and_the_explain_adds_up(lake, trade):
    daily = replay(lake, trade, END, CFG, hedge=HedgeConfig(delta_every=1))
    weekly = replay(lake, trade, END, CFG, hedge=HedgeConfig(delta_every=5))
    d, w = daily.hedged, weekly.hedged
    assert d["hedged_std"] < w["hedged_std"] < d["naked_std"]
    assert d["naked_total"] == pytest.approx(weekly.hedged["naked_total"], rel=1e-12)  # the same naked life
    assert d["costs"] > w["costs"] > 0  # rebalancing more often pays more spread
    assert sum(d["explain"].values()) + d["slippage"] == pytest.approx(d["hedged_total"], abs=1e-6)
    assert abs(d["explain"]["delta"]) < 0.25 * abs(
        sum(r["explain"]["delta"] for r in result_json(daily)["daily"] if r["explain"])
    )
    out = result_json(daily)
    assert out["hedged"]["steps"] == len(out["daily"]) - 1 and "hedge" in out["daily"][1]


@pytest.mark.slow
def test_a_cash_hedge_carries_its_funding_and_dividends(lake, trade):
    cash = replay(lake, trade, END, CFG, hedge=HedgeConfig(instrument="cash"))
    assert cash.hedged["carry"] != 0.0 and cash.hedged["hedged_std"] < cash.hedged["naked_std"]
    with pytest.raises(ValueError, match="needs the Greeks"):
        replay(lake, trade, END, ReplayConfig(greeks=False), hedge=HedgeConfig())
    with pytest.raises(ValueError, match="future or cash"):
        HedgeConfig(instrument="swaps")
