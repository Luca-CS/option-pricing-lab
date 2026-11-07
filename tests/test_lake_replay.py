"""Replay of a book on fixings. Offline, the fixings are built so that the knock-in and the call have known dates, which an
independent reading of the levels confirms. The same checks on real closes run in scripts/lake_replay.py."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from _lake_fixture import derive_all, fill_observed, price_paths

from optlab.marketdata.lake import Lake
from optlab.marketdata.replay import (
    ReplayConfig,
    cost_table,
    filled_fixings,
    replay,
    replay_book,
    strike_trade,
    struck_sheet,
)
from optlab.structured import CALENDARS, preset

STRIKE = dt.date(2020, 1, 15)
CFG = ReplayConfig(n_paths=2000, batch=2000, reval_every=20)


def scripted_paths(days):
    """SPX: flat near 100 %, down to 60 % at the end of March 2020, back above 100 % in late 2020."""
    t = pd.DatetimeIndex(days)
    level = np.interp(
        t.astype("int64"),
        pd.to_datetime(["2019-01-01", "2020-01-15", "2020-03-23", "2020-08-31", "2020-10-20", "2022-01-01"]).astype(
            "int64"
        ),
        [1.0, 1.0, 0.6, 0.85, 1.05, 1.2],
    )
    paths = price_paths(days)
    paths["SPX"] = 3000.0 * level
    return paths


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    lake = Lake(tmp_path_factory.mktemp("lake") / "lake")
    days = pd.bdate_range("2019-01-01", "2021-12-31")
    fill_observed(lake, days, scripted_paths(days))
    derive_all(lake, "2019-01-01", names=("SPX", "NDX"))
    return lake


def close_of(lake, day):
    rows = lake.read("prices_eod", where=f"instrument = 'SPX' AND obs_date = DATE '{day}'", columns="close")
    return float(rows["close"].iloc[0])


def first_below(lake, strike, barrier):
    rows = lake.read(
        "prices_eod",
        where=f"instrument = 'SPX' AND obs_date >= DATE '{strike}'",
        columns="obs_date, close",
        order="obs_date",
    )
    level = close_of(lake, strike) * barrier
    return pd.Timestamp(rows[rows["close"] < level]["obs_date"].iloc[0]).date()


@pytest.fixture(scope="module")
def brc(lake):
    sheet = preset("barrier_reverse_convertible", **{"general.maturity_months": 12, "downside.barrier": 0.7})
    trade, info = strike_trade(lake, "BRC-TEST", sheet, STRIKE, 1.0, CFG)
    return trade, info, replay(lake, trade, cfg=CFG, inception=info)


@pytest.fixture(scope="module")
def phoenix(lake):
    sheet = preset("phoenix", **{"general.maturity_months": 24, "coupon.barrier": 0.7, "downside.barrier": 0.6})
    trade, info = strike_trade(lake, "PHX-TEST", sheet, STRIKE, 1.0, CFG)
    return trade, info, replay(lake, trade, cfg=CFG, inception=info)


@pytest.mark.slow
def test_strike_solves_on_that_dates_market_then_fixes_the_coupon(brc):
    trade, info, _ = brc
    sheet = trade.term_sheet()
    assert sheet.get("wrapper.solve") == "upfront" and sheet.get("coupon.rate") == info["coupon"] > 0
    assert sheet.get("general.calendar") == "nyse" and trade.strike_date == STRIKE
    assert info["provenance"]["SPX"]["surface"]["layer"] == "reconstructed"


def test_brc_knocks_in_on_the_first_close_below_the_barrier(lake, brc):
    _, _, result = brc
    knocked = [e for e in result.events if e.kind == "knocked_in"]
    assert [e.date for e in knocked] == [first_below(lake, STRIKE, 0.7)]
    assert dt.date(2020, 3, 1) < knocked[0].date < dt.date(2020, 3, 31)


def test_brc_flows_match_an_independent_reading_of_the_fixings(lake, brc):
    trade, info, result = brc
    sheet = trade.term_sheet()
    final = sheet_final(sheet)
    perf = close_of(lake, final) / close_of(lake, STRIKE)
    coupons = [e.amount for e in result.events if e.kind == "coupon_paid"]
    assert coupons == pytest.approx([100 * info["coupon"] / 4] * 4)
    matured = next(e for e in result.events if e.kind == "matured")
    assert matured.amount == pytest.approx(100 * min(perf, 1.0))  # knocked in, so it takes the loss if there is one
    assert result.reconciliation["cash_gap"] == pytest.approx(0.0, abs=1e-9)
    assert result.reconciliation["cash_booked"] == pytest.approx(sum(coupons) + matured.amount)


def sheet_final(sheet):
    from optlab.structured.compiler import compile_sheet

    return compile_sheet(sheet, sheet.get("general.strike_date")).maturity


@pytest.mark.slow
def test_phoenix_is_called_on_the_first_observation_at_or_above_its_trigger(lake, phoenix):
    trade, info, result = phoenix
    sheet = trade.term_sheet()
    called = [e for e in result.events if e.kind == "called"]
    assert len(called) == 1
    start = close_of(lake, STRIKE)
    cal = CALENDARS["nyse"]
    for months in range(3, 25, 3):
        y, m = STRIKE.year + (STRIKE.month - 1 + months) // 12, (STRIKE.month - 1 + months) % 12 + 1
        obs = cal.adjust(dt.date(y, m, STRIKE.day), "modified_following")
        if close_of(lake, obs) >= start:
            break
    assert called[0].date == obs
    assert called[0].amount == pytest.approx(100.0)
    missed = [e.date for e in result.events if e.kind == "coupon_missed"]
    paid = [e for e in result.events if e.kind == "coupon_paid"]
    assert missed and paid
    after = next(e for e in paid if e.date > missed[-1])
    assert after.amount == pytest.approx(
        100 * info["coupon"] / 4 * (1 + len(missed))
    )  # the memory pays what was missed
    assert paid[-1].amount == pytest.approx(100 * info["coupon"] / 4)
    assert sheet.get("coupon.memory") is True


def test_cash_ends_the_trade_and_the_value_goes_to_zero(brc, phoenix):
    for trade, info, result in (brc, phoenix):
        d = result.daily
        assert d["cash"].sum() == pytest.approx(result.reconciliation["cash_from_events"])
        assert d["value"].iloc[-1] == pytest.approx(0.0, abs=1e-9)
        assert result.reconciliation["pnl_gap"] == pytest.approx(0.0, abs=1e-9)
        assert (d["value_se"] >= 0).all()


def test_known_flows_are_valued_at_their_discounted_amount_before_they_are_paid(lake, phoenix):
    """Between the call and its payment nothing is random: the value equals the discounted flows from the events."""
    trade, info, result = phoenix
    called = next(e for e in result.events if e.kind == "called")
    d = result.daily[result.daily["date"] == called.date].iloc[0]
    assert d["value_se"] == pytest.approx(0.0, abs=1e-8)
    flows = sum(e.amount for e in result.events if e.payment == called.payment and e.amount)
    assert d["value"] == pytest.approx(flows, rel=0.002)
    assert d["value"] < flows


def test_explained_pnl_is_published_with_its_residual(brc):
    _, _, result = brc
    x = result.explain
    assert x["steps"] == len(result.daily) - 1
    assert x["sum_pnl"] == pytest.approx(sum(x["sum"].values()) + x["sum_residual"], abs=1e-6)
    assert 0 <= x["residual_share"] < 1.0
    assert result.seconds["total"] > 0 and result.seconds["per_valuation"] > 0
    g = result.daily["greeks"].iloc[0]["SPX"]
    assert g["delta"] > 0  # a note holder is long the index


@pytest.mark.slow
def test_valuation_dates_use_only_past_fixings(lake):
    """A value before the knock-in does not change when the fixings after it are different."""
    sheet = preset("barrier_reverse_convertible", **{"general.maturity_months": 12, "downside.barrier": 0.7})
    trade, _ = strike_trade(lake, "BRC-A", sheet, STRIKE, 1.0, CFG, coupon=0.08)
    early = replay(lake, trade, end=dt.date(2020, 2, 28), cfg=CFG)
    full = replay(lake, trade, cfg=CFG)
    both = early.daily.merge(full.daily, on="date", suffixes=("_e", "_f"))
    both = both[both["date"] < early.daily["date"].iloc[-1]]
    assert len(both) >= 1
    assert (both["value_e"] == both["value_f"]).all()


@pytest.mark.slow
def test_book_adds_positions_by_date(lake):
    a, _ = strike_trade(
        lake,
        "A",
        preset("barrier_reverse_convertible", **{"general.maturity_months": 6}),
        STRIKE,
        1.0,
        CFG,
        coupon=0.08,
    )
    b, _ = strike_trade(
        lake,
        "B",
        preset("barrier_reverse_convertible", **{"general.maturity_months": 6}),
        STRIKE,
        -1.0,
        CFG,
        coupon=0.08,
    )
    out = replay_book(lake, [a, b], cfg=CFG)
    assert out["book"]["value"].abs().max() == pytest.approx(
        0.0, abs=1e-9
    )  # same seed, same product: opposite positions
    assert out["book"]["cash"].abs().max() == pytest.approx(0.0, abs=1e-9)
    assert len(out["results"]) == 2


@pytest.mark.slow
def test_worst_of_replay_on_two_names(lake):
    sheet = preset(
        "phoenix",
        **{"general.basket": "worst_of", "general.underlyings": ("SPX", "NDX"), "general.maturity_months": 12},
    )
    cfg = ReplayConfig(n_paths=2000, batch=2000, reval_every=60)
    trade, info = strike_trade(lake, "WO", sheet, STRIKE, 1.0, cfg)
    out = replay(lake, trade, cfg=cfg)
    assert set(out.daily["greeks"].iloc[0]) == {"SPX", "NDX"}
    assert out.reconciliation["cash_gap"] == pytest.approx(0.0, abs=1e-9)


def test_missing_trading_days_take_the_last_close_and_are_reported(tmp_path):
    lake = Lake(tmp_path)
    lake.ingest(
        "prices_eod",
        pd.DataFrame({"instrument": "SX5E", "obs_date": ["2020-05-20", "2020-05-22"], "close": [10.0, 12.0]}),
        "yahoo",
        "observed",
    )
    out, filled = filled_fixings(lake, ["SX5E"], dt.date(2020, 5, 20), dt.date(2020, 5, 22), "eurex")
    assert out["SX5E"] == {"2020-05-20": 10.0, "2020-05-21": 10.0, "2020-05-22": 12.0}
    assert filled == [("SX5E", "2020-05-21")]


def test_struck_sheet_uses_the_place_of_the_underlying():
    assert struck_sheet(preset("phoenix"), STRIKE).get("general.calendar") == "nyse"
    worst = preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": ("SPX", "SX5E")})
    assert struck_sheet(worst, STRIKE).get("general.calendar") == "weekend"


@pytest.mark.slow
def test_cost_table_is_measured(lake, brc):
    trade, _, _ = brc
    out = cost_table(lake, "SPX", dt.date(2020, 3, 2), trade, n_paths=(1000,), particles=(5000,))
    assert out["valuation"]["lv_1000"]["seconds"] > 0 and out["leverage"]["5000"]["seconds"] > 0
