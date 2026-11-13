"""API v1, risk router: the scenario library (signed by the authenticated user, never by the body), stored runs, the
explained P&L and the report. The computations behind the jobs are in test_risk_app.py and optlab.risk's tests."""

import pytest
from _api import api, preset_sheet, raw_errors


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-risk")) as a:
        yield a


@pytest.fixture(scope="module")
def risk(a):
    return a.client("risk")


def test_scenarios_are_signed_by_the_caller(a, risk):
    got = risk.get("/api/v1/risk/scenarios").json()
    assert got["starting"] and len(got["scenarios"]) == 6
    edited = got["scenarios"][1:3]
    r = risk.put("/api/v1/risk/scenarios", json={"scenarios": edited, "note": "two only"})
    assert r.status_code == 200 and r.json()["version"] == 1, r.text
    now = risk.get("/api/v1/risk/scenarios").json()
    assert now["version"] == 1 and now["signed_by"] == risk.user_name and len(now["scenarios"]) == 2
    forged = risk.put("/api/v1/risk/scenarios", json={"scenarios": edited, "signed_by": "someone else"})
    assert forged.status_code == 422  # the signature is the session's user, the body cannot carry one
    assert risk.put("/api/v1/risk/scenarios", json={"scenarios": []}).status_code == 422
    too_many = {"scenarios": edited * 30}
    assert risk.put("/api/v1/risk/scenarios", json=too_many).status_code == 422
    assert a.client("trader").put("/api/v1/risk/scenarios", json={"scenarios": edited}).status_code == 403
    assert any(e["action"] == "put_scenarios" for e in a.state.audit.entries(20))


def test_runs_pnl_and_report(risk):
    assert risk.get("/api/v1/risk/runs").json()["items"] == []
    assert risk.get("/api/v1/risk/runs/none-such").status_code == 404
    pnl = risk.get("/api/v1/risk/pnl")
    assert pnl.status_code == 200 and "note" in pnl.json()
    rep = risk.get("/api/v1/risk/report")
    assert rep.status_code == 200 and "Risk and P&amp;L" in rep.text


def test_bounds_on_what_costs(risk):
    r = risk.post("/api/v1/risk/var", json={"days": 100_000})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "days"
    r = risk.post("/api/v1/risk/ladder", json={"ladder": {"points": [0.01] * 100}})
    assert r.status_code == 422
    r = risk.post("/api/v1/risk/eod-runs", json={"from": "2025-01-06", "to": "nope"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "to"


@pytest.mark.slow
def test_a_greeks_run_through_the_api(a, risk):
    trader = a.client("trader")
    body = {"sheet": preset_sheet(trader, "phoenix"), "coupon_rate": 0.06, "quantity": -1.0}
    assert trader.post("/api/v1/trading/trades", json=body).status_code == 201
    opts = {"precision": "quick", "kxt": "none", "curves": False, "sticky_strike": False, "realistic": False}
    r = risk.post("/api/v1/risk/greeks", json={"options": opts})
    assert r.status_code == 202
    env = a.wait(risk, r.json()["job_id"], timeout=900)
    res = env["result"]
    assert res["run_id"].startswith("risk-") and res["book"]["delta|SPX|"]["se"] > 0
    assert env["method"]["name"] == "full_revaluation" and raw_errors(env) == []
    assert risk.get(f"/api/v1/risk/runs/{res['run_id']}").json()["book"] == res["book"]
