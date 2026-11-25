"""The defaults the OpenAPI document states are the ones the desk applies. `body(model)` passes only the keys a client
gave and the desk fills the others itself, so a default written in a schema can drift from the desk's: these are the
fields where the desk's default is its own (four of them once said something else than what happened)."""

import pytest
from _api import SCRIPT_EXAMPLE, api, preset_sheet

from optlab.server.schemas import BookPricingOptions, FixingsFromLakeIn, MarketFromLakeIn, ScriptPriceIn, TradeIn


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-defaults")) as a:
        yield a


def _default(model, field):
    return model.model_fields[field].default


def _submitted(a, client, path, body) -> dict:
    """The parameters of the job a request made, as the worker reads them (the job is cancelled at once)."""
    r = client.post(path, json=body)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    params = a.state.desk.jobs.payload(job_id)[1]
    client.post(f"/api/v1/jobs/{job_id}/cancel")
    return params


def test_a_trade_booked_without_a_quantity(a):
    trader = a.client("trader")
    r = trader.post("/api/v1/trading/trades", json={"sheet": preset_sheet(trader, "phoenix"), "coupon_rate": 0.05})
    assert r.status_code == 201, r.text
    assert r.json()["quantity"] == _default(TradeIn, "quantity") == 1.0


def test_a_script_priced_without_its_number_of_paths(a):
    params = _submitted(a, a.client("structurer"), "/api/v1/structuring/script/price", {"script": SCRIPT_EXAMPLE})
    assert params["n_paths"] == _default(ScriptPriceIn, "n_paths")
    assert params["model"] == _default(ScriptPriceIn, "model") and params["seed"] == _default(ScriptPriceIn, "seed")


def test_a_market_from_the_lake_with_its_date_alone(a):
    params = _submitted(a, a.client("trader"), "/api/v1/market/snapshots/from-lake", {"date": "2024-03-15"})
    assert params["leverage"] is _default(MarketFromLakeIn, "leverage") is True
    assert params["t_max"] == _default(MarketFromLakeIn, "t_max")
    assert params["underlying"] == _default(MarketFromLakeIn, "underlying")


def test_fixings_from_the_lake_are_those_of_the_market_in_use(a):
    assert _default(FixingsFromLakeIn, "underlying") is None
    r = a.client("trader").post("/api/v1/trading/fixings/from-lake", json={})
    assert r.status_code == 404 and a.state.desk.ctx.underlying in r.json()["detail"]  # an empty lake: no close of it


def test_the_book_has_its_greeks_unless_told_otherwise():
    """What `run_book` does with no `greeks` key is checked where the book is valued (`test_api_trading.py`)."""
    assert _default(BookPricingOptions, "greeks") is True
