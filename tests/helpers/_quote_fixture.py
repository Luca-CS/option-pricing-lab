"""Option quotes from a known smile on known curves, on one day: the cleaning chain should find them again (used by
the lake, vol lab and desk tests)."""

import datetime as dt

import numpy as np
import pandas as pd

from optlab.core.blackscholes import black_price
from optlab.marketdata.derive.curves import USD_CURVE
from optlab.marketdata.sources.archives import snapshot_time

DAY = dt.date(2022, 9, 15)
RATE, CARRY, SPOT = 0.03, 0.015, 4000.0


def smile(k, T):
    return 0.22 - 0.25 * k + 0.5 * k * k + 0.01 * T


def synthetic_rows(expiries=tuple(DAY + dt.timedelta(days=d) for d in (30, 60, 120, 240, 400)), half_spread=0.002):
    """Quotes from a known smile, on known curves: the cleaning should find them again. A relative half spread keeps
    the deep out-of-the-money quotes two-sided, and a price floor keeps a small tick."""
    rows = []
    T0 = snapshot_time(DAY)
    for expiry in expiries:
        close = dt.datetime.combine(expiry, dt.time(16, 0), tzinfo=T0.tzinfo)
        T = (close - T0).total_seconds() / (365 * 86400)
        F, D = SPOT * np.exp(CARRY * T), np.exp(-RATE * T)
        for K in np.arange(2800.0, 5201.0, 50.0):
            vol = smile(np.log(K / F), T)
            for cp in ("C", "P"):
                price = float(black_price(F, K, T, vol, D, "call" if cp == "C" else "put"))
                rows.append(
                    {
                        "underlying": "SPX",
                        "root": "SPXW",
                        "expiry": expiry,
                        "strike": K,
                        "cp": cp,
                        "obs_date": DAY,
                        "bid": price * (1 - half_spread),
                        "ask": price * (1 + half_spread),
                        "underlying_close": SPOT,
                        "settlement": "PM",
                    }
                )
    return pd.DataFrame(rows)


def curve_rows():
    return pd.DataFrame(
        {"curve": USD_CURVE, "obs_date": DAY, "tenor": [1 / 365, 1.0, 5.0], "zero_rate": RATE, "discount": np.nan}
    )
