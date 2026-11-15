"""The desk on the lake: Data, Vol lab and Replay routes, and what a shared desk must not show."""

import datetime as dt
import json

import pandas as pd
import pytest
from _desk import wait
from _quote_fixture import DAY, curve_rows, synthetic_rows

from optlab.marketdata.ingest import as_rows
from optlab.marketdata.lake import Lake
from optlab.marketdata.synthetic import derive_all, fill_observed
from optlab.server.api import ApiError, Desk

EXPIRIES = tuple(DAY + dt.timedelta(days=d) for d in (36, 110, 256, 400))


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    root = tmp_path_factory.mktemp("desk")
    lake = Lake(root / "lake")
    days = pd.bdate_range("2019-01-01", "2020-12-31")
    fill_observed(lake, days)
    derive_all(lake, "2019-01-01")
    lake.ingest("instruments", as_rows(dt.date(2018, 1, 1)), "registry", "observed")
    lake.ingest("discount_curves", curve_rows(), "test", "derived")
    lake.ingest("option_quotes", synthetic_rows(EXPIRIES), "archive", "observed")
    d = Desk(store=root / "optlab.sqlite", lake=root / "lake")
    yield d
    d.close()


@pytest.mark.slow
def test_overview_gives_coverage_with_origins_and_the_universe(desk):
    d = desk.data_overview()
    assert d["available"] is True and d["lake"]
    origins = {c["dataset"]: c["origin"] for c in d["coverage"]}
    assert (
        origins["prices_eod"] == "observed"
        and origins["vol_surfaces"] == "reconstructed"
        and origins["credit_spreads"] == "synthetic"
    )
    names = {i["id"]: i for i in d["instruments"]}
    assert names["SPX"]["origin"] == "observed" and names["SPX"]["n"] > 0 and names["NVDA"]["enabled"] is False
    json.dumps(d)


def test_an_empty_lake_says_so(tmp_path):
    d = Desk(store=tmp_path / "optlab.sqlite", lake=tmp_path / "none")
    try:
        assert d.data_overview()["available"] is False
        assert d.lab_days()["available"] is False
        assert d.replay_runs()["available"] is False
    finally:
        d.close()


def test_market_at_a_past_date_becomes_a_snapshot_the_desk_can_use(desk):
    before = desk.ctx.snapshot_id
    out = wait(desk, desk.market_from_lake({"underlying": "SPX", "date": "2020-06-15", "leverage": False})["job"])
    assert out["provenance"]["surface"]["origin"] == "reconstructed" and out["valuation"] == "2020-06-15"
    ctx = desk.use_snapshot({"snapshot_id": out["snapshot_id"]})
    try:
        assert ctx["valuation"] == "2020-06-15" and ctx["origin"]["surface"] == "reconstructed"
        assert ctx["origin"]["extrapolated_from"] == 1.0 and ctx["origin"]["layer"] == "reconstructed"
        stored = desk.fixings_from_lake({"underlying": "SPX", "start": "2020-06-01"})
        assert stored["origin"] == "observed" and stored["last"] == "2020-06-15"
        level = (
            {f["date"]: f["level"] for f in desk.fixings("SPX")["fixings"]}["2020-06-15"]
            if isinstance(desk.fixings("SPX")["fixings"], list)
            else desk.fixings("SPX")["fixings"]["SPX"]["2020-06-15"]
        )
        assert level == pytest.approx(100.0)
    finally:
        desk.use_snapshot({"snapshot_id": before})
    with pytest.raises(ApiError):
        desk.market_from_lake({"underlying": "SPX", "date": "soon"})


def test_the_calibrated_market_says_it_is_a_fit_on_observed_quotes(desk):
    assert desk.context()["origin"]["surface"] == "observed"
    assert desk.health()["origin"]["surface"] == "observed"


