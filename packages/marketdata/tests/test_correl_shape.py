"""The correlation skew's shape from closes, on synthetic histories: a correlation that rises after the basket has
fallen gives a downside cap, a constant one none (within its band)."""

import numpy as np
import pandas as pd
import pytest

from optlab.marketdata.derive.correl_shape import correlation_shape
from optlab.marketdata.lake import Lake

NAMES = ("SPX", "NDX", "RUT")


def history(skew: bool, seed: int, n: int = 2500) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2015-01-02", periods=n)
    vol = np.array([0.18, 0.22, 0.25]) / np.sqrt(252)
    logp = np.zeros((n, 3))
    for t in range(1, n):
        trailing = logp[t - 1].mean() - logp[max(t - 64, 0)].mean()
        rho = 0.5 + (0.4 if skew and trailing < -0.06 else 0.0)
        c = np.full((3, 3), rho) + (1 - rho) * np.eye(3)
        logp[t] = logp[t - 1] + vol * (np.linalg.cholesky(c) @ rng.standard_normal(3))
    close = 100 * np.exp(logp)
    rows = [
        pd.DataFrame(
            {
                "instrument": name,
                "obs_date": days.date,
                "open": close[:, i],
                "high": close[:, i],
                "low": close[:, i],
                "close": close[:, i],
                "adj_close": close[:, i],
                "volume": 0.0,
            }
        )
        for i, name in enumerate(NAMES)
    ]
    return pd.concat(rows)


@pytest.fixture(scope="module", params=[True, False], ids=["skew", "flat"])
def lake(request, tmp_path_factory):
    lk = Lake(tmp_path_factory.mktemp("shape") / "lake")
    rows = history(request.param, seed=4)
    lk.ingest("prices_eod", rows, "test", "observed")
    return lk, request.param, rows["obs_date"].max()


def test_the_shape_follows_the_history(lake):
    lk, skew, day = lake
    got = correlation_shape(lk, NAMES, day, n_boot=100)
    assert got["origin"] == "derived" and len(got["bins"]) == 5
    low = got["bins"][0]
    if skew:
        assert got["lam_dn"] > 0.3 and low["band"][0] > 0.1, got
    else:
        assert got["lam_dn"] < 0.2 and low["band"][0] < 0.0 < low["band"][1], got


def test_too_short_a_history_is_refused(lake):
    lk, _, _ = lake
    with pytest.raises(ValueError, match="too few"):
        correlation_shape(lk, NAMES, "2015-12-31")
