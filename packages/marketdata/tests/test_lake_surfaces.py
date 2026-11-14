"""Surfaces: the model-free integrals against closed cases, the reconstruction's inputs recovered, no static arbitrage."""

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd
import pytest
from _paths import SPX_CALIBRATION

from optlab.core.svi import SVISurface, svi_arbitrage_report
from optlab.marketdata.derive.surfaces import (
    TERM_INDICES,
    ShapePrior,
    build_reconstructed,
    extrapolate_thetas,
    fit_shape,
    index_panel,
    model_free_skewness,
    model_free_variance,
    reconstruct,
    reconstruct_on,
    rho_from_skew,
    solve_thetas,
    ssvi_w,
    static_prior,
    surface_at,
    synthetic_surface,
)
from optlab.marketdata.lake import Lake

PRIOR = ShapePrior(-0.7, 1.0, 0.3, 0.05, source="test")
LEVELS: dict[str, Any] = {"VIX9D": 16.0, "VIX": 18.0, "VIX3M": 20.0, "VIX6M": 21.0, "VIX1Y": 22.0}


def test_flat_smile_variance_is_the_total_variance():
    # eta -> 0 flattens the slice at sigma^2 T = theta, where the model-free variance is theta itself
    thetas = np.array([0.01, 0.04, 0.2])
    assert model_free_variance(thetas, 0.0, 1e-8, 0.3) == pytest.approx(thetas, rel=1e-3)


def test_skew_raises_the_model_free_variance_above_at_the_money():
    assert (model_free_variance(np.array([0.04]), -0.7, 1.0, 0.3) > 0.04).all()


def test_flat_smile_skewness_is_the_lognormal_one():
    # log-return of a lognormal: symmetric around its mean, so its skewness is zero
    assert model_free_skewness(0.04, 0.0, 1e-8, 0.3) == pytest.approx(0.0, abs=3e-3)


def test_negative_rho_gives_negative_skewness_and_skew_index_inverts():
    theta = 0.2**2 * 30 / 365
    assert model_free_skewness(theta, -0.7, 1.0, 0.3) < model_free_skewness(theta, -0.3, 1.0, 0.3) < 0.01
    target = model_free_skewness(theta, -0.55, 1.0, 0.3)
    assert rho_from_skew(100 - 10 * target, theta, PRIOR) == pytest.approx(-0.55, abs=2e-3)


def test_solved_slices_reprice_the_indices():
    Ts = np.array(list(TERM_INDICES.values()))
    vols = np.array(list(LEVELS.values())) / 100
    thetas = solve_thetas(vols, Ts, PRIOR.rho, PRIOR.eta, PRIOR.gamma)
    assert model_free_variance(thetas, PRIOR.rho, PRIOR.eta, PRIOR.gamma) == pytest.approx(vols**2 * Ts, rel=1e-5)
    assert (thetas < vols**2 * Ts).all()  # the at-the-money variance is below the skew-inclusive level
    assert (np.diff(thetas) > 0).all()


def test_reconstruction_has_no_static_arbitrage_and_says_where_it_extrapolates():
    rec = reconstruct(LEVELS, PRIOR)
    report = svi_arbitrage_report(rec.surface)
    assert report["butterfly_free"] and report["calendar_free"]
    assert rec.extrapolated_from == 1.0 and rec.surface.maturities[-1] == 5.0
    assert rec.inputs["levels"]["VIX1Y"] == 22.0
    atm_1y = float(rec.surface.iv(0.0, 1.0))
    assert 0.15 < atm_1y < 0.22
    assert rec.surface.iv(-0.2, 0.25) > rec.surface.iv(0.0, 0.25) > rec.surface.iv(0.1, 0.25)


def test_reconstruction_stays_arbitrage_free_in_a_crisis_and_when_inverted():
    crisis = {"VIX9D": 90.0, "VIX": 80.0, "VIX3M": 60.0, "VIX6M": 50.0, "VIX1Y": 42.0}
    report = svi_arbitrage_report(reconstruct(crisis, PRIOR).surface)
    assert report["butterfly_free"] and report["calendar_free"]


