"""R7: the risk routes of the desk, their stored runs, the HTTP server and the report."""

import json
import time
import urllib.request

import pytest

from optlab.app.api import Desk
from optlab.app.report import risk_report
from optlab.app.server import serve_in_thread

QUICK = {"precision": "quick"}


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    d = Desk(store=tmp_path_factory.mktemp("desk") / "desk.sqlite")
    sheet = next(p["sheet"] for p in d.presets()["presets"] if p["name"] == "phoenix")
    d.create_trade(
        {
            "sheet": {"type": sheet["type"], "values": {k: v for k, v in sheet.items() if k != "type"}},
            "coupon_rate": 0.06,
            "quantity": -1.0,
        }
    )
    from optlab.app.risk_api import risk_routes

    risk_routes(d)  # the server does this when it starts
    yield d
    d.jobs.shutdown()


def wait(desk, job_id, timeout=900):
    t0 = time.perf_counter()
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        assert time.perf_counter() - t0 < timeout, "job did not finish"
        time.sleep(0.2)
    assert j["state"] == "done", j.get("error")
    return j["result"]


def test_greeks_run_is_stored_and_read_back(desk):
    res = wait(
        desk,
        desk.risk.run(
            {"options": {**QUICK, "kxt": "none", "curves": False, "sticky_strike": False, "realistic": False}}
        )["job"],
    )
    assert res["run_id"].startswith("risk-") and res["book"]["delta|SPX|"][1] > 0
    assert res["positions"][0]["greeks"]["vega|SPX|"][0] != 0 and res["meta"]["snapshot_id"]
    back = desk.risk.run_get(res["run_id"])
    assert back["book"] == res["book"]
    assert any(r["run_id"] == res["run_id"] for r in desk.risk.runs()["runs"])
    json.dumps(back)


def test_ladder_and_scenarios_routes(desk):
    lad = wait(
        desk,
        desk.risk.ladder(
            {
                "options": QUICK,
                "ladder": {"rules": ["sticky_moneyness", "sticky_strike"], "tenors": False, "points": [-0.1, 0.0, 0.1]},
            }
        )["job"],
    )
    assert lad["book"]["pnl@sticky_moneyness@+0.0000"] == [0.0, 0.0] and "pnl@sticky_strike@-0.1000" in lad["book"]
    got = desk.risk.scenarios()
    assert got["starting"] and len(got["scenarios"]) == 6
    edited = got["scenarios"][1:3]
    assert desk.risk.put_scenarios({"scenarios": edited, "signed_by": "test", "note": "two only"})["version"] == 1
    with pytest.raises(Exception):
        desk.risk.put_scenarios({"scenarios": edited, "signed_by": ""})
    run = wait(desk, desk.risk.run_scenarios({"options": QUICK, "grid": False})["job"])
    assert [s["name"] for s in run["scenarios"]] == ["Grind down", "Vol spike"] and run["signed_by"] == "test"
    html = risk_report(desk.risk)
    assert "<h2>Greeks</h2>" in html and "Spot ladder" in html and "Grind down" in html


def test_http_routes(desk):
    httpd, base = serve_in_thread(desk)
    try:
        with urllib.request.urlopen(base + "/api/risk/scenarios") as r:
            assert json.loads(r.read())["version"] == 1
        with urllib.request.urlopen(base + "/api/pnl") as r:
            assert "note" in json.loads(r.read())
        with urllib.request.urlopen(base + "/report/risk") as r:
            assert b"Risk and P&amp;L" in r.read()
        with urllib.request.urlopen(base + "/js/views/risk.js") as r:
            assert b"export async function render" in r.read()
        with urllib.request.urlopen(base + "/js/views/pnl.js") as r:
            assert b"waterfall" in r.read()
    finally:
        httpd.shutdown()