def test_lab_lists_fits_and_replays_runs(desk):
    days = desk.lab_days()
    assert days["sets"][0]["underlying"] == "SPX" and list(days["models"]) == ["svi", "ssvi", "heston", "slv"]
    q = desk.lab_quotes("SPX", str(DAY), "archive")
    assert q["origin"] == "observed" and q["slices"]
    res = wait(
        desk,
        desk.lab_fit({"underlying": "SPX", "day": str(DAY), "source": "archive", "models": ["svi", "heston"]})["job"],
    )
    assert set(res["fits"]) == {"svi", "heston"} and res["origin"] == "observed"
    rid = res["fits"]["svi"]["calibration_id"]
    assert any(r["calibration_id"] == rid for r in desk.lab_runs("SPX")["runs"])
    again = wait(desk, desk.lab_reproduce(rid)["job"])
    assert again["same_quotes"] and again["largest_parameter_gap"] == pytest.approx(0.0, abs=1e-10)
    with pytest.raises(ApiError):
        desk.lab_fit({"underlying": "SPX", "day": str(DAY), "models": ["guess"]})
    with pytest.raises(ApiError, match="no quotes"):
        desk.lab_quotes("SPX", "2021-01-04")


def test_a_shared_desk_keeps_quotes_and_levels_private(desk):
    desk.shared = True
    try:
        with pytest.raises(ApiError) as exc:
            desk.lab_quotes("SPX", str(DAY), "archive")
        assert exc.value.status == 403
        with pytest.raises(ApiError) as exc:
            desk.data_vol_index("VIX")
        assert exc.value.status == 403
        over = desk.data_overview()
        assert over["lake"] is None and over["shared"] is True
        assert desk.lab_days()["shared"] is True
        res = wait(
            desk, desk.lab_fit({"underlying": "SPX", "day": str(DAY), "source": "archive", "models": ["svi"]})["job"]
        )
        text = json.dumps(res)
        assert "iv_bid" not in text and "iv_ask" not in text and "bid" not in text.replace("median_half", "")
    finally:
        desk.shared = False


def test_the_lab_fit_result_never_carries_raw_quotes(desk):
    res = wait(
        desk, desk.lab_fit({"underlying": "SPX", "day": str(DAY), "source": "archive", "models": ["ssvi"]})["job"]
    )
    assert (
        "slices" not in res and "table" not in res and {"curves", "params", "by_maturity"} <= set(res["fits"]["ssvi"])
    )


@pytest.mark.slow
def test_replay_runs_as_a_job_and_is_kept_without_real_levels(desk):
    body = {
        "preset": "barrier_reverse_convertible",
        "strike": "2020-01-15",
        "every": 40,
        "n_paths": 2000,
        "changes": {"general.maturity_months": 6},
    }
    out = wait(desk, desk.replay_run(body)["job"])
    assert out["trade_id"].startswith("barrier-reverse-convertible-SPX") and out["reconciliation"][
        "cash_gap"
    ] == pytest.approx(0.0, abs=1e-9)
    assert out["daily"][0]["perf"]["SPX"] == pytest.approx(1.0) and "spot" not in out["daily"][0]
    assert {v for d in out["daily"] for v in d["layer"].values()} == {"reconstructed"}
    runs = desk.replay_runs()["runs"]
    mine = next(r for r in runs if r["kind"] == "desk")
    assert mine["trade_id"] == out["trade_id"]
    assert desk.replay_get(mine["id"])["trade_id"] == out["trade_id"]
    with pytest.raises(ApiError):
        desk.replay_get("../secret")
    with pytest.raises(ApiError):
        desk.replay_run({"preset": "none", "strike": "2020-01-15"})
    with pytest.raises(ApiError):
        desk.replay_run({"preset": "phoenix", "strike": "soon"})
    with pytest.raises(ApiError):
        desk.replay_run({"preset": "phoenix", "strike": "2020-01-15", "model": "bs"})


