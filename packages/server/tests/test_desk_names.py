"""Every name of the Pricer and the Book on its own market from the lake: a sheet on another name, a worst-of, and what is
typed by hand or missing from the lake."""

import time

import numpy as np
import pandas as pd
import pytest
from _lake_fixture import derive_all, fill_observed

from optlab.marketdata.derive.correl import correlation_at
from optlab.marketdata.lake import Lake
from optlab.marketdata.names import NameMarkets
from optlab.marketdata.pit import context_at, market_at
from optlab.server.api import ApiError, Desk
from optlab.structured import preset, quote
from optlab.structured.multiasset import BasketContext

DAY = "2020-06-15"
PATHS = 4000


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    root = tmp_path_factory.mktemp("names")
    lake = Lake(root / "lake")
    fill_observed(lake, pd.bdate_range("2019-01-01", "2020-12-31"))
    derive_all(lake, "2019-01-01", names=("SPX", "NDX", "AAPL"))
    d = Desk(store=root / "optlab.sqlite", lake=root / "lake")
    d.use_snapshot(
        {"snapshot_id": d.market_store.put(market_at(lake, "SPX", DAY, leverage="calibrate", n_particles=20_000))}
    )
    yield d
    d.jobs.shutdown()


def wait(desk, job_id, timeout=300):
    t0 = time.perf_counter()
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        assert time.perf_counter() - t0 < timeout
        time.sleep(0.05)
    assert j["state"] == "done", j.get("error")
    return j["result"]


def body(name="phoenix", **general):
    s = preset(name).to_dict(json_safe=True)
    s.pop("type")
    s["general"].update(general)
    s["wrapper"]["margin"] = 0.02
    return {"type": preset(name).mask.name, "values": s}


def options(**kw):
    return {"precision": "quick", "n_paths": PATHS, "greeks": False, "control": False, "costs": False, **kw}


@pytest.mark.slow
def test_a_sheet_on_another_name_is_priced_on_that_names_market(desk):
    q = wait(desk, desk.quote({"sheet": body(underlying="AAPL"), "options": options(model="lv")})["job"])
    ctx, _ = context_at(desk.lake, "AAPL", DAY, layer="reconstructed")
    direct = quote(
        preset("phoenix", **{"general.underlying": "AAPL"}), ctx, model="lv", control=None, n_paths=PATHS, batch=10_000
    )
    assert q["coupon_rate"] == pytest.approx(direct.coupon_rate, abs=1e-12)
    row = q["basket"]["names"]["AAPL"]
    assert q["basket"]["kind"] == "single" and row["source"] == "lake" and row["origin"] == "reconstructed"
    spx = wait(desk, desk.quote({"sheet": body(), "options": options(model="lv")})["job"])
    assert (
        spx["basket"] is None and abs(spx["coupon_rate"] - q["coupon_rate"]) > 0.01
    )  # another surface, another coupon


@pytest.mark.slow
def test_a_market_without_a_leverage_calibrates_on_first_slv_use(desk):
    ctx, _ = desk.names.context("AAPL", DAY)
    assert ctx.leverage_flat and ctx.slv.leverage.values.shape[0] == 1
    q = wait(desk, desk.quote({"sheet": body(underlying="AAPL"), "options": options(model="slv")})["job"])
    assert q["policy"]["model"] == "slv"
    model = ctx.model("slv", 2.0)
    assert model.leverage.values.shape[0] > 5 and ctx.model("slv", 1.5) is model  # one band, kept in the context


def test_a_name_the_lake_does_not_hold_is_refused_as_before(desk):
    job = desk.quote({"sheet": body(underlying="GS"), "options": options()})[
        "job"
    ]  # GS has prices but no surface in this lake
    deadline = time.perf_counter() + 60
    while (j := desk.job(job))["state"] in ("queued", "running"):
        assert time.perf_counter() < deadline
        time.sleep(0.05)
    assert j["state"] == "error" and "choose a market of that name" in j["error"]["message"]


def test_a_worst_of_has_every_name_on_its_own_surface_and_a_measured_correlation(desk):
    q = wait(
        desk,
        desk.quote({"sheet": body(basket="worst_of", underlyings=["SPX", "NDX", "AAPL"]), "options": options()})["job"],
    )
    names = q["basket"]["names"]
    assert names["SPX"]["source"] == "active" and names["NDX"]["source"] == names["AAPL"]["source"] == "lake"
    assert all(names[n]["origin"] == "reconstructed" and names[n]["layer"] == "reconstructed" for n in ("NDX", "AAPL"))
    measured, meta = correlation_at(desk.lake, ["SPX", "NDX", "AAPL"], DAY)
    assert (
        q["basket"]["corr"]["NDX|SPX"] == pytest.approx(measured[0, 1])
        and q["basket"]["corr_source"]["NDX|SPX"] == "lake"
    )
    assert q["basket"]["correlation"] == meta
    bctx, _ = desk._basket_ctx(("SPX", "NDX", "AAPL"))
    assert isinstance(bctx, BasketContext) and bctx.has_surfaces and bctx.names == ("SPX", "NDX", "AAPL")
    smile = [float(bctx.vols[n].iv(-0.2, 1.0) - bctx.vols[n].iv(0.0, 1.0)) for n in bctx.names]
    assert min(smile) > 0.005  # each name has a skew, not a flat vol
    direct = quote(
        preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": ["SPX", "NDX", "AAPL"]}),
        bctx,
        control=None,
        n_paths=PATHS,
        batch=10_000,
    )
    assert q["coupon_rate"] == pytest.approx(direct.coupon_rate, abs=1e-12)


