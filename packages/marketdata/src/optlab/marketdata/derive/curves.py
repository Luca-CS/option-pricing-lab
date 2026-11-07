"""Discount curves from free rates. These are government curves, not OIS: the desk has no OIS curve on both sides of a pair.

USD: Treasury par yields (bond-equivalent, semi-annual) bootstrapped to continuous zero rates, with the overnight
SOFR, or the Fed funds rate before April 2018, as the shortest pillar. EUR: the ECB's AAA spot rates, which are already
continuous zero rates, with €STR at the short end when it exists. JPY: the Ministry of Finance's JGB par yields, semi-annual, bootstrapped
like the Treasuries. They start at one year, so below it the curve runs from the overnight call rate to the one-year zero rate. GBP: the
Bank of England's fitted gilt spot rates, continuous zero rates, with SONIA as the shortest pillar. A currency whose rates are not in the
lake gets no curve.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from optlab.marketdata.lake import Lake, new_run_id

USD_CURVE, EUR_CURVE, JPY_CURVE, GBP_CURVE = "USD.DISC", "EUR.DISC", "JPY.DISC", "GBP.DISC"
CURVE_OF_CURRENCY = {"USD": USD_CURVE, "EUR": EUR_CURVE, "JPY": JPY_CURVE, "GBP": GBP_CURVE}
OVERNIGHT = 1 / 365


def bootstrap_par(tenors, par_rates) -> tuple[np.ndarray, np.ndarray]:
    """Zero rates (continuous) at `tenors` from semi-annual par yields (fractions). Up to six months a par yield is a
    simple zero-coupon yield; beyond, par yields are interpolated linearly on the semi-annual grid and bootstrapped."""
    tenors, par = np.asarray(tenors, dtype=float), np.asarray(par_rates, dtype=float)
    order = np.argsort(tenors)
    tenors, par = tenors[order], par[order]
    zeros = np.empty_like(par)
    short = tenors <= 0.5 + 1e-9
    zeros[short] = np.log(1 + par[short] * tenors[short]) / tenors[short]
    if short.all():
        return tenors, zeros
    grid = np.arange(0.5, tenors[-1] + 1e-9, 0.5)
    par_grid = np.interp(grid, tenors, par)
    disc = np.empty_like(grid)
    disc[0] = 1 / (1 + par_grid[0] / 2)
    for i in range(1, len(grid)):
        coupon = par_grid[i] / 2
        disc[i] = (1 - coupon * disc[:i].sum()) / (1 + coupon)
    z_grid = -np.log(disc) / grid
    zeros[~short] = np.interp(tenors[~short], grid, z_grid)
    return tenors, zeros


def _rates(lake: Lake, curve: str) -> pd.DataFrame:
    return lake.read("rate_points", where=f"curve = '{curve}'", columns="obs_date, tenor, rate")


def par_curve_rows(par: pd.DataFrame, overnight: pd.DataFrame, curve: str) -> pd.DataFrame:
    """From semi-annual par yields. overnight: obs_date, rate (percent), already the best available short rate of each day."""
    on = {pd.Timestamp(d).date(): r / 100 for d, r in zip(overnight["obs_date"], overnight["rate"])}
    rows = []
    for day, g in par.groupby("obs_date"):
        t, z = bootstrap_par(g["tenor"], g["rate"] / 100)
        t, z = list(t), list(z)
        d = pd.Timestamp(day).date()
        if d in on:
            t.insert(0, OVERNIGHT)
            z.insert(0, np.log(1 + on[d] * OVERNIGHT) / OVERNIGHT)
        for ti, zi in zip(t, z):
            rows.append((d, ti, zi, float(np.exp(-zi * ti))))
    return pd.DataFrame(rows, columns=["obs_date", "tenor", "zero_rate", "discount"]).assign(curve=curve)


def zero_curve_rows(zeros: pd.DataFrame, overnight: pd.DataFrame, curve: str) -> pd.DataFrame:
    """From rates that are already continuous zero rates, in percent."""
    on = {pd.Timestamp(d).date(): r / 100 for d, r in zip(overnight["obs_date"], overnight["rate"])}
    rows = []
    for day, g in zeros.groupby("obs_date"):
        d = pd.Timestamp(day).date()
        g = g.sort_values("tenor")
        t, z = list(g["tenor"]), list(g["rate"] / 100)
        if d in on:
            t.insert(0, OVERNIGHT)
            z.insert(0, np.log(1 + on[d] * OVERNIGHT) / OVERNIGHT)
        rows += [(d, ti, zi, float(np.exp(-zi * ti))) for ti, zi in zip(t, z)]
    return pd.DataFrame(rows, columns=["obs_date", "tenor", "zero_rate", "discount"]).assign(curve=curve)


def _best_overnight(lake: Lake, primary: str, fallback: str | None = None) -> pd.DataFrame:
    best = _rates(lake, primary)[["obs_date", "rate"]]
    if fallback:
        older = _rates(lake, fallback)[["obs_date", "rate"]]
        best = pd.concat([older[~older["obs_date"].isin(best["obs_date"])], best], ignore_index=True)
    return best


def build_discount_curves(lake: Lake, start: str = "2015-01-01") -> list[dict]:
    run = new_run_id("derive_curves")
    built = (
        (
            par_curve_rows(_rates(lake, "USD.TREASURY"), _best_overnight(lake, "USD.SOFR", "USD.FF"), USD_CURVE),
            "treasury+sofr",
        ),
        (zero_curve_rows(_rates(lake, "EUR.AAA"), _best_overnight(lake, "EUR.ESTR"), EUR_CURVE), "ecb_aaa+estr"),
        (par_curve_rows(_rates(lake, "JPY.JGB"), _best_overnight(lake, "JPY.CALL"), JPY_CURVE), "jgb+call"),
        (zero_curve_rows(_rates(lake, "GBP.GLC"), _best_overnight(lake, "GBP.SONIA"), GBP_CURVE), "boe_glc+sonia"),
    )
    return [
        lake.ingest("discount_curves", rows[pd.to_datetime(rows["obs_date"]) >= start], source, "derived", run)
        for rows, source in built
        if len(rows)
    ]
