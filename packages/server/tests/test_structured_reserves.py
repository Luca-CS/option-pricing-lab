"""Reserves: frozen with the trade, linear over the contractual life, released at a call, in the Book and in a replay where the P&L still adds up."""

import datetime as dt
import json
import time

import pandas as pd
import pytest

from optlab.marketdata.replay import ReplayConfig, replay, result_json, strike_trade
from optlab.marketdata.synthetic import synthetic_lake
from optlab.server.api import Desk
from optlab.server.report import book_report
from optlab.structured import Trade, preset
from optlab.structured.defaults import resolve
from optlab.structured.reserves import METHOD, fraction_left, frozen, remaining

DESK_PRICE = {
    "side": "sell",
    "base_price": 97.0,
    "final_price": 98.0,
    "lines": [
        {"key": "slv", "label": "Model: SLV", "kind": "model", "cost": 0.6, "se": 0.03},
        {"key": "vol", "label": "Volatility", "kind": "bump", "cost": 0.5, "se": 0.01},
        {"key": "interaction", "label": "Interaction", "kind": "interaction", "cost": -0.1, "se": 0.01},
    ],
}


def sold(status="live", quantity=-2.0):
    sheet = preset("phoenix", **{"general.notional": 1000.0})
    t = Trade.from_term_sheet("T", sheet, quantity, "2026-01-01", "2026-01-01", status=status)
    import dataclasses

    return dataclasses.replace(t, reserves=frozen(DESK_PRICE, "2026-01-01", "2028-01-01"))


def test_the_record_keeps_every_line_at_its_value_of_the_day():
    r = frozen(DESK_PRICE, dt.date(2026, 1, 1), dt.date(2028, 1, 1), "abc")
    assert r["total"] == pytest.approx(1.0) and r["client_price"] - r["base_price"] == pytest.approx(r["total"])
    assert r["issue"] == "2026-01-01" and r["end"] == "2028-01-01" and r["method"] == METHOD and r["quote_id"] == "abc"
    json.dumps(r)


def test_it_amortises_by_calendar_days_and_ends_at_a_call_or_at_maturity():
    r = frozen(DESK_PRICE, "2026-01-01", "2028-01-01")
    assert fraction_left(r, "2025-12-01") == 1.0 and fraction_left(r, "2026-01-01") == 1.0
    assert fraction_left(r, "2027-01-01") == pytest.approx(365 / 730)
    assert fraction_left(r, "2028-01-01") == 0.0
    assert fraction_left(r, "2026-07-01", ended="2026-07-01") == 0.0
    assert fraction_left(r, "2026-06-30", ended="2026-07-01") > 0.5


def test_the_amount_scales_with_the_size_whichever_side_and_goes_with_the_status():
    left = remaining(sold(), "2027-01-01")
    assert left["initial"] == pytest.approx(2 * 1000 * 1.0 / 100) and left["amount"] == pytest.approx(20.0 * 365 / 730)
    assert left["lines"][0]["amount"] == pytest.approx(2 * 1000 * 0.6 / 100 * 365 / 730)
    assert remaining(sold(quantity=2.0), "2027-01-01")["amount"] == left["amount"]
    assert remaining(sold("called"), "2027-01-01")["amount"] == 0.0
    plain = Trade.from_term_sheet("P", preset("phoenix"), 1.0, "2026-01-01", "2026-01-01")
    assert remaining(plain, "2027-01-01") is None


def test_a_trade_keeps_its_reserves_through_json_and_old_trades_have_none():
    t = sold()
    again = Trade.from_json(t.to_json())
    assert again.reserves == t.reserves
    d = json.loads(t.to_json())
    d.pop("reserves")
    assert Trade(**d).reserves is None


# --- the desk ---
@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    d = Desk(store=tmp_path_factory.mktemp("res") / "optlab.sqlite")
    yield d
    d.close()


def wait(desk, job_id, timeout=300):
    t0 = time.perf_counter()
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        assert time.perf_counter() - t0 < timeout
        time.sleep(0.05)
    assert j["state"] == "done", j.get("error")
    return j["result"]


def body(sheet):
    d = sheet.to_dict(json_safe=True)
    t = d.pop("type")
    return {"type": t, "values": d}


