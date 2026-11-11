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
    e = quant.get("/api/v1/quant/engine").json()
    assert e and isinstance(e, dict)


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
