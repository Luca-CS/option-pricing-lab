"""API v1, quant router: model risk, the engine, the vol lab and the replay lab on an empty lake (their content on a
synthetic lake is in test_lake_desk.py), and the ids that must never become paths."""

import json
from pathlib import Path

import pytest
from _api import api

CALIBRATION = Path(__file__).resolve().parents[3] / "results" / "spx_calibration.json"


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    folder = tmp_path_factory.mktemp("api-quant")
    results = folder / "results"
    results.mkdir()
    (results / "replay_test.json").write_text(json.dumps({"one": {"trade_id": "R-1", "events": []}}), encoding="utf-8")
    (folder / "secret.json").write_text(json.dumps({"one": {"secret": True}}), encoding="utf-8")
    with api(folder, results=results, calibration=CALIBRATION) as a:
        yield a


@pytest.fixture(scope="module")
def quant(a):
    return a.client("quant")


def test_model_risk_and_engine(quant):
    r = quant.get("/api/v1/quant/model-risk").json()
    assert "exotics" in r and r["errors"]
    # the calibrated parameters of the market in use: SVI per maturity, Heston, Feller, the arbitrage checks, the SLV mix
    params = r["params"]
    assert set(params["heston"]) == {"v0", "kappa", "theta", "xi", "rho"}
    assert len(params["svi"]["params"]) == len(params["svi"]["maturities"]) and len(params["svi"]["params"][0]) == 5
    assert params["feller"] > 0 and params["slv"]["mix"] is not None and "butterfly_free" in params["arbitrage"]
    e = quant.get("/api/v1/quant/engine").json()
    assert e and e["runs_in"] in ("worker", "api")


def test_the_engine_shown_is_the_one_the_jobs_run_on(a, quant):
    """In the containers the api has no GPU and the worker has it: the engine shown is the live worker's."""
    from optlab.server.jobs import Workers

    leases = Workers(a.state.desk.db)
    for w in leases.alive():
        leases.stop(w["worker_id"])
    gpu = {"engine": "auto", "compiled": True, "cuda_build": True, "devices": [{"name": "a card"}], "resolved": "gpu"}
    assert leases.register("w-gpu", "elsewhere", 1, "worker", "auto", 2)
    leases.beat("w-gpu", {"engine": gpu})
    try:
        e = quant.get("/api/v1/quant/engine").json()
        assert e["resolved"] == "gpu" and e["runs_in"] == "worker" and e["devices"][0]["name"] == "a card"
    finally:
        leases.stop("w-gpu")
    assert quant.get("/api/v1/quant/engine").json()["runs_in"] in ("worker", "api")


def test_vol_lab_on_an_empty_lake(a, quant):
    assert quant.get("/api/v1/quant/vollab/days").status_code == 200
    assert quant.get("/api/v1/quant/vollab/runs").status_code == 200
    r = quant.get("/api/v1/quant/vollab/quotes", params={"day": "2025-01-06"})
    assert r.status_code == 404
    assert quant.get("/api/v1/quant/vollab/quotes", params={"day": "x"}).status_code == 422
    assert a.client("viewer").get("/api/v1/quant/vollab/quotes", params={"day": "2025-01-06"}).status_code == 403
    r = quant.post("/api/v1/quant/vollab/runs/..%2fx/reproduce")
    assert r.status_code in (404, 422)


def test_replay_ids_name_stored_runs_never_files(quant):
    runs = quant.get("/api/v1/quant/replay/runs").json()
    assert [r["id"] for r in runs["runs"]] == ["replay_test/one"]
    assert quant.get("/api/v1/quant/replay/runs/replay_test/one").json()["trade_id"] == "R-1"
    for rid in ("replay_test/two", "none-such", "secret/one"):  # well formed, not stored
        r = quant.get(f"/api/v1/quant/replay/runs/{rid}")
        assert r.status_code == 404 and "true" not in r.text, rid  # the 404 names the id, never the file
    for rid in ("../secret/one", "..%2fsecret/one", "replay_test/../x", "a/b/c"):  # not an id at all
        r = quant.get(f"/api/v1/quant/replay/runs/{rid}")
        assert r.status_code in (404, 422) and "true" not in r.text, rid


def test_the_bounds_of_a_replay(quant):
    r = quant.post("/api/v1/quant/replay/runs", json={"strike": "2020-01-15", "n_paths": 10_000_000})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "n_paths"
    r = quant.post("/api/v1/quant/replay/runs", json={"strike": "2020-01-15", "model": "heston"})
    assert r.status_code == 422


def test_a_fit_takes_its_settings_within_bounds(quant):
    base = {"day": "2025-01-06", "models": ["svi"]}
    for bad in (
        {"moneyness": [1.2, 0.8]},
        {"moneyness": [0.1, 1.2]},
        {"maturities": [0.0]},
        {"maturities": [20.0]},
        {"weights": "vega"},
        {"max_rel_spread": 0.0},
        {"n_strikes_heston": 2},
    ):
        r = quant.post("/api/v1/quant/vollab/fits", json={**base, **bad})
        assert r.status_code == 422, (bad, r.text)
    good = {**base, "maturities": [0.25, 1.0], "weights": "equal", "moneyness": [0.8, 1.2], "max_rel_spread": 0.4}
    r = quant.post("/api/v1/quant/vollab/fits", json=good)
    assert r.status_code == 202, r.text  # accepted: the empty lake fails the job itself, not the request
