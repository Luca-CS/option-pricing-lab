"""API v1, structuring router: presets, schema, the term-sheet form and its field errors, quotes, the script lab."""

import pytest
from _api import SCRIPT_EXAMPLE as EXAMPLE
from _api import api, preset_sheet, raw_errors


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-structuring")) as a:
        yield a


@pytest.fixture(scope="module")
def structurer(a):
    return a.client("structurer")


def test_presets_schema_and_a_valid_form(structurer):
    p = structurer.get("/api/v1/structuring/presets").json()
    assert {"autocall", "bullet"} <= set(p["masks"]) and any(x["name"] == "phoenix" for x in p["presets"])
    s = structurer.get("/api/v1/structuring/schema").json()
    assert s and isinstance(s, dict)
    r = structurer.post("/api/v1/structuring/termsheet/form", json=preset_sheet(structurer, "phoenix"))
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["error"] is None and f["sheet"] and f["form"]["sections"] and f["bounds"]


def test_a_field_in_error_is_a_problem_that_keeps_the_form(structurer):
    sheet = preset_sheet(structurer, "phoenix")
    sheet["values"]["coupon"]["barrier"] = -0.2
    r = structurer.post("/api/v1/structuring/termsheet/form", json=sheet)
    assert r.status_code == 422 and r.headers["content-type"] == "application/problem+json"
    p = r.json()
    assert p["errors"][0]["field"] == "coupon.barrier" and p["detail"] == p["errors"][0]["message"]
    shown = [x["path"] for sec in p["form"]["sections"] for x in sec["fields"]]
    assert "coupon.barrier" in shown and "coupon.memory" in shown and p["bounds"]
    r = structurer.post("/api/v1/structuring/termsheet/form", json={"type": "nope"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "type"
    r = structurer.post("/api/v1/structuring/termsheet/form", json={"type": "autocall", "values": {"x": 1}})
    assert r.status_code == 422  # a section is an object of fields


def test_bounds_on_what_costs(structurer):
    sheet = preset_sheet(structurer, "phoenix")
    r = structurer.post("/api/v1/structuring/quotes", json={"sheet": sheet, "options": {"n_paths": 50_000_000}})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "options.n_paths"
    r = structurer.post("/api/v1/structuring/script/price", json={"script": EXAMPLE, "n_paths": 10})
    assert r.status_code == 422


def test_script_check_and_price(a, structurer):
    ok = structurer.post("/api/v1/structuring/script/check", json={"script": EXAMPLE}).json()
    assert ok["ok"] and list(ok["underlyings"]) == ["SPX"]
    bad = structurer.post(
        "/api/v1/structuring/script/check", json={"script": "underlying SPX\nevent 2027-10-27\n  pay (SPX as x\nend\n"}
    ).json()
    assert not bad["ok"] and bad["line"] == 3
    r = structurer.post("/api/v1/structuring/script/price", json={"script": EXAMPLE, "model": "lv", "n_paths": 4_000})
    assert r.status_code == 202, r.text
    env = a.wait(structurer, r.json()["job_id"])
    price = env["result"]["price"]
    assert 50 < price["value"] < 130 and price["se"] > 0 and raw_errors(env) == []
    assert env["method"] == {"name": "monte_carlo", "model": "lv", "n_paths": 4000, "seed": 2026}
    assert env["market"]["snapshot_id"] == a.state.desk.ctx.snapshot_id and env["market"]["as_of"]
    wide = "underlying SPX\nevent t=1 pay t=1\n  pay variance(SPX, t=0, t=1, n=10000000) as redemption\nend\n"
    got = structurer.post("/api/v1/structuring/script/check", json={"script": wide}).json()
    assert not got["ok"] and "at most" in got["message"] and got["line"] == 3
    assert structurer.post("/api/v1/structuring/script/price", json={"script": wide}).status_code == 422  # no job
    viewer = a.client("viewer")
    assert viewer.post("/api/v1/structuring/script/check", json={"script": EXAMPLE}).status_code == 200
    assert viewer.post("/api/v1/structuring/script/price", json={"script": EXAMPLE}).status_code == 403


def test_unknown_quote(structurer):
    assert structurer.get("/api/v1/structuring/quotes/none-such").status_code == 404
    assert structurer.get("/api/v1/structuring/quotes/none-such/report").status_code == 404
    assert structurer.get("/api/v1/structuring/quotes").json() == {"items": [], "next": None}


@pytest.mark.slow
def test_a_quote_end_to_end(a, structurer):
    sheet = preset_sheet(structurer, "phoenix")
    r = structurer.post("/api/v1/structuring/quotes", json={"sheet": sheet, "options": {"precision": "quick"}})
    assert r.status_code == 202
    env = a.wait(structurer, r.json()["job_id"])
    q = env["result"]
    assert q["headline"]["kind"] == "coupon" and 0 < q["headline"]["value"] < 30 and q["headline"]["se"] > 0
    assert q["coupon_rate"]["se"] > 0 and q["result"]["price"]["se"] > 0 and raw_errors(env) == []
    assert env["method"]["name"] == q["policy"]["method"] and env["market"]["snapshot_id"] == q["snapshot_id"]
    listed = structurer.get("/api/v1/structuring/quotes").json()["items"]
    assert listed[0]["quote_id"] == q["quote_id"]
    saved = structurer.get(f"/api/v1/structuring/quotes/{q['quote_id']}").json()
    assert saved["result"]["headline"] == q["headline"] and saved["result"]["coupon_rate"] == q["coupon_rate"]
    rep = structurer.get(f"/api/v1/structuring/quotes/{q['quote_id']}/report")
    assert rep.status_code == 200 and "<svg" in rep.text and "script-src" not in rep.headers["content-security-policy"]
    assert any(e["action"] == "create_quote" for e in a.state.audit.entries(20))


def test_a_variance_swap_is_quoted_on_its_strike_and_booked_with_it(a, structurer):
    sheet = preset_sheet(structurer, "variance_swap")
    sheet["values"]["general"]["maturity_months"] = 6
    r = structurer.post("/api/v1/structuring/quotes", json={"sheet": sheet, "options": {"precision": "quick"}})
    assert r.status_code == 202, r.text
    q = a.wait(structurer, r.json()["job_id"])["result"]
    head = q["headline"]
    assert head["kind"] == "strike" and head["unit"] == "vol" and 5 < head["value"] < 80 and head["se"] > 0
    assert q["reference"]["unit"] == "vol" and abs(q["reference"]["strike"] - head["value"]) < 3
    rate = q["coupon_rate"]["value"]  # the solved variance strike, booked as the sheet's strike in vol
    trader = a.client("trader")
    t = trader.post("/api/v1/trading/trades", json={"sheet": sheet, "coupon_rate": rate, "quantity": -1.0})
    assert t.status_code in (200, 201), t.text
    summary = t.json()["summary"]
    assert summary["swap"] == "variance" and summary["strike"] == pytest.approx(rate**0.5)
    booked = t.json()["product"]["term_sheet"]
    assert booked["variance"]["strike"] == pytest.approx(rate**0.5) and booked["wrapper"]["solve"] == "upfront"
    bad = trader.post("/api/v1/trading/trades", json={"sheet": sheet, "coupon_rate": -0.01})
    assert bad.status_code == 422


def test_the_sheet_types_the_api_takes_are_the_library_s_masks():
    from typing import get_args

    from optlab.server.schemas import TermSheetIn
    from optlab.structured.schema import MASKS

    assert set(get_args(TermSheetIn.model_fields["type"].annotation)) == set(MASKS)
