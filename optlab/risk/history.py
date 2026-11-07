"""Daily market factors read from the lake: spots, at-the-money vols per tenor and the 90-110 % skew, zero rates per tenor,
carry, realised correlation. Every series keeps its `origin` (a reconstructed surface is "reconstructed", and screens say so).

The vols come from one surface layer over the whole history (the reconstructed one by default, like the replay): a switch of
layer would show as a market move. Vols are read in forward moneyness, as the surfaces and the risk grid are.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..marketdata.lake import Lake, sql_str
from ..svi import SVISurface
from . import grid

SKEW_K = (float(np.log(0.9)), float(np.log(1.1)))
SKEW_T = 1.0


def closes(lake: Lake, names, start, end) -> pd.DataFrame:
    """Close of each name by date (columns = names)."""
    out = {}
    for n in names:
        rows = lake.read(
            "prices_eod",
            where=f"instrument = {sql_str(n)} AND obs_date >= DATE '{pd.Timestamp(start).date()}'"
            f" AND obs_date <= DATE '{pd.Timestamp(end).date()}'",
            columns="obs_date, close",
            order="obs_date",
        )
        out[n] = pd.Series(rows["close"].to_numpy(dtype=float), index=pd.to_datetime(rows["obs_date"]).dt.date)
    return pd.DataFrame(out).sort_index()


def vol_factors(lake: Lake, name: str, start, end, layer: str = "reconstructed") -> pd.DataFrame:
    """Per date: at-the-money (forward) vol at each tenor of the grid ("atm_1m"...), the 90-110 % skew at one year ("skew"),
    and the origin of the surface."""
    rows = lake.read(
        "vol_surfaces",
        where=f"underlying = {sql_str(name)} AND layer = {sql_str(layer)} AND obs_date >= DATE "
        f"'{pd.Timestamp(start).date()}' AND obs_date <= DATE '{pd.Timestamp(end).date()}'",
        columns="obs_date, surface, origin",
        order="obs_date",
    )
    T = np.asarray(grid.TENORS)
    recs = []
    for d, s, origin in zip(rows["obs_date"], rows["surface"], rows["origin"]):
        surf = SVISurface.from_dict(json.loads(s))
        atm = surf.iv(np.zeros(len(T)), T)
        lo, hi = surf.iv(np.array(SKEW_K), np.full(2, SKEW_T))
        recs.append(
            {
                "date": pd.Timestamp(d).date(),
                **{f"atm_{lab}": float(v) for lab, v in zip(grid.TENOR_LABELS, atm)},
                "skew": float(lo - hi),
                "origin": origin,
            }
        )
    df = pd.DataFrame(recs)
    return df.set_index("date") if len(df) else df


def zero_rates(lake: Lake, currency: str, start, end) -> pd.DataFrame:
    """Zero rates of a currency's discount curve at each tenor of the grid, by date (linear in the curve's tenors)."""
    rows = lake.read(
        "discount_curves",
        where=f"curve = {sql_str(currency + '.DISC')} AND obs_date >= DATE "
        f"'{pd.Timestamp(start).date()}' AND obs_date <= DATE '{pd.Timestamp(end).date()}'",
        columns="obs_date, tenor, zero_rate",
        order="obs_date, tenor",
    )
    out = {}
    for d, g in rows.groupby("obs_date"):
        out[pd.Timestamp(d).date()] = np.interp(grid.TENORS, g["tenor"].to_numpy(float), g["zero_rate"].to_numpy(float))
    return pd.DataFrame.from_dict(out, orient="index", columns=[f"r_{lab}" for lab in grid.TENOR_LABELS]).sort_index()


def carry(lake: Lake, name: str, start, end) -> pd.DataFrame:
    """The implied carry ln(F / S) / T at each tenor, by date, from the lake's carry table (its method in `method`)."""
    rows = lake.read(
        "carry_implied",
        where=f"underlying = {sql_str(name)} AND obs_date >= DATE '{pd.Timestamp(start).date()}'"
        f" AND obs_date <= DATE '{pd.Timestamp(end).date()}'",
        columns="obs_date, tenor, carry, method",
        order="obs_date, tenor",
    )
    out, methods = {}, {}
    for (d, m), g in rows.groupby(["obs_date", "method"]):
        day = pd.Timestamp(d).date()
        if day in out and methods[day] == "total_return":
            continue  # one method a day, the total-return one first when a name has several
        out[day] = np.interp(grid.TENORS, g["tenor"].to_numpy(float), g["carry"].to_numpy(float))
        methods[day] = m
    df = pd.DataFrame.from_dict(out, orient="index", columns=[f"c_{lab}" for lab in grid.TENOR_LABELS]).sort_index()
    df["method"] = pd.Series(methods)
    return df


def realised_correlation(spots: pd.DataFrame, window: int = 120) -> dict:
    """{"A|B": rolling correlation of daily log returns} for every pair of columns, each pair on the days both names have a
    close (a holiday of one market does not break the window), carried to the other days."""
    out = {}
    cols = list(spots.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            both = spots[[a, b]].dropna()
            r = np.log(both).diff().dropna()
            c = r[a].rolling(window).corr(r[b])
            out["|".join(sorted((a, b)))] = c.reindex(spots.index).ffill()
    return out
