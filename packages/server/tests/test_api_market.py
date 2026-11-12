"""API v1, market router: the market in use, snapshots, smile and term, the other underlyings of worst-ofs, and the
lake's routes on an empty lake (their content on a synthetic lake is in test_lake_desk.py)."""

import pytest
from _api import api


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-market")) as a:
        yield a


@pytest.fixture(scope="module")
def trader(a):
    return a.client("trader")


def test_context_smile_and_term(trader):
    c = trader.get("/api/v1/market/context").json()
    assert c["spot"] == pytest.approx(100.0) and c["snapshot_id"] and c["maturities"]
    s = trader.get("/api/v1/market/snapshots/in-use/smile", params={"T": 1.0}).json()
    assert len(s["moneyness"]) == len(s["iv"]) == 61
    t = trader.get("/api/v1/market/snapshots/in-use/term").json()
    assert t["discount"][0] > t["discount"][-1]
    assert trader.get("/api/v1/market/snapshots/in-use/smile", params={"T": 0}).status_code == 422
    assert trader.get("/api/v1/market/snapshots/..%2f..%2fx/term").status_code in (404, 422)
    assert trader.get("/api/v1/market/snapshots/none-such/term").status_code == 404


def test_switching_the_market_in_use_is_audited(a, trader):
    snaps = trader.get("/api/v1/market/snapshots").json()["items"]
    assert snaps and any(s["active"] for s in snaps)
    current = next(s["snapshot_id"] for s in snaps if s["active"])
    r = trader.put("/api/v1/market/in-use", json={"snapshot_id": current})
    assert r.status_code == 200, r.text
    assert trader.put("/api/v1/market/in-use", json={"snapshot_id": "none-such"}).status_code == 404
    assert trader.put("/api/v1/market/in-use", json={"snapshot_id": "../x"}).status_code == 422
    (entry, *_) = [e for e in a.state.audit.entries(20) if e["action"] == "use_market"]
    assert entry["target"] == current and entry["before_hash"]
    assert a.client("viewer").put("/api/v1/market/in-use", json={"snapshot_id": current}).status_code == 403


def test_other_underlyings(trader):
    before = trader.get("/api/v1/market/other-underlyings").json()
    body = {"names": {"SX5E": {"vol": 0.2, "dividend": 0.03}}, "corr": {"SPX|SX5E": 0.7}}
    r = trader.put("/api/v1/market/other-underlyings", json=body)
    assert r.status_code == 200, r.text
    after = trader.get("/api/v1/market/other-underlyings").json()
    assert after != before and "SX5E" in str(after)
    assert trader.put("/api/v1/market/other-underlyings", json={"corr": {"SPX|SX5E": 2.0}}).status_code == 422


def test_the_lake_routes_on_an_empty_lake(trader):
    for path in ("/api/v1/market/lake/coverage", "/api/v1/market/universe", "/api/v1/market/lake/quality"):
        r = trader.get(path)
        assert r.status_code < 500, (path, r.text)
    assert trader.get("/api/v1/market/lake/vol-indices/VIX").status_code < 500
    assert trader.get("/api/v1/market/lake/vol-indices/x;drop table").status_code in (404, 422)
    r = trader.post("/api/v1/market/snapshots/from-lake", json={"underlying": "SPX", "date": "not a date"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "date"
