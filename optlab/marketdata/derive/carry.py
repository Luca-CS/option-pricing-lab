"""Forwards and carry: covered interest parity for FX, cost of carry for shares, put-call parity where chains exist.

No free source gives repo or index dividends. Where chains exist, parity gives a net carry, which is not split into
dividend and repo for an index. A share's repo is what remains after its known dividends. Elsewhere the carry is an
estimate from the past (the realised yield of a total-return index, trailing dividends) or a labelled assumption.
Everything used at a date is known at that date: future dividends are never read.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..lake import Lake, new_run_id

# Assumed dividend yields of indices with neither chains nor a total-return index (origin = synthetic).
SYNTHETIC_YIELD = {"NDX": 0.008, "SX5E": 0.03}


def cip_forward(spot, discount_domestic, discount_foreign):
    """FX forward by covered interest parity. Spot is in domestic per foreign unit: F = S Df / Dd."""
    return np.asarray(spot) * np.asarray(discount_foreign) / np.asarray(discount_domestic)


def equity_forward(spot: float, T, discount, repo: float = 0.0, dividends=()) -> np.ndarray:
    """F(T) = S exp(-repo T) / D(T) - sum of known dividends paid before T, carried to T. `discount` is T -> D(T),
    `dividends` a list of (time, amount)."""
    T = np.atleast_1d(np.asarray(T, dtype=float))
    out = spot * np.exp(-repo * T) / discount(T)
    for t_pay, amount in dividends:
        out = out - np.where(t_pay <= T, amount * np.exp(-repo * (T - t_pay)) * discount(t_pay) / discount(T), 0.0)
    return out


def parity_forward_discount(strikes, calls, puts) -> tuple[float, float]:
    """(D, F) from C - P = D (F - K): an exact line in K, by least squares."""
    K, diff = np.asarray(strikes, dtype=float), np.asarray(calls, dtype=float) - np.asarray(puts, dtype=float)
    slope, intercept = np.polyfit(K, diff, 1)
    return float(-slope), float(-intercept / slope)


def parity_carry_rows(quotes: pd.DataFrame, discount, spot: float, band: float = 0.05, min_pairs: int = 3) -> pd.DataFrame:
    """Net carry per expiry from a day's chain (expiry, strike, cp, bid, ask): the median near the money of
    F = K + (C - P) / D, with D from the discount curve. Short maturities carry almost no information on the rate, so
    the curve is taken as given."""
    q = quotes[(quotes["bid"] > 0) & (quotes["ask"] > quotes["bid"])].copy()
    q["mid"] = 0.5 * (q["bid"] + q["ask"])
    day = pd.Timestamp(q["obs_date"].iloc[0]).date() if len(q) else None
    rows = []
    for expiry, g in q.groupby("expiry"):
        T = (pd.Timestamp(expiry).date() - day).days / 365.0
        if T <= 0.01:
            continue
        pairs = g[g["cp"] == "C"].merge(g[g["cp"] == "P"], on="strike", suffixes=("_c", "_p"))
        pairs = pairs[np.abs(pairs["strike"] / spot - 1) < band]
        if len(pairs) < min_pairs:
            continue
        D = float(discount(T))
        F = float(np.median(pairs["strike"] + (pairs["mid_c"] - pairs["mid_p"]) / D))
        rows.append({"obs_date": day, "tenor": T, "discount": D, "forward_ratio": F / spot, "carry": np.log(F / spot) / T})
    return pd.DataFrame(rows, columns=["obs_date", "tenor", "discount", "forward_ratio", "carry"])


def realised_yield(price: pd.Series, total_return: pd.Series, window_days: int = 365) -> pd.Series:
    """Dividend yield of the past `window_days`: ln(TR_t / TR_t-w) - ln(P_t / P_t-w), annualised. Both series indexed by
    date. Only past levels enter."""
    both = pd.concat({"p": price, "tr": total_return}, axis=1).dropna().sort_index()
    both.index = pd.to_datetime(both.index)
    lagged = both.reindex(both.index - pd.Timedelta(days=window_days), method="ffill")
    lagged.index = both.index
    gap = np.log(both["tr"] / lagged["tr"]) - np.log(both["p"] / lagged["p"])
    return (gap * 365.0 / window_days).dropna()


def trailing_dividend_yield(dividends: pd.DataFrame, price: pd.Series) -> pd.Series:
    """Dividends of the past year over the close, per day of `price`. dividends: obs_date (ex date), value."""
    px = price.copy()
    px.index = pd.to_datetime(px.index)
    px = px.sort_index()
    if dividends.empty:
        return pd.Series(0.0, index=px.index)
    ex = pd.Series(dividends["value"].to_numpy(dtype=float), index=pd.to_datetime(dividends["obs_date"])).sort_index()
    paid = ex.reindex(px.index.union(ex.index)).fillna(0.0).rolling("365D").sum().reindex(px.index)
    return paid / px


def build_carry(lake: Lake, instruments) -> list[dict]:
    """Daily carry estimates per instrument, tenor 1 year: dividend yield with its method. `rate - yield` is the carry."""
    out = []
    run = new_run_id("derive_carry")
    for inst in instruments:
        prices = lake.read("prices_eod", where=f"instrument = '{inst.id}'", columns="obs_date, close", order="obs_date")
        if prices.empty:
            continue
        price = pd.Series(prices["close"].to_numpy(), index=pd.to_datetime(prices["obs_date"]))
        origin, method = "derived", "total_return"
        if inst.total_return:
            tr = lake.read("prices_eod", where=f"instrument = '{inst.id}.TR'", columns="obs_date, close", order="obs_date")
            series = realised_yield(price, pd.Series(tr["close"].to_numpy(), index=pd.to_datetime(tr["obs_date"])))
        elif inst.asset_class == "equity":
            method = "trailing_dividends"
            div = lake.read("corporate_actions", where=f"instrument = '{inst.id}' AND kind = 'dividend'")
            series = trailing_dividend_yield(div, price)
        elif inst.id in SYNTHETIC_YIELD:
            origin, method = "synthetic", "assumed_yield"
            series = pd.Series(SYNTHETIC_YIELD[inst.id], index=price.index)
        else:
            continue
        rows = pd.DataFrame({"underlying": inst.id, "tenor": 1.0, "method": method, "obs_date": series.index,
                             "carry": -series.to_numpy(), "discount": np.nan, "forward_ratio": np.nan})
        out.append(lake.ingest("carry_implied", rows, f"derive_{method}", origin, run))
    return out


def dividend_yield_at(lake: Lake, name: str, day: dt.date, as_of=None) -> tuple[float, str, str]:
    """(yield, origin, method): the latest estimate at or before `day` in carry_implied, tenor 1 year."""
    rows = lake.read("carry_implied", where=f"underlying = '{name}' AND tenor = 1.0 AND obs_date <= DATE '{day}' "
                     "AND method <> 'parity'", order="obs_date DESC LIMIT 1", as_of=as_of)
    if rows.empty:
        return SYNTHETIC_YIELD.get(name, 0.02), "synthetic", "assumed_yield"
    r = rows.iloc[0]
    return -float(r["carry"]), str(r["origin"]), str(r["method"])
