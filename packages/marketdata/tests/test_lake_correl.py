"""Correlation: matrices stay semi-definite, the copula matches Margrabe and the bivariate reference, CvC and PvP spreads
match exact lognormal paths and give back their correlation."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from _lake_fixture import synthetic_lake
from _structured_refs import worst_put
from scipy.special import ndtr

from optlab.core.blackscholes import black_price
from optlab.core.correlation import nearest_correlation
from optlab.core.svi import SVISurface
from optlab.marketdata.derive.correl import (
    apply_premium,
    build_basket_quotes,
    build_premium,
    build_realized,
    closes_wide,
    correlation_at,
    pair_horizon,
    premium_series,
    realised_matrix,
    rolling_pair,
)
from optlab.marketdata.lake import Lake
from optlab.structured.localcorr import (
    CorrelationSkew,
    average_correlation,
    basket_paths,
    basket_rows,
    copula_expectation,
    forward_quantile,
    member_vols,
    read_correlation,
    skewed_matrix,
    spread_payoff,
    spread_quote,
)
from optlab.structured.multiasset import correlation_root


def correlated_closes(rho=0.6, n=800, names=("SPX", "NDX", "RUT"), seed=3):
    rng = np.random.default_rng(seed)
    c = np.full((len(names), len(names)), rho) + (1 - rho) * np.eye(len(names))
    r = rng.standard_normal((n, len(names))) @ np.linalg.cholesky(c).T * 0.01
    return pd.DataFrame(
        100 * np.exp(np.cumsum(r, axis=0)), index=pd.bdate_range("2017-01-02", periods=n), columns=list(names)
    )


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


def constant(rho, n):
    return np.full((n, n), rho) + (1 - rho) * np.eye(n)


def flat_quote(vols, rho, kind, strike, T=1.0, forwards=None, n_pairs=200_000):
    surfaces = [FlatSurface(v) for v in vols]
    f = np.ones(len(vols)) if forwards is None else np.asarray(forwards)
    m = constant(rho, len(vols))
    x = basket_paths([forward_quantile(s, T) for s in surfaces], f, m, n_pairs)
    return spread_quote(surfaces, f, x, m, kind, strike, T), x, f


def exact_spread(vols, rho, kind, strike, T=1.0, forwards=None, n_pairs=1_000_000, seed=77):
    """The same spread on exact lognormal paths with other random numbers: price and standard error, in % of notional."""
    v = np.asarray(vols)
    f = np.ones(len(v)) if forwards is None else np.asarray(forwards)
    z = np.random.default_rng(seed).standard_normal((n_pairs, len(v))) @ np.linalg.cholesky(constant(rho, len(v))).T
    paths = [f * np.exp(sign * v * np.sqrt(T) * z - 0.5 * v * v * T) for sign in (1, -1)]
    pairs = 0.5 * (spread_payoff(kind, strike, paths[0]) + spread_payoff(kind, strike, paths[1]))
    return 100 * pairs.mean(), 100 * pairs.std(ddof=1) / np.sqrt(n_pairs)


@pytest.mark.parametrize(
    "vols,rho,kind,strike",
    [
        ((0.25, 0.35), 0.6, "cvc", 1.1),
        ((0.25, 0.35), 0.6, "pvp", 0.9),
        ((0.25, 0.28, 0.30, 0.35, 0.40), 0.5, "cvc", 1.0),
        ((0.25, 0.28, 0.30, 0.35, 0.40), 0.5, "pvp", 0.9),
    ],
)
def test_spread_matches_exact_lognormal_paths(vols, rho, kind, strike):
    got, _, _ = flat_quote(vols, rho, kind, strike)
    ref, se = exact_spread(vols, rho, kind, strike)
    assert abs(got["price"] - ref) < 4 * np.hypot(got["price_se"], se)


def test_spread_on_two_names_matches_the_tensor_quadrature():
    vols, rho, K, T = (0.25, 0.35), 0.6, 1.1, 1.0
    qa, qb = forward_quantile(FlatSurface(vols[0]), T), forward_quantile(FlatSurface(vols[1]), T)
    ref = copula_expectation(
        lambda a, b: spread_payoff("cvc", K, np.stack([a, b], axis=-1).reshape(-1, 2)).reshape(a.shape),
        qa,
        qb,
        rho,
        300,
    )
    got, _, _ = flat_quote(vols, rho, "cvc", K)
    assert abs(got["price"] - 100 * ref) < 4 * got["price_se"] + 2e-3


def test_members_are_black_prices_and_the_basket_is_their_sum_with_the_spread():
    vols, K, T = (0.25, 0.35), 0.9, 0.5
    f = np.array([1.01, 0.99])
    got, _, _ = flat_quote(vols, 0.6, "pvp", K, T, f)
    assert got["members_price"] == pytest.approx(
        100 * np.mean(black_price(f, K, T, np.array(vols), 1.0, "put")), abs=1e-10
    )
    assert got["basket_price"] == pytest.approx(got["members_price"] + got["price"], abs=1e-12)


@pytest.mark.parametrize("strike", [0.9, 1.0, 1.1])
def test_cvc_equals_pvp_on_a_strike_by_put_call_parity(strike):
    x = np.random.default_rng(1).lognormal(-0.02, 0.2, (5000, 3))
    assert spread_payoff("cvc", strike, x) == pytest.approx(spread_payoff("pvp", strike, x), abs=1e-12)
    with pytest.raises(ValueError):
        spread_payoff("best", strike, x)


def test_identical_names_at_correlation_one_leave_nothing():
    got, _, _ = flat_quote((0.3, 0.3, 0.3), 1.0, "cvc", 1.0, n_pairs=20_000)
    assert got["price"] == pytest.approx(0.0, abs=1e-6)


def test_the_spread_is_never_positive_and_rises_with_correlation():
    prices = [flat_quote((0.25, 0.35), r, "cvc", 1.1, n_pairs=100_000)[0]["price"] for r in (0.0, 0.5, 0.9)]
    assert prices[0] < prices[1] < prices[2] < 0


@pytest.mark.parametrize("vols,rho", [((0.25, 0.35), 0.6), ((0.25, 0.28, 0.30, 0.35, 0.40), 0.5)])
@pytest.mark.parametrize("kind,strike", [("cvc", 1.0), ("cvc", 1.1), ("pvp", 1.0), ("pvp", 0.9)])
def test_the_direct_reading_gives_back_the_correlation_without_a_smile(vols, rho, kind, strike):
    got, _, _ = flat_quote(vols, rho, kind, strike)
    assert got["rho"] == pytest.approx(rho, abs=1e-12)
    assert got["implied_rho"] == pytest.approx(rho, abs=0.01)  # the basket is not quite lognormal


def test_reading_uses_the_shares_of_the_members():
    vols, f, T, K, rho = np.array([0.2, 0.4]), np.array([1.2, 0.8]), 1.0, 1.0, 0.5
    a = f / f.sum()
    s_b = np.sqrt(a[0] ** 2 * 0.04 + a[1] ** 2 * 0.16 + 2 * rho * a[0] * a[1] * 0.08)
    price = float(black_price(f.mean(), K, T, s_b, 1.0, "call"))
    got, vol = read_correlation(price, "cvc", K, T, f, vols)
    assert vol == pytest.approx(s_b, abs=1e-9)
    assert got == pytest.approx(rho, abs=1e-8)


def test_average_correlation_weights_the_pairs_like_the_basket():
    m = np.array([[1.0, 0.8, 0.2], [0.8, 1.0, 0.4], [0.2, 0.4, 1.0]])
    assert average_correlation(constant(0.3, 3), [1, 1, 1], [0.1, 0.2, 0.3]) == pytest.approx(0.3)
    assert 0.2 < average_correlation(m, [1, 1, 1], [0.2, 0.2, 0.2]) < 0.8


def test_member_volatility_is_read_at_the_one_strike_of_the_structure():
    params = [[0.01, 0.1, -0.4, 0.0, 0.2], [0.04, 0.15, -0.4, 0.0, 0.3]]
    surf = SVISurface([0.25, 1.0], params)
    got = member_vols([surf, surf], [1.0, 1.1], 1.0, 1.0)
    assert got[1] > got[0]  # the same strike is further down for the name with the higher forward, on a negative skew
    assert got[0] == pytest.approx(np.sqrt(surf.w(np.array([0.0]), 1.0)[0]))


def test_rows_on_a_smile_have_one_correlation_per_strike_and_cvc_equal_to_pvp():
    params = [[0.01, 0.1, -0.4, 0.0, 0.2], [0.04, 0.15, -0.4, 0.0, 0.3]]
    surf = SVISurface([0.25, 1.0], params)
    rows = pd.DataFrame(
        basket_rows(
            dt.date(2022, 7, 1),
            ("SPX", "NDX"),
            [surf, surf],
            lambda T: [1.0, 1.0],
            constant(0.7, 2),
            tenors=(1.0,),
            n_pairs=20_000,
        )
    )
    assert rows["basket"].unique().tolist() == ["SPX+NDX"]
    assert sorted(rows[rows["kind"] == "cvc"]["strike"]) == [1.0, 1.1]
    assert sorted(rows[rows["kind"] == "pvp"]["strike"]) == [0.9, 1.0]
    atm = rows[rows["strike"] == 1.0].set_index("kind")
    assert atm.loc["cvc", "price"] == pytest.approx(atm.loc["pvp", "price"], abs=1e-12)
    assert atm.loc["cvc", "rho"] == atm.loc["pvp", "rho"] == pytest.approx(0.7)
    assert rows.set_index("strike")["rho"].loc[0.9] == pytest.approx(0.75)
    assert rows[rows["kind"] == "cvc"].set_index("strike")["rho"].loc[1.1] == pytest.approx(0.675)
    assert (rows["price"] < 0).all() and (rows["price_se"] < 0.05).all()
    assert (rows["basket_price"] > 0).all() and rows["implied_rho"].notna().all()


@pytest.mark.slow
def test_quotes_in_the_lake_are_synthetic_and_reload_to_nothing(tmp_path):
    lake, days, _ = synthetic_lake(tmp_path, "2019-01-01", "2019-12-31")
    day = days[-1]
    assert build_basket_quotes(lake, [("SPX", "NDX")], [day], n_pairs=2000)[0]["n_new"] == 12  # 3 tenors, 4 lines
    assert build_basket_quotes(lake, [("SPX", "NDX")], [day], n_pairs=2000)[0]["n_new"] == 0
    rows = lake.read("basket_spreads")
    assert set(rows["origin"]) == {"synthetic"} and set(rows["source"]) == {"copula_on_surfaces"}
    assert build_basket_quotes(lake, [("SPX", "AAPL")], [day], n_pairs=2000) == []  # no surface of AAPL in this lake


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


def test_a_low_barrier_takes_a_higher_correlation_and_the_matrix_stays_valid():
    m = np.array([[1.0, 0.9, 0.5], [0.9, 1.0, 0.5], [0.5, 0.5, 1.0]])
    down = skewed_matrix(m, 0.6)
    up = skewed_matrix(m, 1.2)
    assert skewed_matrix(m, 1.0) == pytest.approx(m, abs=1e-12)
    assert CorrelationSkew().offset(0.9) == pytest.approx(0.05) and CorrelationSkew().offset(1.1) == pytest.approx(
        -0.025
    )
    assert down[0, 2] > 0.5 > up[0, 2]
    assert np.linalg.eigvalsh(down).min() >= -1e-10 and np.allclose(np.diag(down), 1.0)
    correlation_root(down)