def test_extrapolation_goes_to_the_long_run_variance():
    th = extrapolate_thetas(0.05, 0.08, 1.0, PRIOR, [2.0, 50.0, 51.0])
    assert th[2] - th[1] == pytest.approx(PRIOR.v_inf, abs=1e-6)
    assert (th[0] - 0.05) > PRIOR.v_inf  # still above, coming from a higher forward variance


def test_scale_multiplies_the_level_and_other_names_use_their_index():
    base = reconstruct(LEVELS, PRIOR)
    twice = reconstruct(LEVELS, PRIOR, scale=2.0)
    assert float(twice.surface.iv(0.0, 0.25)) == pytest.approx(2 * float(base.surface.iv(0.0, 0.25)), rel=0.08)
    row = pd.Series({**LEVELS, "VXN": 27.0, "VXAPL": 36.0, "SKEW": 130.0})
    ndx = reconstruct_on(row, "NDX", PRIOR)
    assert ndx.inputs["scale"] == pytest.approx(1.5) and ndx.rho == PRIOR.rho
    aapl = reconstruct_on(row, "AAPL", PRIOR)
    assert aapl.rho == pytest.approx(0.6 * PRIOR.rho)
    with pytest.raises(ValueError, match="no RVX"):
        reconstruct_on(row, "RUT", PRIOR)


def test_static_prior_is_the_versioned_calibration_shape():
    p = static_prior(SPX_CALIBRATION)
    assert -0.99 < p.rho < -0.3 and p.eta * (1 + abs(p.rho)) <= 2.0 + 1e-9 and 0 < p.v_inf < 0.2
    assert "2026-10-27" in p.source


def test_fit_shape_recovers_a_power_law_surface():
    Ts = np.array([0.1, 0.25, 0.5, 1.0, 2.0])
    th = 0.04 * Ts
    rec = reconstruct(LEVELS, PRIOR)
    assert rec is not None
    k = np.linspace(-0.3, 0.2, 5)
    from optlab.core.svi import ssvi_slice_to_svi
    from optlab.marketdata.derive.surfaces import _phi

    surf = SVISurface(Ts, np.array([ssvi_slice_to_svi(t, t * _phi(t, 0.9, 0.35), -0.6) for t in th]))
    fit = fit_shape(surf)
    assert fit.rho == pytest.approx(-0.6, abs=0.01) and fit.eta == pytest.approx(0.9, abs=0.03)
    assert np.asarray(surf.w(k, 1.0)) == pytest.approx(ssvi_w(k, 0.04, -0.6, 0.9, 0.35), abs=1e-9)


def test_synthetic_surface_labels_its_inputs():
    rec = synthetic_surface(0.25, PRIOR)
    assert rec.inputs["realised_vol"] == 0.25 and rec.inputs["premium"] == 1.1


def vol_lake(tmp_path):
    lake = Lake(tmp_path)
    days = pd.bdate_range("2020-03-02", "2020-03-13")
    rows = [
        pd.DataFrame({"index_name": n, "obs_date": days, "close": lvl}) for n, lvl in {**LEVELS, "SKEW": 130.0}.items()
    ]
    lake.ingest("vol_indices", pd.concat(rows), "cboe", "observed")
    return lake, days


def test_history_loads_and_surface_at_never_reads_a_later_date(tmp_path):
    lake, days = vol_lake(tmp_path)
    assert build_reconstructed(lake, ["SPX"], "2020-03-02", "2020-03-13", PRIOR)[0]["n_new"] == len(days)
    assert build_reconstructed(lake, ["SPX"], "2020-03-02", "2020-03-13", PRIOR)[0]["n_new"] == 0
    got = surface_at(lake, "SPX", dt.date(2020, 3, 8))  # a Sunday
    assert got.layer == "reconstructed" and got.origin == "reconstructed" and got.obs_date == dt.date(2020, 3, 6)
    assert got.extrapolated_from == 1.0 and json.loads(got.meta["inputs"])["levels"]["VIX"] == 18.0
    with pytest.raises(KeyError):
        surface_at(lake, "SPX", dt.date(2020, 3, 1))
    with pytest.raises(KeyError):
        surface_at(lake, "SPX", dt.date(2020, 4, 1))


