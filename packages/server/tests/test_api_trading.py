"""API v1, trading router: trades (versions, soft delete, filters, cursor pages), events, fixings, the cost table, the
reports, and the audit line of every write."""

import pytest
from _api import api, preset_sheet, raw_errors


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-trading")) as a:
        yield a


@pytest.fixture(scope="module")
def trader(a):
    return a.client("trader")


def _book(trader, **extra):
    body = {"sheet": preset_sheet(trader, "phoenix"), "coupon_rate": 0.05, "quantity": 2.0, **extra}
    r = trader.post("/api/v1/trading/trades", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _audit(a, action, target=None):
    return [
        e for e in a.state.audit.entries(200) if e["action"] == action and (target is None or e["target"] == target)
    ]


def test_a_trade_s_life(a, trader):
    t = _book(trader, counterparty="Desk A")
    tid = t["trade_id"]
    assert t["version"] == 1 and t["summary"]["coupon_rate"] == 0.05
    got = trader.get(f"/api/v1/trading/trades/{tid}").json()
    assert got["trade"]["trade_id"] == tid and got["trade"]["counterparty"] == "Desk A" and len(got["versions"]) == 1
    r = trader.put(f"/api/v1/trading/trades/{tid}", json={"quantity": 3.0, "counterparty": "Desk B"})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["quantity"] == 3.0
    assert trader.put(f"/api/v1/trading/trades/{tid}", json={"status": "gone"}).status_code == 422
    assert trader.delete(f"/api/v1/trading/trades/{tid}").status_code == 200
    assert tid not in [x["trade_id"] for x in trader.get("/api/v1/trading/trades").json()["items"]]
    assert trader.post(f"/api/v1/trading/trades/{tid}/restore").status_code == 200
    assert tid in [x["trade_id"] for x in trader.get("/api/v1/trading/trades").json()["items"]]
    (created,) = _audit(a, "create_trade")
    (updated,) = _audit(a, "update_trade", tid)
    assert created["after_hash"] and updated["before_hash"] and updated["before_hash"] != updated["after_hash"]
    assert all(e["user_id"] for e in (created, updated))


def test_events_and_unknown_trades(trader):
    tid = _book(trader)["trade_id"]
    ev = trader.get(f"/api/v1/trading/trades/{tid}/events")
    assert ev.status_code == 200 and ev.json()["trade_id"] == tid and ev.json()["upcoming"]
    for method, path in (
        ("GET", "/api/v1/trading/trades/T-none"),
        ("GET", "/api/v1/trading/trades/T-none/events"),
        ("PUT", "/api/v1/trading/trades/T-none"),
        ("DELETE", "/api/v1/trading/trades/T-none"),
    ):
        r = trader.request(method, path, json={"quantity": 1.0} if method == "PUT" else None)
        assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json", (method, path)


def test_filters_and_cursor_pages(trader):
    for _i in range(3):
        _book(trader, book="paged")
    first = trader.get("/api/v1/trading/trades", params={"book": "paged", "limit": 2}).json()
    assert len(first["items"]) == 2 and first["next"]
    rest = trader.get("/api/v1/trading/trades", params={"book": "paged", "limit": 2, "cursor": first["next"]}).json()
    assert len(rest["items"]) == 1 and rest["next"] is None
    ids = [t["trade_id"] for t in first["items"] + rest["items"]]
    assert len(set(ids)) == 3
    assert trader.get("/api/v1/trading/trades", params={"limit": 500}).status_code == 422


def test_fixings_and_their_audit(a, trader):
    r = trader.post(
        "/api/v1/trading/fixings", json={"underlying": "SX5E", "levels": {"2026-10-26": 5100.0}, "real_spot": 5000.0}
    )
    assert r.status_code == 200 and r.json()["stored"] == 1
    got = trader.get("/api/v1/trading/fixings", params={"underlying": "SX5E"}).json()
    assert got["fixings"]["SX5E"]["2026-10-26"] == pytest.approx(102.0)
    r = trader.post("/api/v1/trading/fixings", json={"underlying": "SX5E", "levels": {"2026-12-01": 100.0}})
    assert r.status_code == 422  # a fixing in the future
    r = trader.post("/api/v1/trading/fixings", json={"underlying": "SX5E", "text": "nothing here"})
    assert r.status_code == 422 and "no line" in r.json()["detail"]
    assert trader.delete("/api/v1/trading/fixings/SX5E/2026-10-26").status_code == 200
    assert trader.delete("/api/v1/trading/fixings/SX5E/not-a-date").status_code == 422
    assert "SX5E" not in trader.get("/api/v1/trading/fixings").json()["fixings"]
    assert _audit(a, "add_fixings", "SX5E") and _audit(a, "delete_fixing", "SX5E/2026-10-26")


def test_cost_defaults_versions(a, trader):
    table = trader.get("/api/v1/trading/cost-defaults").json()
    assert table["groups"] and "versions" in table
    r = trader.put("/api/v1/trading/cost-defaults", json={"groups": table["groups"]})
    assert r.status_code == 200, r.text
    assert len(trader.get("/api/v1/trading/cost-defaults").json()["versions"]) == len(table["versions"]) + 1
    assert _audit(a, "put_cost_defaults")
    viewer = a.client("viewer")
    assert viewer.put("/api/v1/trading/cost-defaults", json={"groups": table["groups"]}).status_code == 403


def test_reports_and_upcoming(trader):
    _book(trader)
    up = trader.get("/api/v1/trading/events/upcoming", params={"days": 400}).json()
    assert "events" in up
    for path in ("/api/v1/trading/book/report", "/api/v1/trading/desk-snapshot"):
        r = trader.get(path)
        assert r.status_code == 200 and r.text.lower().startswith("<!doctype html>"), path
        assert "script-src" not in r.headers["content-security-policy"]


@pytest.mark.slow
def test_value_a_trade_and_the_book(a, trader):
    tid = _book(trader)["trade_id"]
    quick = {"options": {"precision": "quick"}}
    r = trader.post(f"/api/v1/trading/trades/{tid}/valuations", json=quick)
    assert r.status_code == 202
    env = a.wait(trader, r.json()["job_id"])
    v = env["result"]
    assert v["snapshot_id"] == env["market"]["snapshot_id"] and v["trade_id"] == tid and v["unit_price"]["se"] > 0
    assert v["value"]["se"] > 0 and raw_errors(env) == []
    r = trader.post("/api/v1/trading/book/valuations", json=quick)
    env = a.wait(trader, r.json()["job_id"])
    book = env["result"]
    assert book["positions"] and any(p["name"] == tid for p in book["positions"]) and book["value"]["se"] > 0
    assert env["method"]["groups"] >= 1 and raw_errors(env) == []
