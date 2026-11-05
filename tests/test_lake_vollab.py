"""The Vol Fitting Lab: fits on a chain with a known smile, errors against the bid-ask, runs replayed from their stored row."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest
from test_lake_quotes import DAY, curve_rows, synthetic_rows

from optlab.marketdata.lake import Lake
from optlab.marketdata.quotes import clean_day
from optlab.marketdata.vollab import DEFAULTS, fit_day, lab_days, list_runs, quote_table, reproduce, run_fit

SMALL = {"n_particles": 10_000, "n_paths": 10_000, "n_strikes_heston": 12, "targets": (0.1, 0.3, 0.7, 1.1)}
EXPIRIES = tuple(DAY + dt.timedelta(days=d) for d in (36, 110, 256, 400))


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    lake = Lake(tmp_path_factory.mktemp("lake"))
    lake.ingest("discount_curves", curve_rows(), "test", "derived")
    lake.ingest("option_quotes", synthetic_rows(EXPIRIES, half_spread=0.004), "archive", "observed")
    return lake


@pytest.fixture(scope="module")
def run(lake):
    return run_fit(lake, "SPX", DAY, "archive", settings=SMALL)


def test_lab_lists_days_per_name_and_source(lake):
    rows = lab_days(lake)
    assert rows == [{"underlying": "SPX", "source": "archive", "days": [str(DAY)], "first": str(DAY), "last": str(DAY), "n": 1,
                     "quality": ["ok"]}]


def test_quote_table_gives_bid_mid_ask_vols(lake):
    t = quote_table(lake, "SPX", DAY, "archive", targets=SMALL["targets"])
    s = t["slices"][0]
    assert t["origin"] == "observed" and len(t["slices"]) == 4
    assert (np.array(s["iv_bid"]) < np.array(s["iv"])).all() and (np.array(s["iv"]) < np.array(s["iv_ask"])).all()


def test_svi_fits_inside_the_spread_and_is_free_of_arbitrage(run):
    svi = run["fits"]["svi"]
    assert svi["overall"]["rmse_bp"] < 30 and svi["overall"]["within_spread"] > 0.5
    assert svi["arbitrage"]["butterfly_free"]


def test_models_rank_as_expected_and_report_their_cost(run):
    f = run["fits"]
    assert set(f) == {"svi", "ssvi", "heston", "slv"}
    assert f["svi"]["overall"]["rmse_bp"] <= f["ssvi"]["overall"]["rmse_bp"] + 1
    assert f["svi"]["overall"]["rmse_bp"] < f["heston"]["overall"]["rmse_bp"]
    assert all(f[m]["seconds"] > 0 for m in f)
    assert f["slv"]["by_maturity"][0]["mc_stderr_bp"] > 0


def test_each_model_is_a_stored_run_with_its_inputs(lake, run):
    rows = list_runs(lake, "SPX")
    assert {r["model"] for r in rows} == {"svi", "ssvi", "heston", "slv"}
    assert {r["source"] for r in rows} == {"archive"} and {r["origin"] for r in rows} == {"derived"}
    assert all(r["day"] == str(DAY) for r in rows)
    stored = lake.read("calibration_runs", where="model = 'svi'").iloc[0]
    fit = json.loads(stored["fit"])
    assert fit["quotes_hash"] == run["quotes_hash"] and fit["settings"]["n_particles"] == 10_000


def test_running_again_adds_nothing(lake, run):
    n = len(lake.versions("calibration_runs"))
    again = run_fit(lake, "SPX", DAY, "archive", models=("svi",), settings=SMALL)
    assert again["fits"]["svi"]["calibration_id"] == run["fits"]["svi"]["calibration_id"]
    assert len(lake.read("calibration_runs")) == 4
    assert len(lake.versions("calibration_runs")) >= n


@pytest.mark.parametrize("model", ["svi", "ssvi", "heston", "slv"])
def test_a_fit_is_reproducible_from_its_run(lake, run, model):
    out = reproduce(lake, run["fits"][model]["calibration_id"])
    assert out["same_quotes"] and out["largest_parameter_gap"] == pytest.approx(0.0, abs=1e-10)
    assert out["new_rmse_bp"] == pytest.approx(out["stored_rmse_bp"], abs=1e-6)


def test_unknown_run_and_day(lake):
    with pytest.raises(KeyError):
        reproduce(lake, "svi-000")
    with pytest.raises(KeyError):
        run_fit(lake, "SPX", dt.date(2021, 1, 4))


def test_settings_change_the_run_id_and_the_defaults_are_complete(lake):
    cleaned = clean_day(lake, "SPX", DAY, "archive")
    a = fit_day(cleaned, ("svi",), SMALL)
    assert a["maturities"] and set(a["fits"]) == {"svi"}
    assert {"targets", "mix", "seed"} <= set(DEFAULTS)
    assert isinstance(pd.Timestamp(DAY).date(), dt.date)
