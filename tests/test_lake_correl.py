"""Correlation: matrices stay semi-definite, two-asset prices match Margrabe and the existing bivariate reference."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from scipy.special import ndtr

from optlab.marketdata.derive.correl import (CorrelationSkew, apply_premium, build_premium, build_realized, closes_wide,
                                             copula_expectation, correlation_at, forward_quantile, implied_pair_rho,
                                             nearest_correlation, pair_horizon, pair_payoff, pair_rows, premium_series,
                                             realised_matrix, rolling_pair)
from optlab.marketdata.lake import Lake
from optlab.structured.multiasset import correlation_root
from optlab.svi import SVISurface
from _structured_refs import worst_put


def correlated_closes(rho=0.6, n=800, names=("SPX", "NDX", "RUT"), seed=3):
    rng = np.random.default_rng(seed)
    c = np.full((len(names), len(names)), rho) + (1 - rho) * np.eye(len(names))
    r = rng.standard_normal((n, len(names))) @ np.linalg.cholesky(c).T * 0.01
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=pd.bdate_range("2017-01-02", periods=n), columns=list(names))


class FlatSurface:
    def __init__(self, vol):
        self.vol = vol

    def theta(self, T):
        return self.vol**2 * T

    def w(self, k, T):
        return np.full_like(np.asarray(k, dtype=float), self.vol**2 * T)


def test_rolling_correlation_and_horizons():
    closes = correlated_closes()
    s = rolling_pair(closes, "SPX", "NDX", 250)
    assert s.iloc[-1] == pytest.approx(0.6, abs=0.12)
    assert pair_horizon("SPX", "NDX") == 1 and pair_horizon("SPX", "SX5E") == 3


def test_realised_matrices_are_semi_definite_and_usable_by_the_simulator():
    closes = correlated_closes(names=("SPX", "NDX", "RUT", "SX5E", "AAPL"))
    for window in (60, 120, 250):
        m = realised_matrix(closes, list(closes.columns), window)
        assert np.linalg.eigvalsh(m).min() >= -1e-10
        assert np.allclose(np.diag(m), 1.0)
        correlation_root(m)


def test_nearest_correlation_repairs_an_impossible_matrix():
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    assert np.linalg.eigvalsh(bad).min() < 0
    fixed = nearest_correlation(bad)
    assert np.linalg.eigvalsh(fixed).min() >= -1e-10 and np.allclose(np.diag(fixed), 1.0)
    correlation_root(fixed)
    good = np.array([[1.0, 0.3], [0.3, 1.0]])
    assert nearest_correlation(good) == pytest.approx(good)


def test_premium_keeps_semi_definiteness_and_moves_toward_one():
    m = np.array([[1.0, 0.2, -0.3], [0.2, 1.0, 0.1], [-0.3, 0.1, 1.0]])
    out = apply_premium(m, 0.25)
    assert out[0, 1] == pytest.approx(0.2 + 0.25 * 0.8)
    assert np.linalg.eigvalsh(out).min() >= 0.75 * np.linalg.eigvalsh(m).min() - 1e-12
    with pytest.raises(ValueError):
        apply_premium(m, 1.5)


def test_premium_series_reads_the_implied_gap_and_is_clipped():
    closes = correlated_closes(names=("AAPL", "AMZN", "GOOGL"))
    implied = pd.Series(0.8, index=closes.index)
    lam = premium_series(implied, closes, ["AAPL", "AMZN", "GOOGL"], 63)
    assert 0.0 < lam.iloc[-1] < 1.0 and (lam >= 0).all()
    assert (premium_series(implied * 0 - 0.5, closes, ["AAPL", "AMZN", "GOOGL"], 63) == 0.0).all()


def test_marginal_quantile_is_the_lognormal_one_on_a_flat_surface():
    vol, T = 0.2, 1.0
    q = forward_quantile(FlatSurface(vol), T)
    s = vol * np.sqrt(T)
    for u in (0.05, 0.5, 0.95):
        z = float(np.interp(u, ndtr(np.linspace(-6, 6, 4001)), np.linspace(-6, 6, 4001)))
        assert q(u) == pytest.approx(np.exp(-0.5 * s * s + s * z), rel=2e-3)


def margrabe(vol_a, vol_b, rho, T):
    s = np.sqrt((vol_a**2 + vol_b**2 - 2 * rho * vol_a * vol_b) * T)
    d1 = 0.5 * s
    return ndtr(d1) - ndtr(-d1)


@pytest.mark.parametrize("rho", [-0.5, 0.0, 0.6, 0.9])
def test_exchange_option_matches_margrabe(rho):
    va, vb, T = 0.25, 0.18, 1.0
    qa, qb = forward_quantile(FlatSurface(va), T), forward_quantile(FlatSurface(vb), T)
    got = copula_expectation(lambda a, b: np.maximum(a - b, 0.0), qa, qb, rho, 300)
    assert got == pytest.approx(margrabe(va, vb, rho, T), abs=2e-4)


@pytest.mark.parametrize("rho,K", [(0.0, 1.0), (0.5, 0.9), (0.8, 1.1)])
def test_worst_of_put_matches_the_existing_bivariate_reference(rho, K):
    vols, T = (0.2, 0.3), 1.0
    qa, qb = forward_quantile(FlatSurface(vols[0]), T), forward_quantile(FlatSurface(vols[1]), T)
    got = copula_expectation(lambda a, b: np.maximum(K - np.minimum(a, b), 0.0), qa, qb, rho, 300)
    assert got == pytest.approx(worst_put(K, T, vols, rho, 0.0, (0.0, 0.0)), abs=3e-4)


def test_cvc_and_pvp_rise_with_correlation_and_invert():
    q = forward_quantile(FlatSurface(0.2), 1.0)
    for kind, m in (("cvc", 1.1), ("pvp", 0.9)):
        low = copula_expectation(pair_payoff(kind, m), q, q, 0.2)
        high = copula_expectation(pair_payoff(kind, m), q, q, 0.8)
        assert high > low > 0
        assert implied_pair_rho(copula_expectation(pair_payoff(kind, m), q, q, 0.55), kind, m, q, q) == pytest.approx(0.55, abs=1e-3)
    with pytest.raises(ValueError):
        pair_payoff("best", 1.0)


def test_pseudo_quotes_have_a_correlation_skew_with_pvp_above_cvc():
    params = [[0.01, 0.1, -0.4, 0.0, 0.2], [0.04, 0.15, -0.4, 0.0, 0.3]]
    surf = SVISurface([0.25, 1.0], params)
    rows = pd.DataFrame(pair_rows(dt.date(2022, 7, 1), "SPX", "NDX", surf, surf, 0.7, tenors=(1.0,)))
    atm = rows[rows["moneyness"] == 1.0].set_index("kind")["rho"]
    assert atm["pvp"] > atm["cvc"]
    down = rows[rows["kind"] == "pvp"].sort_values("moneyness")["rho"].to_numpy()
    assert down[0] > down[2] == down[-1]
    assert (rows["price_se"] < 0.05).all() and (rows["price"] >= 0).all()
    assert CorrelationSkew().rho("cvc", 2.0, 0.0) == pytest.approx(-0.3)


def test_tables_load_and_correlation_at_reads_only_the_past(tmp_path):
    lake = Lake(tmp_path)
    closes = correlated_closes(names=("SPX", "NDX", "AAPL", "AMZN", "GOOGL", "GS", "IBM"))
    long = closes.reset_index(names="obs_date").melt("obs_date", var_name="instrument", value_name="close")
    lake.ingest("prices_eod", long, "yahoo", "observed")
    cor = pd.DataFrame({"index_name": "COR3M", "tenor": 0.25, "obs_date": closes.index, "correlation": 0.9})
    lake.ingest("correl_implied", cor, "cboe", "observed")
    assert build_realized(lake, ["SPX", "NDX"])[0]["n_new"] > 0
    assert build_realized(lake, ["SPX", "NDX"])[0]["n_new"] == 0
    build_premium(lake)
    assert set(lake.read("correl_implied", where="index_name = 'LAMBDA.COR3M'")["origin"]) == {"derived"}
    day = closes.index[400]
    m, meta = correlation_at(lake, ["SPX", "NDX"], day)
    assert 0 < meta["lambda"] < 1 and np.allclose(m, m.T)
    assert closes_wide(lake, ["SPX"], day).index.max() == day
    realised, meta0 = correlation_at(lake, ["SPX", "NDX"], day, implied=False)
    assert meta0["lambda"] == 0.0 and m[0, 1] > realised[0, 1]
    # adding later prices changes nothing at that date
    later = long.assign(close=long["close"] * 2)
    later = later[later["obs_date"] > day]
    lake2 = Lake(tmp_path / "two")
    lake2.ingest("prices_eod", long[long["obs_date"] <= day], "yahoo", "observed")
    same, _ = correlation_at(lake2, ["SPX", "NDX"], day, implied=False)
    assert same == pytest.approx(realised, abs=1e-14)
    assert len(later) > 0
