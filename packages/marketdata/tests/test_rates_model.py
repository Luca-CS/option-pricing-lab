"""The rates model's parameters from the lake, on a synthetic history of known parameters: a Hull-White zero curve and
an equity correlated with its factor. Point in time: a later day's rows never move an earlier estimate."""

import numpy as np
import pandas as pd
import pytest

from optlab.core.rates import B
from optlab.marketdata.derive.rates_model import hw_estimate, rate_correlation
from optlab.marketdata.lake import Lake

SIGMA, A, RHO, VOL = 0.010, 0.03, 0.3, 0.18
TENORS = (0.25, 1.0, 2.0, 5.0, 10.0)


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    rng = np.random.default_rng(3)
    days = pd.bdate_range("2023-01-02", periods=700)
    dt = 1 / 252
    z = rng.standard_normal((2, len(days)))
    dw_r = z[0] * np.sqrt(dt)
    dw_s = (RHO * z[0] + np.sqrt(1 - RHO**2) * z[1]) * np.sqrt(dt)
    x = np.zeros(len(days))
    for i in range(1, len(days)):
        x[i] = x[i - 1] * np.exp(-A * dt) + SIGMA * dw_r[i]
    rows = []
    for tau in TENORS:
        zero = 0.035 + float(B(A, tau)) / tau * x
        rows.append(
            pd.DataFrame({"obs_date": days.date, "tenor": tau, "zero_rate": zero, "discount": np.exp(-zero * tau)})
        )
    lk = Lake(tmp_path_factory.mktemp("rates") / "lake")
    lk.ingest("discount_curves", pd.concat(rows).assign(curve="USD.DISC"), "test", "derived")
    close = 5000 * np.exp(np.cumsum(VOL * dw_s - 0.5 * VOL**2 * dt))
    lk.ingest(
        "prices_eod",
        pd.DataFrame(
            {
                "instrument": "SPX",
                "obs_date": days.date,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "adj_close": close,
                "volume": 0.0,
            }
        ),
        "test",
        "observed",
    )
    return lk, days


def test_the_rates_volatility_is_hull_white_s(lake):
    lk, days = lake
    got = hw_estimate(lk, "USD", days[-1].date())
    assert got["window"] == 504 and got["origin"] == {"sigma": "derived", "a": "assumption"}
    assert got["sigma"] == pytest.approx(SIGMA, rel=0.08)  # two years of daily changes: about 3 % of error per tenor
    assert got["tenors"] == [1.0, 2.0, 5.0, 10.0]  # from one year


def test_the_equity_rate_correlation_and_its_band(lake):
    lk, days = lake
    got = rate_correlation(lk, "SPX", "USD", days[-1].date())
    assert got["band"][0] < RHO < got["band"][1]
    assert got["tenor"] == 2.0 and got["window"] == 504


def test_an_estimate_reads_only_its_past(lake):
    lk, days = lake
    day = days[-150].date()
    before = hw_estimate(lk, "USD", day)
    assert before["to"] <= str(day)
    assert rate_correlation(lk, "SPX", "USD", day)["window"] == 504
    with pytest.raises(ValueError, match="too few"):
        hw_estimate(lk, "USD", days[50].date())
