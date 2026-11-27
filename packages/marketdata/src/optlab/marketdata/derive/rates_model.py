"""The parameters of the stochastic rates model, from the lake's history: the Hull-White volatility from the zero rates'
daily changes, the equity-rate correlation from the closes against those changes. Only rows dated on or before the
day are read (point in time), over a window of business days (504: two years).

Under Hull-White the tau-year zero rate moves by sigma B_a(tau)/tau dW, so each tenor's annualised volatility of daily
changes s_tau is fitted by least squares on that shape: sigma = sum s_tau b_tau / sum b_tau^2, b_tau = B_a(tau)/tau,
over the tenors from one year (the short end follows the policy rate, not a diffusion). The mean reversion is an
assumption (3 %): history gives no positive estimate of it at this window. The correlation is that of the equity's
daily log returns with the two-year zero rate's daily changes (the rate factor's proxy). Each value comes with its
origin, its method and the band of its estimate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from optlab.core.rates import B
from optlab.marketdata.derive.curves import CURVE_OF_CURRENCY
from optlab.marketdata.lake import Lake, sql_str

WINDOW = 504
MEAN_REVERSION = 0.03  # an assumption: no positive estimate from two years of history
FACTOR_TENOR = 2.0
MIN_TENOR = 1.0
MIN_DAYS = 120


def _zero_history(lake: Lake, currency: str, day, window: int) -> pd.DataFrame:
    curve = CURVE_OF_CURRENCY.get(currency)
    if curve is None:
        raise KeyError(f"no discount curve of {currency} in the lake")
    rows = lake.read(
        "discount_curves",
        where=f"curve = {sql_str(curve)} AND obs_date <= {sql_str(str(day))}",
        columns="obs_date, tenor, zero_rate",
    )
    if rows.empty:
        raise KeyError(f"no {curve} on or before {day}")
    wide = rows.pivot_table(index="obs_date", columns="tenor", values="zero_rate").sort_index()
    return wide.iloc[-(window + 1) :]


def hw_estimate(lake: Lake, currency: str, day, window: int = WINDOW, a: float = MEAN_REVERSION) -> dict:
    """Hull-White's sigma fitted on the tenors' volatilities (annualised, 252 days), a given."""
    wide = _zero_history(lake, currency, day, window)
    changes = wide.diff().dropna(how="all")
    tenors = np.array([float(t) for t in wide.columns if float(t) >= MIN_TENOR - 1e-9])
    if len(changes) < MIN_DAYS or not len(tenors):
        raise ValueError(f"{len(changes)} days of {currency} zero rates before {day}: too few to estimate a volatility")
    vols = np.array(
        [float(changes[t].std(ddof=1)) * np.sqrt(252) for t in wide.columns if float(t) >= MIN_TENOR - 1e-9]
    )
    b = np.asarray(B(a, tenors)) / tenors
    sigma = float(np.dot(vols, b) / np.dot(b, b))
    fitted = sigma * b
    return {
        "currency": currency,
        "sigma": sigma,
        "a": a,
        "window": int(len(changes)),
        "from": pd.Timestamp(changes.index[0]).date().isoformat(),
        "to": pd.Timestamp(changes.index[-1]).date().isoformat(),
        "tenors": tenors.tolist(),
        "vols": vols.tolist(),
        "fitted": fitted.tolist(),
        "origin": {"sigma": "derived", "a": "assumption"},
        "method": "least squares of the tenors' daily volatilities on sigma B_a(tau)/tau",
    }


def rate_correlation(lake: Lake, name: str, currency: str, day, window: int = WINDOW) -> dict:
    """The correlation of the name's daily log returns with the two-year zero rate's daily changes, with its band
    (Fisher's z, two standard errors)."""
    wide = _zero_history(lake, currency, day, window)
    tenor = min(wide.columns, key=lambda t: abs(float(t) - FACTOR_TENOR))
    rate = wide[tenor].diff()
    closes = lake.read(
        "prices_eod",
        where=f"instrument = {sql_str(name)} AND obs_date <= {sql_str(str(day))}",
        columns="obs_date, close",
    )
    if closes.empty:
        raise KeyError(f"no closes of {name} on or before {day}")
    series = closes.set_index("obs_date")["close"].sort_index().astype(float)
    equity = pd.Series(np.log(series.to_numpy()), index=series.index).diff()
    both = pd.concat([equity.rename("s"), rate.rename("r")], axis=1, join="inner").dropna().iloc[-window:]
    if len(both) < MIN_DAYS:
        raise ValueError(f"{len(both)} common days of {name} and {currency} rates before {day}: too few")
    rho = float(np.corrcoef(both["s"], both["r"])[0, 1])
    z, se = np.arctanh(np.clip(rho, -0.999, 0.999)), 1 / np.sqrt(len(both) - 3)
    return {
        "name": name,
        "currency": currency,
        "rho": rho,
        "band": [float(np.tanh(z - 2 * se)), float(np.tanh(z + 2 * se))],
        "window": int(len(both)),
        "tenor": float(tenor),
        "origin": "derived",
        "method": f"daily log returns against the {float(tenor):g}-year zero rate's daily changes",
    }
