"""The desk on the lake: Data, Vol lab and Replay routes, and what a shared desk must not show."""

import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import pytest
from _lake_fixture import fill_observed, derive_all
from test_lake_quotes import DAY, curve_rows, synthetic_rows

from optlab.app.api import ApiError, Desk
from optlab.app.server import routes
from optlab.marketdata.lake import Lake
from optlab.marketdata.universe import as_rows

STATIC = Path(__file__).resolve().parents[1] / "optlab" / "app" / "static"
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
    d = Desk(store=root / "desk.sqlite", lake=root / "lake")
    yield d
    d.jobs.shutdown()


def wait(desk, job_id, timeout=300):
    t0 = time.perf_counter()
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        assert time.perf_counter() - t0 < timeout
        time.sleep(0.05)
    assert j["state"] == "done", j.get("error")
    return j["result"]


def test_overview_gives_coverage_with_origins_and_the_universe(desk):
    d = desk.data_overview()
    assert d["available"] is True and d["lake"]
    origins = {c["dataset"]: c["origin"] for c in d["coverage"]}
    assert origins["prices_eod"] == "observed" and origins["vol_surfaces"] == "reconstructed" and origins["credit_spreads"] == "synthetic"
    names = {i["id"]: i for i in d["instruments"]}
    assert names["SPX"]["origin"] == "observed" and names["SPX"]["n"] > 0 and names["NVDA"]["enabled"] is False
    json.dumps(d)


def test_an_empty_lake_says_so(tmp_path):
    d = Desk(store=tmp_path / "desk.sqlite", lake=tmp_path / "none")
    try:
        assert d.data_overview()["available"] is False
        assert d.lab_days()["available"] is False
        assert d.replay_runs()["available"] is False
    finally:
        d.jobs.shutdown()


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
        level = {f["date"]: f["level"] for f in desk.fixings("SPX")["fixings"]}["2020-06-15"] if isinstance(desk.fixings("SPX")["fixings"], list) \
            else desk.fixings("SPX")["fixings"]["SPX"]["2020-06-15"]
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
    res = wait(desk, desk.lab_fit({"underlying": "SPX", "day": str(DAY), "source": "archive", "models": ["svi", "heston"]})["job"])
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
        res = wait(desk, desk.lab_fit({"underlying": "SPX", "day": str(DAY), "source": "archive", "models": ["svi"]})["job"])
        text = json.dumps(res)
        assert "iv_bid" not in text and "iv_ask" not in text and "bid" not in text.replace("median_half", "")
    finally:
        desk.shared = False


def test_the_lab_fit_result_never_carries_raw_quotes(desk):
    res = wait(desk, desk.lab_fit({"underlying": "SPX", "day": str(DAY), "source": "archive", "models": ["ssvi"]})["job"])
    assert "slices" not in res and "table" not in res and {"curves", "params", "by_maturity"} <= set(res["fits"]["ssvi"])


def test_replay_runs_as_a_job_and_is_kept_without_real_levels(desk):
    body = {"preset": "barrier_reverse_convertible", "strike": "2020-01-15", "every": 40, "n_paths": 2000,
            "changes": {"general.maturity_months": 6}}
    out = wait(desk, desk.replay_run(body)["job"])
    assert out["trade_id"].startswith("barrier-reverse-convertible-SPX") and out["reconciliation"]["cash_gap"] == pytest.approx(0.0, abs=1e-9)
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


def test_routes_and_screens_are_wired(desk):
    table = routes(desk)
    paths = {p for _, p, _ in table}
    for path in ("/api/data/overview", "/api/vollab/days", "/api/vollab/fit", "/api/replay/run"):
        assert path in paths
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    main = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    for view in ("vollab", "replay", "data"):
        assert (STATIC / "js" / "views" / f"{view}.js").exists()
        assert f'data-route="{view}"' in index and f"{view}: () => import" in main
    for view in ("vollab", "replay", "data", "overview"):
        assert "originBadge" in (STATIC / "js" / "views" / f"{view}.js").read_text(encoding="utf-8")
