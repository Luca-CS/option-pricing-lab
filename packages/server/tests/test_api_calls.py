"""API v1, the issuer's calls: the call dates to come, the review that prices each note going on and called, and the
call itself (the trader's, audited); a note that is not an issuer callable is refused."""

import pytest
from _api import api, preset_sheet


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-calls")) as a:
        yield a


@pytest.fixture(scope="module")
def trader(a):
    return a.client("trader")


def _book(trader, name, coupon, quantity=-1.0):
    sheet = preset_sheet(trader, name)
    sheet["values"]["issuer_call"] = {
        **sheet["values"].get("issuer_call", {}),
        "first_call_months": 3,
        "notice_days": 2,
    }
    r = trader.post("/api/v1/trading/trades", json={"sheet": sheet, "coupon_rate": coupon, "quantity": quantity})
    assert r.status_code == 201, r.text
    return r.json()["trade_id"]


def test_the_calls_to_come_the_review_and_the_call(a, trader):
    high = _book(trader, "callable_reverse_convertible", 0.25)  # far above the fair coupon: worth calling
    low = _book(trader, "callable_reverse_convertible", 0.0)  # no coupon: the issuer keeps it
    other = trader.post(
        "/api/v1/trading/trades",
        json={"sheet": preset_sheet(trader, "phoenix"), "coupon_rate": 0.05, "quantity": -1.0},
    ).json()["trade_id"]
    rows = {r["trade_id"]: r for r in trader.get("/api/v1/trading/calls").json()["calls"]}
    assert set(rows) >= {high, low} and other not in rows
    assert rows[high]["notice_date"] < rows[high]["call_date"] and rows[high]["call_price"] == 1.0

    r = trader.post("/api/v1/trading/calls/reviews", json={"trade_ids": [high, low], "options": {"n_paths": 4000}})
    assert r.status_code == 202, r.text
    got = {x["trade_id"]: x for x in a.wait(trader, r.json()["job_id"])["result"]["reviews"]}
    assert got[high]["decision"] == "call" and got[high]["gap"]["value"] > 0
    assert got[low]["decision"] == "keep"
    for x in got.values():
        assert x["going_on"]["value"] > 0 and x["called"]["value"] > 0

    viewer = a.client("viewer")
    assert viewer.post(f"/api/v1/trading/trades/{high}/call", json={}).status_code == 403
    wrong = trader.post(f"/api/v1/trading/trades/{high}/call", json={"call_date": "2099-01-01"})
    assert wrong.status_code == 422
    called = trader.post(f"/api/v1/trading/trades/{high}/call", json={"call_date": rows[high]["call_date"]})
    assert called.status_code == 200, called.text
    t = called.json()
    assert t["status"] == "called"
    assert t["product"]["issuer_call"]["amount"] == pytest.approx(-1.0 * rows[high]["notional"])
    assert trader.post(f"/api/v1/trading/trades/{other}/call", json={}).status_code == 409
    assert high not in {r["trade_id"] for r in trader.get("/api/v1/trading/calls").json()["calls"]}
    assert any(e["action"] == "call_trade" and e["target"] == high for e in a.state.audit.entries(200))
