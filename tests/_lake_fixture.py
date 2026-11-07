"""A small synthetic lake with every table the point-in-time market reads, for offline tests. All levels are made up."""

from __future__ import annotations

import numpy as np
import pandas as pd

from optlab.marketdata.derive.bank import BankParams, build_bank
from optlab.marketdata.derive.carry import build_carry
from optlab.marketdata.derive.correl import build_premium
from optlab.marketdata.derive.curves import build_discount_curves
from optlab.marketdata.derive.surfaces import ShapePrior, build_reconstructed
from optlab.marketdata.lake import Lake
from optlab.marketdata.universe import instrument

PRIOR = ShapePrior(-0.7, 1.0, 0.3, 0.05, source="test")
NAMES = ("SPX", "NDX", "AAPL", "AMZN", "GOOGL", "GS", "IBM")


def price_paths(days, seed=11, names=NAMES):
    rng = np.random.default_rng(seed)
    common = rng.standard_normal(len(days))
    out = {}
    for i, n in enumerate(names):
        own = rng.standard_normal(len(days))
        out[n] = (1000.0 + 100 * i) * np.exp(np.cumsum(0.008 * common + 0.006 * own + 0.0002))
    return out


def fill_observed(lake: Lake, days, paths=None) -> dict:
    paths = paths or price_paths(days)
    rows = [
        pd.DataFrame({"instrument": n, "obs_date": days, "close": p, "open": p, "high": p, "low": p, "adj_close": p})
        for n, p in paths.items()
    ]
    t = (days - days[0]).days / 365.0
    spx = paths["SPX"]
    rows.append(pd.DataFrame({"instrument": "SPX.TR", "obs_date": days, "close": spx * np.exp(0.015 * t)}))
    lake.ingest("prices_eod", pd.concat(rows), "yahoo", "observed")
    wiggle = 1 + 0.1 * np.sin(np.arange(len(days)) / 20)
    vols = {
        "VIX9D": 15.0,
        "VIX": 17.0,
        "VIX3M": 19.0,
        "VIX6M": 20.0,
        "VIX1Y": 21.0,
        "VXN": 22.0,
        "VXAPL": 28.0,
        "SKEW": 130.0,
    }
    lake.ingest(
        "vol_indices",
        pd.concat(
            [pd.DataFrame({"index_name": n, "obs_date": days, "close": lvl * wiggle}) for n, lvl in vols.items()]
        ),
        "cboe",
        "observed",
    )
    rates = [
        (c, ten, r)
        for c, ten, r in (
            ("USD.TREASURY", 0.25, 2.0),
            ("USD.TREASURY", 1.0, 2.2),
            ("USD.TREASURY", 5.0, 2.5),
            ("USD.TREASURY", 10.0, 2.8),
            ("USD.FF", 1 / 365, 1.9),
            ("USD.CMT10", 10.0, 2.0),
            ("USD.BAA", 20.0, 4.0),
        )
    ]
    lake.ingest(
        "rate_points",
        pd.concat(
            [pd.DataFrame({"curve": c, "tenor": ten, "obs_date": days, "rate": r * wiggle}) for c, ten, r in rates]
        ),
        "fred",
        "observed",
    )
    lake.ingest(
        "correl_implied",
        pd.DataFrame({"index_name": "COR3M", "tenor": 0.25, "obs_date": days, "correlation": 0.5}),
        "cboe",
        "observed",
    )
    return paths


def derive_all(lake: Lake, start: str, names=("SPX", "NDX"), prior: ShapePrior = PRIOR) -> None:
    build_discount_curves(lake, start)
    build_carry(lake, [instrument(n) for n in NAMES])
    build_premium(lake)
    build_bank(lake, BankParams(start=start))
    build_reconstructed(lake, names, start, prior=prior)


def synthetic_lake(root, start="2019-01-01", end="2021-12-31") -> tuple[Lake, pd.DatetimeIndex, dict]:
    lake = Lake(root)
    days = pd.bdate_range(start, end)
    paths = fill_observed(lake, days)
    derive_all(lake, start)
    return lake, days, paths