def test_an_observed_fit_of_the_day_takes_over_only_when_it_reaches_far_enough(tmp_path):
    lake, _ = vol_lake(tmp_path)
    build_reconstructed(lake, ["SPX"], "2020-03-02", "2020-03-13", PRIOR)
    rec = reconstruct(LEVELS, PRIOR)
    base: dict[str, Any] = {
        "underlying": "SPX",
        "layer": "observed",
        "surface": json.dumps(rec.surface.to_dict()),
        "inputs": "{}",
        "fit": "{}",
    }
    rows = pd.DataFrame(
        [
            {**base, "obs_date": "2020-03-05", "t_max_observed": 2.0, "extrapolated_from": 2.0},
            {**base, "obs_date": "2020-03-06", "t_max_observed": 0.2, "extrapolated_from": 0.2},
        ]
    )
    lake.ingest("vol_surfaces", rows, "svi_on_test", "observed")
    assert surface_at(lake, "SPX", dt.date(2020, 3, 5)).layer == "observed"
    assert (
        surface_at(lake, "SPX", dt.date(2020, 3, 6)).layer == "reconstructed"
    )  # a short chain does not make a surface
    assert surface_at(lake, "SPX", dt.date(2020, 3, 9)).layer == "reconstructed"  # an old fit is not reused


def test_index_panel_fills_only_from_the_past(tmp_path):
    lake = Lake(tmp_path)
    rows = pd.DataFrame({"index_name": "VIX", "obs_date": ["2020-03-02", "2020-03-04"], "close": [10.0, 30.0]})
    other = pd.DataFrame(
        {"index_name": "VIX3M", "obs_date": ["2020-03-02", "2020-03-03", "2020-03-04"], "close": [1.0, 2.0, 3.0]}
    )
    lake.ingest("vol_indices", pd.concat([rows, other]), "cboe", "observed")
    panel = index_panel(lake)
    assert panel.loc["2020-03-03", "VIX"] == 10.0


def test_the_slope_falls_with_the_level_and_stays_free_of_butterfly_arbitrage():
    from optlab.marketdata.derive.surfaces import ShapePrior

    prior = ShapePrior(-0.6, 1.2, 0.45, 0.03, level_power=0.12, level_ref=0.12)
    assert prior.eta_at(0.10) == prior.eta_at(0.12) == pytest.approx(1.2)  # below the reference, nothing changes
    assert prior.eta_at(0.60) == pytest.approx(1.2 * 5.0**-0.12)
    assert prior.eta_at(0.60) < prior.eta_at(0.30) < prior.eta_at(0.15)
    tight = ShapePrior(-0.9, 3.0, 0.45, 0.03)
    assert tight.eta_at(0.2) == pytest.approx(2.0 / 1.9)


def test_at_a_high_level_the_slope_gives_back_at_the_money_level():
    """The index fixes the variance: less skew leaves more of it at the money."""
    from optlab.marketdata.derive.surfaces import ShapePrior, reconstruct

    levels = {"VIX9D": 79.0, "VIX": 62.0, "VIX3M": 57.0, "VIX6M": 47.0, "VIX1Y": 36.0}
    calm = ShapePrior(-0.6, 1.2, 0.45, 0.03)
    moved = ShapePrior(-0.6, 1.2, 0.45, 0.03, level_power=0.12, level_ref=0.12)
    a, b = reconstruct(levels, calm).surface, reconstruct(levels, moved).surface
    assert b.theta(1 / 12) > 1.05 * a.theta(1 / 12)
    low = {k: v / 6 for k, v in levels.items()}
    assert reconstruct(low, moved).surface.theta(0.25) == pytest.approx(
        reconstruct(low, calm).surface.theta(0.25), rel=2e-3
    )


def test_the_panel_stops_at_the_last_vix(tmp_path):
    from optlab.marketdata.derive.surfaces import index_panel
    from optlab.marketdata.lake import Lake

    lake = Lake(tmp_path)
    days = pd.bdate_range("2026-11-02", periods=3)
    rows = [
        pd.DataFrame({"index_name": "RVX", "obs_date": days, "close": [24.0, 23.0, 20.0]}),
        pd.DataFrame({"index_name": "VIX", "obs_date": days[:2], "close": [21.0, 20.0]}),
    ]
    lake.ingest("vol_indices", pd.concat(rows), "cboe", "observed")
    panel = index_panel(lake)
    assert panel.index.max() == days[1]  # the RVX of the last day has no VIX to be compared with yet
    assert panel.loc[days[1], "RVX"] == 23.0
