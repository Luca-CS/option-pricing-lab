"""The plan's exit criteria on the real data of the lake. Skipped without it: the data is personal-use and never versioned."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from optlab.config import paths
from optlab.marketdata.lake import Lake
from optlab.marketdata.pit import basket_at, context_at, market_at, real_close, truncated_copy
from optlab.marketdata.quality import check_splits
from optlab.marketdata.quotes import clean_day, quote_days
from optlab.marketdata.universe import enabled_names
from optlab.structured import CALENDARS, preset, quote

LAKE = Lake()
pytestmark = pytest.mark.skipif(not LAKE.has("prices_eod") or not LAKE.has("vol_surfaces"), reason="no data lake on this machine")
TABLES = ("prices_eod", "corporate_actions", "vol_indices", "discount_curves", "carry_implied", "vol_surfaces", "correl_implied")
RESULTS = paths().results


def closes(name):
    rows = LAKE.read("prices_eod", where=f"instrument = '{name}'", columns="obs_date, close", order="obs_date")
    return pd.Series(rows["close"].to_numpy(), index=pd.to_datetime(rows["obs_date"]))


def test_splits_are_continuous_in_the_real_closes():
    actions = LAKE.read("corporate_actions", where="kind = 'split'")
    assert {"AAPL", "AMZN", "GOOGL"} <= set(actions["instrument"])
    prices = LAKE.read("prices_eod", columns="instrument, obs_date, close")
    assert check_splits(prices, actions) == []


def test_every_nyse_trading_day_has_a_close_and_no_other_day_does():
    days = closes("SPX")
    cal = CALENDARS["nyse"]
    expected = cal.business_days(days.index[0].date(), days.index[-1].date())
    assert [d.date() for d in days.index] == expected


def test_all_enabled_names_have_prices_and_a_surface_on_a_stress_date():
    day = dt.date(2020, 3, 23)
    for name in enabled_names():
        snap = market_at(LAKE, name, day)
        assert snap.market["spot"] == 100.0 and snap.spot_scale > 0
        assert snap.provenance["surface"]["layer"] == "reconstructed"


@pytest.mark.parametrize("day", [dt.date(2020, 3, 23), dt.date(2022, 7, 1)])
def test_erasing_the_future_changes_no_past_price_on_real_data(tmp_path, day):
    cut = truncated_copy(LAKE, tmp_path / "cut", day, tables=TABLES,
                         where_extra={"prices_eod": "instrument IN ('SPX', 'NDX', 'SPX.TR')",
                                      "vol_surfaces": "underlying IN ('SPX', 'NDX')",
                                      "carry_implied": "underlying IN ('SPX', 'NDX')",
                                      "corporate_actions": "instrument = 'SPX'"})
    assert pd.to_datetime(cut.read("prices_eod")["obs_date"]).max().date() == day
    a, b = market_at(LAKE, "SPX", day), market_at(cut, "SPX", day)
    assert a.snapshot_id == b.snapshot_id and a.spot_scale == b.spot_scale
    sheet = preset("phoenix", **{"general.strike_date": day, "general.calendar": "nyse"})
    full = quote(sheet, context_at(LAKE, "SPX", day)[0], model="lv", control=None, n_paths=4000, batch=4000)
    short = quote(sheet, context_at(cut, "SPX", day)[0], model="lv", control=None, n_paths=4000, batch=4000)
    assert full.coupon_rate == short.coupon_rate and full.result.price == short.result.price
    corr_a, corr_b = basket_at(LAKE, ["SPX", "NDX"], day)[0].corr, basket_at(cut, ["SPX", "NDX"], day)[0].corr
    assert (corr_a == corr_b).all()


def test_the_market_at_a_date_uses_that_dates_close():
    px = closes("SPX")
    assert real_close(LAKE, "SPX", dt.date(2020, 3, 23)) == (px.loc["2020-03-23"], dt.date(2020, 3, 23))
    assert real_close(LAKE, "SPX", dt.date(2020, 3, 22))[1] == dt.date(2020, 3, 20)  # a Sunday: never the Monday after


def test_a_2022_archive_day_and_a_yahoo_day_go_through_the_same_chain():
    archive = clean_day(LAKE, "SPX", quote_days(LAKE, "SPX", "historicaldata")[-1], "historicaldata")
    assert len(archive.slices) > 20 and archive.maturities.max() > 4.0
    day = quote_days(LAKE, "SPX", "yahoo")[-1]
    live = clean_day(LAKE, "SPX", day, "yahoo")
    assert len(live.slices) >= 1 and live.source == "yahoo"
    for s in (archive.slices[0], live.slices[0]):
        assert set(s) >= {"T", "F", "D", "K", "k", "iv", "spread_iv"}


REPLAYS = {"BRC-2020": "replay_brc_lv.json", "PHX-2022": "replay_phx_lv.json"}


def replayed(name):
    path = RESULTS / REPLAYS[name]
    if not path.exists():
        pytest.skip(f"{path.name} not produced yet: scripts/lake_replay.py run")
    return json.loads(path.read_text(encoding="utf-8"))[name]


def test_the_brc_struck_in_early_2020_knocked_in_in_march_2020():
    r = replayed("BRC-2020")
    knocked = [e for e in r["events"] if e["kind"] == "knocked_in"]
    px = closes("SPX")
    below = px[(px.index >= "2020-01-15") & (px < 0.7 * px.loc["2020-01-15"])]
    assert [e["date"] for e in knocked] == [str(below.index[0].date())]
    assert knocked[0]["date"].startswith("2020-03")


def test_the_phoenix_was_called_on_the_right_date():
    r = replayed("PHX-2022")
    px = closes("SPX")
    start = px.loc["2022-01-14"]
    cal = CALENDARS["nyse"]
    called = [e for e in r["events"] if e["kind"] == "called"]
    assert len(called) == 1
    for months in range(3, 37, 3):
        y, m = 2022 + months // 12, 1 + months % 12
        obs = cal.adjust(dt.date(y, m, 14), "modified_following")
        if px.loc[str(obs)] >= start:
            break
    assert called[0]["date"] == str(obs)


@pytest.mark.parametrize("name", list(REPLAYS))
def test_flows_reconcile_with_the_lifecycle_events(name):
    r = replayed(name)
    rec = r["reconciliation"]
    assert rec["cash_gap"] == pytest.approx(0.0, abs=1e-9) and rec["pnl_gap"] == pytest.approx(0.0, abs=1e-9)
    assert rec["value_end"] == pytest.approx(0.0, abs=1e-9)
    assert sum(d["cash"] for d in r["daily"]) == pytest.approx(rec["cash_from_events"])
    assert "spot" not in r["daily"][0]  # no real level in a versioned result
    assert r["explain"]["steps"] == len(r["daily"]) - 1


def test_the_crash_keeps_its_at_the_money_level():
    """23/03/2020, against real SPY chains: at about a month the reconstruction was 5.6 points low before the slope fell with the level."""
    from optlab.marketdata.derive.surfaces import surface_at
    from optlab.marketdata.quotes import clean_day
    day = dt.date(2020, 3, 23)
    surface = surface_at(LAKE, "SPX", day, layer="reconstructed").surface
    chain = clean_day(LAKE, "SPY", day, "dolthub", spot=0.1 * real_close(LAKE, "SPX", day)[0])
    for s in chain.slices:
        if 0.05 < s["T"] < 0.2:
            order = np.argsort(s["k"])
            atm = float(np.interp(0.0, np.asarray(s["k"])[order], np.asarray(s["iv"])[order]))
            model = float(np.sqrt(surface.theta(s["T"]) / s["T"]))
            assert abs(model - atm) < 0.04


def test_dividends_give_the_forward_its_steps():
    from optlab.marketdata.pit import market_curves
    market, _, prov = market_curves(LAKE, "IBM", dt.date(2022, 7, 1))
    assert prov["carry"]["method"] == "dividend_dates"
    assert 0.03 < prov["carry"]["dividend_yield"] < 0.07
    T = np.linspace(0.01, 1.0, 400)
    jumps = np.log(market.F(T[1:]) / market.F(T[:-1]))
    assert (jumps < -0.005).sum() >= 3  # IBM pays four times a year, each over one percent of spot


def test_the_desk_prices_a_share_and_a_worst_of_on_their_real_surfaces(tmp_path):
    from optlab.app.api import Desk

    desk = Desk(store=tmp_path / "desk.sqlite")
    try:
        day = dt.date(2026, 11, 4)
        snap = market_at(LAKE, "SPX", day, layer="reconstructed")
        desk.use_snapshot({"snapshot_id": desk.market_store.put(snap)})
        ctx, shown = desk._single_ctx("AAPL", desk.ctx)
        assert ctx.underlying == "AAPL" and shown["names"]["AAPL"]["origin"] == "reconstructed"
        assert ctx.atm_vol(1.0) > desk.ctx.atm_vol(1.0)  # a share is more volatile than the index
        phoenix = preset("phoenix", **{"general.underlying": "AAPL"})
        q = quote(phoenix, ctx, model="lv", control=None, n_paths=20_000, batch=10_000)
        spx = quote(preset("phoenix"), desk.ctx, model="lv", control=None, n_paths=20_000, batch=10_000)
        assert q.coupon_rate > spx.coupon_rate + 4 * np.hypot(q.coupon_rate_se, spx.coupon_rate_se)
        bctx, shown = desk._basket_ctx(("SPX", "NDX", "AAPL"))
        assert bctx.has_surfaces and {r["origin"] for r in shown["names"].values()} == {"reconstructed"}
        assert all(v == "lake" for v in shown["corr_source"].values()) and 0.3 < shown["corr"]["NDX|SPX"] < 1
    finally:
        desk.jobs.shutdown()


def test_slv_on_a_real_basket_reprices_each_name_once_the_noise_of_the_forward_is_out():
    from optlab.blackscholes import black_price
    from optlab.structured.multiasset import simulate_basket
    from optlab.structured.pricer import _controlled

    names = ["SPX", "NDX", "RUT"]
    ctx, _, _ = basket_at(LAKE, names, "2026-11-04", layer="reconstructed")
    n = 200_000
    paths = simulate_basket(ctx.model("slv", 2.0, mix=0.5, spot_driven=True), [1.0, 2.0], n, 11)
    half = n // 2
    for name in names:
        market, surface = ctx.markets[name], ctx.vols[name]
        for j, T in enumerate((1.0, 2.0)):
            F = float(market.F(T))
            s = paths[name][:, j]
            forward = 0.5 * (s[:half] + s[half:]) / F - 1.0
            for pct in (0.9, 1.0, 1.1):
                pay = np.maximum(s - pct * F, 0.0)
                pay = 0.5 * (pay[:half] + pay[half:])
                corrected, _ = _controlled(pay, forward[:, None])
                se = corrected.std(ddof=1) / np.sqrt(half)
                vol = float(surface.iv(np.log(pct), T))
                want = float(black_price(F, pct * F, T, vol, 1.0, "call"))
                vega = F * np.sqrt(T) * 0.3  # a lower bound of the vega near the money, for the bias of the leverage: 10 bp of vol
                assert abs(corrected.mean() - want) <= 4 * se + 0.001 * vega, (name, T, pct, corrected.mean(), want, se)