@pytest.mark.slow
def test_showcase_seed_fills_a_store_with_trades_struck_on_their_own_markets(tmp_path):
    from optlab.marketdata.synthetic import synthetic_lake
    from optlab.server.api import Desk
    from optlab.server.tools import showcase as mod

    lake, days, _ = synthetic_lake(tmp_path / "lake", "2019-01-01", "2021-06-30")
    desk = Desk(tmp_path / "store" / "optlab.sqlite", lake=tmp_path / "lake")
    try:
        deals = (
            mod.Deal("SHOW-A", "phoenix", ("SPX",), "2020-06-15", 24, 1e6, "Client", {"coupon.barrier": 0.8}),
            mod.Deal("SHOW-B", "barrier_reverse_convertible", ("SPX",), "2020-09-15", 18, 2e6, "Client"),
        )
        import time

        import pandas as pd

        from optlab.marketdata.derive.bank import spread_at

        day = pd.Timestamp("2021-06-30").date()
        added = mod.seed(desk, lake, day, deals, n_paths=2000, log=lambda s: None, look_back=())
        assert added == ["SHOW-A", "SHOW-B"]
        assert desk.ctx.valuation == day and desk.ctx.market.spot == 100.0
        trades = {t.trade_id: t for t in desk.repo.trades()}
        assert trades["SHOW-A"].strike_date.isoformat() == "2020-06-15" and trades["SHOW-A"].quantity == -1.0
        sheet = trades["SHOW-A"].term_sheet()
        assert sheet.get("wrapper.solve") == "upfront" and 0 < sheet.get("coupon.rate") < 1
        assert sheet.get("wrapper.issuer_spread") == pytest.approx(spread_at(lake, "2020-06-15", 2.0))
        stored = desk.repo.fixings("SPX")["SPX"]
        assert stored[day] == pytest.approx(100.0)  # the fixings are in the scale of the market in use
        assert desk.trade_events("SHOW-A")["missing"] is None
        # fixings of another name, or later than the market in use, do not concern this trade
        desk.repo.put_fixings("NDX", {"2021-01-04": 100.0}, "test")
        desk.repo.put_fixings("SPX", {"2021-07-01": 100.0}, "test")
        got = desk.trade_events("SHOW-A")
        assert got["missing"] is None and got["past"]
        job = desk.price_book({"options": {"precision": "fast", "greeks": False}})["job"]
        for _ in range(240):
            state = desk.job(job)
            if state["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        assert state["state"] == "done", state["error"]
        live = {t.trade_id for t in desk.repo.trades() if t.status == "live"}
        assert live and {p["name"] for p in state["result"]["positions"]} == live
        # a second run keeps what is there
        assert mod.seed(desk, lake, day, deals, n_paths=2000, log=lambda s: None, look_back=()) == []
    finally:
        desk.close()


def test_data_screen_shows_the_latest_basket_spreads_as_synthetic(tmp_path):
    import pandas as pd

    from optlab.marketdata.lake import Lake
    from optlab.server.api import Desk

    desk = Desk(tmp_path / "store" / "optlab.sqlite", lake=tmp_path / "lake")
    try:
        assert desk.data_baskets() == {"rows": [], "day": None}
        row = {
            "basket": "SPX+NDX",
            "kind": "cvc",
            "strike": 1.1,
            "price": -0.1,
            "price_se": 0.01,
            "basket_price": 5.0,
            "members_price": 5.1,
            "basket_vol": 0.2,
            "rho": 0.8,
            "implied_rho": 0.82,
        }
        rows = pd.DataFrame(
            [{**row, "tenor": 1.0, "obs_date": d} for d in ("2026-09-30", "2026-10-30")]
            + [{**row, "tenor": 0.5, "obs_date": "2026-10-30"}]
        )
        Lake(tmp_path / "lake").ingest("basket_spreads", rows, "copula_on_surfaces", "synthetic")
        got = desk.data_baskets()
        assert got["day"] == "2026-10-30" and len(got["rows"]) == 1 and got["rows"][0]["origin"] == "synthetic"
    finally:
        desk.close()
