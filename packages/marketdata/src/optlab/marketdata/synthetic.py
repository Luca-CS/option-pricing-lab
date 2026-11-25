"""A small synthetic lake with every table the point-in-time market reads: the offline tests' lake, the CI's, and the
demo data of `optlab demo data` (`optlab serve --demo-data`). Every level is made up, from a fixed seed: no vendor's
data, so it may go anywhere (fixtures, CI artefacts, a demo build). The inputs carry the origin `observed` for the tests,
which play the part of real sources on them; the demo's lake says `synthetic`, which is what they are."""

from __future__ import annotations

import numpy as np
import pandas as pd

from optlab.core.instruments import instrument
from optlab.marketdata.derive.bank import BankParams, build_bank
from optlab.marketdata.derive.carry import build_carry
from optlab.marketdata.derive.correl import build_basket_quotes, build_premium
from optlab.marketdata.derive.curves import build_discount_curves
from optlab.marketdata.derive.surfaces import ShapePrior, build_reconstructed
from optlab.marketdata.lake import Lake

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


def fill_observed(lake: Lake, days, paths=None, origin: str = "observed") -> dict:
    paths = paths or price_paths(days)
    rows = [
        pd.DataFrame({"instrument": n, "obs_date": days, "close": p, "open": p, "high": p, "low": p, "adj_close": p})
        for n, p in paths.items()
    ]
    t = (days - days[0]).days / 365.0
    spx = paths["SPX"]
    rows.append(pd.DataFrame({"instrument": "SPX.TR", "obs_date": days, "close": spx * np.exp(0.015 * t)}))
    lake.ingest("prices_eod", pd.concat(rows), "yahoo", origin)
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
        origin,
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
        origin,
    )
    lake.ingest(
        "correl_implied",
        pd.DataFrame({"index_name": "COR3M", "tenor": 0.25, "obs_date": days, "correlation": 0.5}),
        "cboe",
        origin,
    )
    return paths


def derive_all(lake: Lake, start: str, names=("SPX", "NDX"), prior: ShapePrior = PRIOR) -> None:
    build_discount_curves(lake, start)
    build_carry(lake, [instrument(n) for n in NAMES])
    build_premium(lake)
    build_bank(lake, BankParams(start=start))
    build_reconstructed(lake, names, start, prior=prior)


def _third_friday(year: int, month: int) -> pd.Timestamp:
    first = pd.Timestamp(year, month, 1)
    return first + pd.Timedelta(days=(4 - first.weekday()) % 7 + 14)


def option_chain(day, spot: float, discount, seed: int, q: float = 0.015) -> pd.DataFrame:
    """A made-up SPX chain of one day: European calls and puts on monthly expiries up to two years, strikes from 60 % to
    140 % of the spot, their implied vols from an SSVI smile (skew and term structure of an equity index) plus a little
    noise, and a bid and an ask around the Black price. `discount` is T -> D(T), the lake's curve, so that the cleaning
    finds the forward back from the put-call parity."""
    from optlab.core.blackscholes import black_price

    day = pd.Timestamp(day)
    rng = np.random.default_rng(seed)
    months = [day + pd.DateOffset(months=m) for m in (1, 2, 3, 6, 9, 12, 18, 24)]
    expiries = sorted({_third_friday(d.year, d.month) for d in months})
    rows = []
    for expiry in expiries:
        T = (expiry - day).days / 365.0
        if T < 7 / 365:
            continue
        D = float(discount(T))
        F = spot * np.exp(-q * T) / D
        theta = (0.17**2 + 0.01 * T) * T  # ATM total variance: 17 % short, rising with the maturity
        phi = 0.8 / theta**0.4
        rho = -0.7
        for m in np.arange(0.60, 1.401, 0.025):
            K = round(m * spot / 5) * 5.0
            k = np.log(K / F)
            w = 0.5 * theta * (1 + rho * phi * k + np.sqrt((phi * k + rho) ** 2 + 1 - rho**2))
            iv = np.sqrt(w / T) + rng.normal(0.0, 0.002)
            for cp in ("C", "P"):
                mid = float(black_price(F, K, T, iv, D, "call" if cp == "C" else "put"))
                half = max(0.05, 0.015 * mid)
                if mid <= 2 * half:
                    continue  # too far out of the money to carry a two-sided quote
                rows.append(
                    {
                        "underlying": "SPX",
                        "root": "SPXW",
                        "expiry": expiry.date(),
                        "strike": K,
                        "cp": cp,
                        "bid": round(mid - half, 2),
                        "ask": round(mid + half, 2),
                        "iv": iv,
                        "underlying_close": spot,
                        "obs_date": day.date(),
                    }
                )
    return pd.DataFrame(rows)


def fill_option_quotes(lake: Lake, days, paths: dict, origin: str, n_days: int = 3) -> int:
    """The last `n_days` business days get an SPX chain each (source `synthetic`): what the vol lab fits."""
    from optlab.marketdata.quotes import discount_function

    chains = [
        option_chain(d, float(paths["SPX"][len(days) - n_days + i]), discount_function(lake, "USD", d), seed=100 + i)
        for i, d in enumerate(days[-n_days:])
    ]
    lake.ingest("option_quotes", pd.concat(chains), "synthetic", origin)
    return sum(len(c) for c in chains)


def synthetic_lake(
    root, start="2019-01-01", end="2021-12-31", origin: str = "observed", options: bool = False
) -> tuple[Lake, pd.DatetimeIndex, dict]:
    lake = Lake(root)
    days = pd.bdate_range(start, end)
    paths = fill_observed(lake, days, origin=origin)
    derive_all(lake, start)
    if options:
        fill_option_quotes(lake, days, paths, origin)
        # the CvC and PvP pseudo-quotes of the last day, on the reconstructed surfaces (origin synthetic either way)
        build_basket_quotes(lake, [("SPX", "NDX")], [days[-1]], n_pairs=20_000)
    return lake, days, paths