@pytest.mark.slow
def test_a_booked_quote_freezes_its_lines_and_the_book_shows_what_is_left(desk):
    sheet = preset("phoenix", **{"general.notional": 1e6})
    q = wait(desk, desk.quote({"sheet": body(sheet), "options": {"precision": "quick"}})["job"])
    t = desk.create_trade(
        {
            "sheet": q["sheet"] and body(sheet),
            "coupon_rate": q["coupon_rate"],
            "quantity": -1.0,
            "tags": [f"quote:{q['quote_id']}"],
        }
    )
    assert t["reserves"]["total"] == pytest.approx(sum(x["cost"] for x in q["desk"]["lines"]))
    assert t["reserves"]["total"] == pytest.approx(q["desk"]["final_price"] - q["desk"]["base_price"], abs=1e-9)
    assert {x["key"] for x in t["reserves"]["lines"]} >= {"slv", "vol", "coupon"}
    bare = desk.create_trade({"sheet": body(sheet), "coupon_rate": 0.05, "quantity": -1.0})
    assert bare["reserves"] is None
    costless = wait(
        desk,
        desk.quote({"sheet": body(sheet), "options": {"precision": "quick", "costs": False, "control": False}})["job"],
    )
    assert (
        desk.create_trade(
            {"sheet": body(sheet), "coupon_rate": 0.05, "quantity": -1.0, "tags": [f"quote:{costless['quote_id']}"]}
        )["reserves"]
        is None
    )
    detail = desk.trade(t["trade_id"])
    assert (
        detail["reserve"]["initial"] == pytest.approx(1e6 * t["reserves"]["total"] / 100)
        and detail["reserve"]["fraction"] == 1.0
    )
    book = wait(desk, desk.price_book({"options": {"precision": "quick", "greeks": False}})["job"])
    rows = {p["name"]: p for p in book["positions"]}
    assert (
        rows[t["trade_id"]]["reserve"]["amount"] == pytest.approx(detail["reserve"]["amount"])
        and rows[bare["trade_id"]]["reserve"] is None
    )
    assert book["reserve"] == pytest.approx(detail["reserve"]["amount"]) and bare["trade_id"] in book["no_reserve"]
    assert book["value_net"] == pytest.approx(book["value"] - book["reserve"])
    assert book["groups"][0]["model"] == "lv"  # the book values at the base
    position = rows[t["trade_id"]]
    assert position["value"] == pytest.approx(
        -1e6 * q["desk"]["base_price"] / 100, rel=2e-3
    )  # the trade's base, not its client price
    html = book_report(desk)
    assert "Reserves left" in html and "none booked" in html
    updated = desk.update_trade(t["trade_id"], {"counterparty": "Desk B"})
    assert updated["reserves"] == t["reserves"]  # a new version keeps what was frozen


def test_the_trade_page_says_when_there_is_none(desk):
    t = desk.create_trade({"sheet": body(preset("phoenix")), "coupon_rate": 0.05, "quantity": -1.0})
    assert desk.trade(t["trade_id"])["reserve"] is None


# --- the replay ---
@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    return synthetic_lake(tmp_path_factory.mktemp("lake"))[0]


@pytest.mark.slow
def test_a_reserve_follows_a_trade_to_its_call_and_the_pnl_adds_up(lake):
    sheet = preset(
        "phoenix",
        **{"general.underlying": "SPX", "general.maturity_months": 24, "autocall.trigger": 0.9, "coupon.barrier": 0.6},
    )
    cfg = ReplayConfig(n_paths=2000, batch=2000, reval_every=20, greeks=False)
    trade, info = strike_trade(
        lake, "PHX", sheet, "2020-01-15", -1.0, cfg, coupon=0.05, costs=resolve(None, sheet).inputs()
    )
    assert trade.reserves is not None and info["desk"]["final_price"] - info["desk"]["base_price"] == pytest.approx(
        trade.reserves["total"], abs=1e-9
    )
    r = replay(lake, trade, cfg=cfg, inception=info)
    called = next(e for e in r.events if e.kind == "called")
    d = r.daily
    rec = r.reconciliation
    assert (
        rec["reserve_start"] == pytest.approx(trade.reserves["total"]) and rec["reserve_end"] == 0.0
    )  # per 100 of notional
    assert abs(rec["pnl_gap"]) < 1e-9 and abs(rec["cash_gap"]) < 1e-9
    before = d[pd.to_datetime(d["date"]).dt.date < called.date]
    assert (before["reserve"] > 0).all() and (
        before["reserve"].diff().dropna() <= 1e-12
    ).all()  # it falls until the call
    at_call = d[pd.to_datetime(d["date"]).dt.date >= called.date]
    assert (at_call["reserve"] == 0).all()
    released = float(d["release"].fillna(0.0).sum())
    assert released == pytest.approx(rec["reserve_start"]) and rec["reserve_released"] == pytest.approx(released)
    assert r.explain["sum"]["reserve"] == pytest.approx(released)
    # the same run without reserves has the same values, and a P&L smaller by what was released
    plain = replay(lake, Trade.from_json(json.dumps({**json.loads(trade.to_json()), "reserves": None})), cfg=cfg)
    assert plain.daily["value"].tolist() == d["value"].tolist()
    assert plain.reconciliation["total_pnl"] == pytest.approx(rec["total_pnl"] - released)
    out = result_json(r)
    assert out["reserves"]["total"] == trade.reserves["total"] and out["daily"][0]["reserve"] == rec["reserve_start"]
    json.dumps(out, default=str)
