"""API v1, hedging: instruments booked as trades (live or simulated), their marks, promote and discard, the roll of a
future, and the book valued with its hedges: a hedge's value is its closed form, and it never moves the notes' numbers."""

import datetime as dt

import pytest
from _api import api, preset_sheet

from optlab.structured.hedges.contracts import quarterly_expiries


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-hedging")) as a:
        d = a.state.desk  # the market in use with a real level (from a file, it has none): futures are in real points
        sid = str(d.ctx.snapshot_id).split("+")[0]
        d.market_store.put(d.market_store.get(sid).with_spot_scale(6000.0))
        yield a


@pytest.fixture(scope="module")
def trader(a):
    return a.client("trader")


def _valuation(trader) -> dt.date:
    return dt.date.fromisoformat(trader.get("/api/v1/market/context").json()["valuation"])


def _future(trader, **extra):
    expiry = quarterly_expiries(_valuation(trader), 1)[0]
    return {"type": "future", "name": "SPX", "expiry": expiry.isoformat(), "price": 6000.0, **extra}


def _irs(trader):
    v = _valuation(trader)
    return {
        "type": "irs",
        "ccy": "USD",
        "start": v.isoformat(),
        "end": (v + dt.timedelta(days=5 * 365)).isoformat(),
        "rate": 0.04,
    }


def test_a_mark_gives_the_value_the_fair_level_and_the_cost(trader):
    r = trader.post("/api/v1/trading/hedges/marks", json={"instrument": _irs(trader), "quantity": 1_000_000})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["home"] == "SPX" and m["currency"] == "USD" and m["origin"].startswith("government proxy")
    assert m["value"] == pytest.approx(1_000_000 * m["unit"]["value"])
    assert m["cost"] == pytest.approx(1_000_000 * m["unit"]["half_spread"]) and m["cost"] > 0
    par = dict(_irs(trader), rate=m["unit"]["fair"])
    again = trader.post("/api/v1/trading/hedges/marks", json={"instrument": par}).json()
    assert abs(again["unit"]["value"]) < 1e-12  # the fair rate is the par rate
    bad = trader.post("/api/v1/trading/hedges/marks", json={"instrument": {"type": "swaption"}})
    assert bad.status_code == 422 and "unknown instrument type" in bad.json()["detail"]


def test_a_hedge_is_a_trade_and_a_what_if_is_promoted_or_discarded(a, trader):
    r = trader.post(
        "/api/v1/trading/hedges", json={"instrument": _future(trader), "quantity": -2, "status": "simulated"}
    )
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["status"] == "simulated" and "hedge" in t["tags"] and t["product"]["kind"] == "instrument"
    assert t["summary"]["type"] == "future" and t["summary"]["label"].startswith("ES ")
    assert t["desk_side"] == "short" and t["side_check"] is None
    p = trader.post(f"/api/v1/trading/trades/{t['trade_id']}/promote")
    assert p.status_code == 200 and p.json()["status"] == "live" and p.json()["version"] == 2
    assert trader.post(f"/api/v1/trading/trades/{t['trade_id']}/promote").status_code == 409
    w = trader.post("/api/v1/trading/hedges", json={"instrument": _irs(trader), "quantity": 1e6, "status": "simulated"})
    tid = w.json()["trade_id"]
    assert trader.post(f"/api/v1/trading/trades/{tid}/discard").status_code == 200
    assert tid not in [x["trade_id"] for x in trader.get("/api/v1/trading/trades").json()["items"]]
    actions = [e["action"] for e in a.state.audit.entries(200)]
    assert "promote_trade" in actions and "discard_trade" in actions
    assert trader.post("/api/v1/trading/hedges", json={"instrument": _irs(trader), "quantity": 0}).status_code == 422


def test_a_future_is_rolled_onto_the_next_expiry(trader):
    t = trader.post("/api/v1/trading/hedges", json={"instrument": _future(trader), "quantity": 3}).json()
    r = trader.post(f"/api/v1/trading/trades/{t['trade_id']}/roll")
    assert r.status_code == 200, r.text
    closed, opened = r.json()["closed"], r.json()["opened"]
    assert closed["status"] == "closed" and closed["product"]["close"]["price"] > 0
    first = dt.date.fromisoformat(t["product"]["instrument"]["expiry"])
    assert opened["product"]["instrument"]["expiry"] == quarterly_expiries(first, 1)[0].isoformat()
    assert opened["quantity"] == 3 and opened["product"]["fees"] > 0 and f"rolled:{t['trade_id']}" in opened["tags"]
    assert trader.post(f"/api/v1/trading/trades/{t['trade_id']}/roll").status_code == 409


