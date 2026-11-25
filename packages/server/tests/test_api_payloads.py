"""The described answers (`optlab.server.payloads`, api.md §10) are true: every route the document describes answers
what its model says, on a desk with a trade, a quote and a risk run. The front's types are generated from these
descriptions, so a description that drifts from the answer would type the front wrongly: this test fails first."""

import pytest
from _api import api, preset_sheet

from optlab.server import payloads

DESCRIBED = {  # the domain routes of session 2's screens, so far; each names its model in the document
    "/api/v1/market/context": "MarketContext",
    "/api/v1/market/snapshots": "SnapshotPage",
    "/api/v1/market/lake/coverage": "LakeCoverage",
    "/api/v1/market/universe": "Universe",
    "/api/v1/market/lake/quality": "LakeQuality",
    "/api/v1/structuring/presets": "PresetList",
    "/api/v1/structuring/quotes": "QuotePage",
    "/api/v1/trading/trades": "TradePage",
    "/api/v1/trading/events/upcoming": "Upcoming",
    "/api/v1/risk/runs": "RiskRunPage",
    "/api/v1/risk/pnl": "PnlHistory",
    "/api/v1/quant/engine": "EngineInfo",
    "/api/v1/admin/system": "SystemInfo",
    "/api/v1/admin/jobs": "JobRowPage",
}

# JSON answers still open in the document; this set only shrinks (a screen that reads one describes it)
OPEN = {
    "/api/v1/ready",
    "/api/v1/structuring/schema",  # a JSON Schema itself
    "/api/v1/trading/trades/{trade_id}",
    "/api/v1/trading/trades/{trade_id}/events",
    "/api/v1/trading/fixings",
    "/api/v1/trading/cost-defaults",
    "/api/v1/market/snapshots/{snapshot_id}/smile",
    "/api/v1/market/snapshots/{snapshot_id}/term",
    "/api/v1/market/other-underlyings",
    "/api/v1/market/lake/vol-indices/{name}",
    "/api/v1/market/lake/basket-quotes",
    "/api/v1/risk/runs/{run_id}",
    "/api/v1/risk/scenarios",
    "/api/v1/quant/model-risk",
    "/api/v1/quant/vollab/days",
    "/api/v1/quant/vollab/quotes",
    "/api/v1/quant/vollab/runs",
    "/api/v1/quant/replay/runs",
    "/api/v1/quant/replay/runs/{run_id}",
    "/api/v1/admin/network",
}
NOT_JSON = {"/api/v1/jobs/{job_id}/events"}  # Server-Sent Events


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-payloads")) as a:
        yield a


@pytest.fixture(scope="module")
def admin(a):
    """An admin on a desk holding a trade, a quote and a risk run: lists with items, not only empty ones."""
    c = a.client("admin")
    body = {"sheet": preset_sheet(c, "phoenix"), "coupon_rate": 0.06, "quantity": -1.0}
    assert c.post("/api/v1/trading/trades", json=body).status_code == 201
    quote = {"sheet": preset_sheet(c, "phoenix"), "options": {"precision": "quick"}}
    r = c.post("/api/v1/structuring/quotes", json=quote)
    assert r.status_code == 202, r.text
    c.quote = a.wait(c, r.json()["job_id"])  # type: ignore[attr-defined]
    r = c.post("/api/v1/risk/scenarios/runs", json={"options": {"precision": "quick"}, "grid": False})
    assert r.status_code == 202, r.text
    a.wait(c, r.json()["job_id"], timeout=900)
    return c


def _documented(a) -> dict[str, str]:
    """GET path -> the name of the model its 200 names in the document."""
    out = {}
    for path, item in a.app.openapi()["paths"].items():
        op = item.get("get")
        if op is None:
            continue
        ref = (
            op["responses"].get("200", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        )
        if ref:
            out[path] = ref.rsplit("/", 1)[-1]
    return out


def test_every_described_answer_is_true(a, admin):
    documented = _documented(a)
    for path, name in DESCRIBED.items():
        assert documented.get(path) == name, f"{path} does not name {name} in the document"
        r = admin.get(path)
        assert r.status_code == 200, (path, r.text)
        data = r.json()
        getattr(payloads, name).model_validate(data)  # raises on a missing field or a wrong type
        for key in DESCRIBED_WITH_ITEMS.get(path, ()):
            assert data[key], f"{path}: the test meant {key} to hold something"


DESCRIBED_WITH_ITEMS = {
    "/api/v1/market/snapshots": ("items",),
    "/api/v1/structuring/presets": ("presets",),
    "/api/v1/structuring/quotes": ("items",),
    "/api/v1/trading/trades": ("items",),
    "/api/v1/risk/runs": ("items",),
    "/api/v1/admin/system": ("workers",),
    "/api/v1/admin/jobs": ("items",),
}


def test_the_open_answers_only_shrink(a):
    """Every JSON GET route is described, or listed as still open; a route described since leaves the open list."""
    documented = _documented(a)
    gets = {
        path
        for path, item in a.app.openapi()["paths"].items()
        if "get" in item and "text/html" not in item["get"]["responses"].get("200", {}).get("content", {})
    }
    undescribed = {p for p in gets if p not in documented} - NOT_JSON
    assert undescribed == OPEN, (
        f"newly open: {sorted(undescribed - OPEN)}; described since: {sorted(OPEN - undescribed)}"
    )


def test_a_described_route_still_sends_its_own_dict(a, admin):
    """The description changes the document only: fields the model does not name still reach the client."""
    trade = admin.get("/api/v1/trading/trades").json()["items"][0]
    assert "product" in trade and "term_sheet" in trade["product"]
    ctx = admin.get("/api/v1/market/context").json()
    assert set(payloads.MarketContext.model_fields) <= set(ctx)


def test_a_quote_s_result_and_its_stored_copy_are_described(a, admin):
    doc = a.app.openapi()
    assert doc["x-job-results"]["quote"] == {"$ref": "#/components/schemas/QuoteResult"}
    result = admin.quote["result"]
    q = payloads.QuoteResult.model_validate(result)
    assert q.headline.kind == "coupon" and q.result.price.se and q.policy.class_
    path = "/api/v1/structuring/quotes/{quote_id}"
    assert _documented(a)[path] == "SavedQuote"
    saved = admin.get(path.format(quote_id=result["quote_id"]))
    assert saved.status_code == 200
    payloads.SavedQuote.model_validate(saved.json())


def test_the_sheet_form_is_described_and_its_problem_keeps_the_form(a, admin):
    op = a.app.openapi()["paths"]["/api/v1/structuring/termsheet/form"]["post"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TermSheetFormOut")
    ok = admin.post("/api/v1/structuring/termsheet/form", json={"type": "autocall", "values": {}})
    form = payloads.TermSheetFormOut.model_validate(ok.json())
    assert form.form.sections and all(f.path for s in form.form.sections for f in s.fields)
    bad = admin.post(
        "/api/v1/structuring/termsheet/form",
        json={"type": "autocall", "values": {"coupon": {"type": "conditional", "barrier": 5}}},
    )
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "coupon.barrier"
    payloads.SheetForm.model_validate(bad.json()["form"])


def test_a_booked_trade_is_described(a, admin):
    op = a.app.openapi()["paths"]["/api/v1/trading/trades"]["post"]
    assert op["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/Trade")
    body = {"sheet": preset_sheet(admin, "phoenix"), "coupon_rate": 0.05, "quantity": -1.0, "tags": ["e2e"]}
    r = admin.post("/api/v1/trading/trades", json=body)
    assert r.status_code == 201
    payloads.Trade.model_validate(r.json())