def test_what_is_typed_wins_over_the_lake_and_what_is_missing_is_said(desk):
    typed = {"names": {"NDX": {"vol": 0.31, "q": 0.01, "r": 0.02}}, "corr": {"NDX|SPX": 0.5}}
    bctx, shown = desk._basket_ctx(("SPX", "NDX", "AAPL"), typed)
    assert (
        bctx.vols["NDX"] == 0.31
        and shown["names"]["NDX"]["source"] == "input"
        and shown["names"]["NDX"]["origin"] == "synthetic"
    )
    assert shown["corr"]["NDX|SPX"] == 0.5 and shown["corr_source"]["NDX|SPX"] == "input"
    assert shown["names"]["AAPL"]["source"] == "lake" and shown["corr_source"]["AAPL|SPX"] == "lake"
    assert shown["corr_source"]["AAPL|NDX"] == "default"  # NDX is typed: no measure applies to it
    bctx, shown = desk._basket_ctx(("SPX", "NKY"), {})  # declared, not loaded
    assert (
        np.isscalar(bctx.vols["NKY"])
        and shown["names"]["NKY"]["source"] == "default"
        and shown["corr_source"]["NKY|SPX"] == "default"
    )


def test_a_basket_without_the_active_name_discounts_on_its_first_name(desk):
    bctx, shown = desk._basket_ctx(("NDX", "AAPL"))
    assert (
        bctx.names == ("NDX", "AAPL")
        and bctx.market is bctx.markets["NDX"]
        and shown["corr_source"]["AAPL|NDX"] == "lake"
    )


@pytest.mark.slow
def test_the_book_prices_one_group_per_name_and_the_worst_ofs(desk):
    tids = []
    for sheet, rate in (
        (body(), 0.05),
        (body(underlying="AAPL"), 0.09),
        (body(basket="worst_of", underlyings=["SPX", "AAPL"]), 0.1),
    ):
        tids.append(desk.create_trade({"sheet": sheet, "coupon_rate": rate, "quantity": -1.0})["trade_id"])
    try:
        book = wait(desk, desk.price_book({"options": options(greeks=True, model="lv")})["job"])
        groups = {g["label"]: g for g in book["groups"]}
        assert set(groups) == {"SPX", "AAPL", "worst-of"}
        assert groups["SPX"]["basket"] is None and groups["AAPL"]["basket"]["kind"] == "single"
        assert groups["worst-of"]["basket"]["names"]["AAPL"]["source"] == "lake"
        positions = {p["name"]: p for p in book["positions"]}
        assert positions[tids[1]]["group"] == "AAPL" and positions[tids[2]]["group"] == "worst-of"
        by_name = book["greeks"]["by_name"]
        assert set(by_name) == {"SPX", "AAPL"} and by_name["AAPL"]["delta"] != 0
        assert book["stderr"] == pytest.approx(sum(g["stderr"] ** 2 for g in book["groups"]) ** 0.5)
        v = wait(desk, desk.value_trade(tids[1], {"options": options(model="lv")})["job"])
        assert v["basket"]["names"]["AAPL"]["source"] == "lake" and v["unit_stderr"] > 0
    finally:
        for t in tids:
            desk.delete_trade(t)


def test_a_book_trade_on_a_name_without_a_market_says_which(desk):
    t = desk.create_trade({"sheet": body(underlying="GS"), "coupon_rate": 0.05, "quantity": -1.0})["trade_id"]
    try:
        job = desk.price_book({"options": options(model="lv")})["job"]
        deadline = time.perf_counter() + 60
        while (j := desk.job(job))["state"] in ("queued", "running"):
            assert time.perf_counter() < deadline
            time.sleep(0.05)
        assert j["state"] == "error" and t in j["error"]["message"] and "no market of GS" in j["error"]["message"]
    finally:
        desk.delete_trade(t)
    with pytest.raises(ApiError):
        desk.price_book({"options": options()})  # nothing live is left


@pytest.mark.slow
def test_name_markets_keep_one_context_per_name_and_date(tmp_path):
    lake = Lake(tmp_path / "lake")
    fill_observed(lake, pd.bdate_range("2019-01-01", "2020-12-31"))
    derive_all(lake, "2019-01-01", names=("SPX",))
    names = NameMarkets(lake, kept=2)
    a = names.context("SPX", DAY)
    assert names.context("SPX", DAY) is a
    assert names.context("SPX", "2020-06-16") is not a
    names.context("SPX", "2020-06-17")
    assert names.context("SPX", DAY) is not a  # the oldest went away
    with pytest.raises(KeyError):
        names.context("AAPL", DAY)