def test_the_book_takes_its_hedges_without_moving_its_notes(a, trader):
    body = {"sheet": preset_sheet(trader, "phoenix"), "coupon_rate": 0.05, "quantity": -2.0}
    assert trader.post("/api/v1/trading/trades", json=body).status_code == 201
    opts = {"options": {"precision": "quick", "greeks": False}}

    def book():
        r = trader.post("/api/v1/trading/book/valuations", json=opts)
        assert r.status_code == 202, r.text
        job = r.json()["job_id"]
        return a.wait(trader, job)["result"]

    before = book()
    hedge = trader.post("/api/v1/trading/hedges", json={"instrument": _irs(trader), "quantity": 1e6}).json()
    after = book()
    notes = {p["name"]: p for p in before["positions"]}
    for p in after["positions"]:
        if p["name"] in notes:
            assert p["value"] == notes[p["name"]]["value"]  # the same seeds and paths
    mine = next(p for p in after["positions"] if p["name"] == hedge["trade_id"])
    mark = trader.post("/api/v1/trading/hedges/marks", json={"instrument": _irs(trader), "quantity": 1e6}).json()
    assert mine["value"]["value"] == pytest.approx(mark["value"], rel=1e-9) and mine["value"]["se"] == 0.0


def test_a_vanilla_is_marked_on_the_desk_surface_and_a_listed_one_needs_the_chain(trader):
    v = _valuation(trader)
    otc = {"type": "vanilla", "name": "SPX", "expiry": (v + dt.timedelta(days=365)).isoformat(), "strike": 5700.0}
    put = trader.post("/api/v1/trading/hedges/marks", json={"instrument": {**otc, "kind": "put"}, "quantity": 2})
    assert put.status_code == 200, put.text
    m = put.json()
    assert m["unit"]["value"] > 0 and m["unit"]["fair"] == m["unit"]["value"]  # OTC: one unit per index point
    assert m["unit"]["half_spread"] > 0 and m["origin"].startswith("model") and m["value"] == 2 * m["unit"]["value"]
    call = trader.post("/api/v1/trading/hedges/marks", json={"instrument": {**otc, "kind": "call"}}).json()
    assert call["unit"]["value"] > m["unit"]["value"]  # 5700 is below the forward (6000 x the carry)
    listed = {**otc, "kind": "put", "listed": True}
    r = trader.post("/api/v1/trading/hedges", json={"instrument": listed, "quantity": 1})
    assert r.status_code == 422 and "not in the day's chain" in r.json()["detail"]
    priced = trader.post("/api/v1/trading/hedges", json={"instrument": {**listed, "price": 120.0}, "quantity": -1})
    assert priced.status_code == 201 and priced.json()["product"]["fill"] == 120.0 * 100
    assert trader.get("/api/v1/trading/hedges/listed", params={"name": "SPX"}).status_code == 404  # an empty lake


def test_an_otc_vanilla_is_filled_at_the_mid_plus_or_minus_the_vol_spread(trader):
    v = _valuation(trader)
    otc = {"type": "vanilla", "name": "SPX", "expiry": (v + dt.timedelta(days=200)).isoformat(), "strike": 6000.0}
    otc["kind"] = "call"
    m = trader.post("/api/v1/trading/hedges/marks", json={"instrument": otc}).json()["unit"]
    bought = trader.post("/api/v1/trading/hedges", json={"instrument": otc, "quantity": 5}).json()
    sold = trader.post("/api/v1/trading/hedges", json={"instrument": otc, "quantity": -5}).json()
    assert bought["product"]["instrument"]["price"] == pytest.approx(m["fair"] + m["half_spread"], rel=1e-9)
    assert sold["product"]["instrument"]["price"] == pytest.approx(m["fair"] - m["half_spread"], rel=1e-9)
