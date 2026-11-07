"""Forwards and carry: covered interest parity for FX, cost of carry for shares, put-call parity where chains exist.

No free source gives repo or index dividends. Where chains exist, parity gives a net carry, which is not split into
dividend and repo for an index. A share's repo is what remains after its known dividends. Elsewhere the carry is an
estimate from the past (the realised yield of a total-return index, trailing dividends) or a labelled assumption.
Everything used at a date is known at that date: future dividends are never read.

A market's forward uses a dividend stream: the dividends of the past year in points, as a share of today's spot, repeated
on the same dates of the years to come. So the curve has its steps and its season, and a fall of the spot raises the yield at
once, as it does for dividends that were already announced. A yield averaged over the past year does neither.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from optlab.core.instruments import instrument
from optlab.marketdata.lake import Lake, new_run_id, sql_str

# Assumed dividend yields of indices with neither chains nor a total-return index (origin = synthetic).
SYNTHETIC_YIELD = {"NDX": 0.008, "SX5E": 0.03}
# Assumed share of the year's dividends per month, January first. European dividends fall in spring; no source gives this.
ASSUMED_SEASON = {"SX5E": (0.03, 0.03, 0.03, 0.20, 0.30, 0.25, 0.02, 0.03, 0.03, 0.03, 0.03, 0.02)}
EVEN_SEASON = (1 / 12,) * 12
STREAM_YEARS = 8
MIN_HISTORY_DAYS = 300  # less than that does not show a year of dividends


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


def parity_carry_rows(
    quotes: pd.DataFrame, discount, spot: float, band: float = 0.05, min_pairs: int = 3
) -> pd.DataFrame:
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
        rows.append(
            {"obs_date": day, "tenor": T, "discount": D, "forward_ratio": F / spot, "carry": np.log(F / spot) / T}
        )
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
            tr = lake.read(
                "prices_eod", where=f"instrument = '{inst.id}.TR'", columns="obs_date, close", order="obs_date"
            )
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
        rows = pd.DataFrame(
            {
                "underlying": inst.id,
                "tenor": 1.0,
                "method": method,
                "obs_date": series.index,
                "carry": -series.to_numpy(),
                "discount": np.nan,
                "forward_ratio": np.nan,
            }
        )
        out.append(lake.ingest("carry_implied", rows, f"derive_{method}", origin, run))
    return out


def build_parity_carry(lake: Lake, name: str, source: str, days=None, currency: str = "USD") -> dict:
    """Net carry per expiry from put-call parity, on the days with a chain: an index's carry is not split into dividend and
    repo. Stored next to the estimates from the past, which `dividend_yield_at` keeps using for a homogeneous history."""
    from optlab.marketdata.quotes import chain_rows, discount_function, quote_days

    rows = []
    for day in days if days is not None else quote_days(lake, name, source):
        chain = chain_rows(lake, name, day, source)
        closes = pd.to_numeric(chain["underlying_close"], errors="coerce").dropna()
        if chain.empty or closes.empty:
            continue
        carry = parity_carry_rows(
            chain[chain["root"] != "SPX"], discount_function(lake, currency, day), float(closes.iloc[0])
        )
        rows.append(carry.assign(underlying=name, method="parity"))
    if not rows:
        return {"n_new": 0, "n_changed": 0, "n_input": 0}
    return lake.ingest(
        "carry_implied",
        pd.concat(rows, ignore_index=True),
        f"parity_on_{source}",
        "derived",
        new_run_id("derive_parity"),
    )


@dataclass(frozen=True)
class DividendStream:
    """Dividends to come as shares of today's spot: `times` in years from the date, `shares` paid at each."""

    times: np.ndarray
    shares: np.ndarray
    origin: str
    method: str

    @property
    def annual_yield(self) -> float:
        return float(self.shares[self.times <= 1.001].sum())  # a dividend of today comes back in 365.25 days

    def factor(self, T) -> np.ndarray:
        """What is left of a unit of spot at T once the dividends paid by then are gone."""
        T = np.atleast_1d(np.asarray(T, dtype=float))
        if not len(self.times):
            return np.ones_like(T)
        order = np.argsort(self.times)
        cumulated = np.concatenate([[0.0], np.cumsum(np.log1p(-self.shares[order]))])
        return np.exp(cumulated[np.searchsorted(self.times[order], T, side="right")])


def _repeat_year(day: dt.date, dates, shares, origin: str, method: str, years: int = STREAM_YEARS) -> DividendStream:
    times, out = [], []
    for n in range(1, years + 1):
        for d, s in zip(dates, shares):
            times.append(((pd.Timestamp(d) - pd.Timestamp(day)).days + 365.25 * n) / 365.0)
            out.append(float(s))
    return DividendStream(np.array(times, dtype=float), np.array(out, dtype=float), origin, method)


def _monthly_stream(day: dt.date, annual: float, season, origin: str, method: str) -> DividendStream:
    """A year's yield on the middle of the months of the coming years, from a season."""
    first = pd.Timestamp(day).replace(day=15) - pd.DateOffset(years=1)
    months = pd.date_range(first, periods=12, freq=pd.DateOffset(months=1))
    months = [m if m > pd.Timestamp(day) - pd.DateOffset(years=1) else m + pd.DateOffset(years=1) for m in months]
    return _repeat_year(day, months, [annual * season[m.month - 1] for m in months], origin, method)


def dividend_stream(lake: Lake, name: str, day, spot: float, as_of=None) -> DividendStream:
    """The dividends to come of a name, from what is known at `day`. A total-return index gives its dividends in points day by
    day, a share its ex-dates; other names have an assumed yield, with its season."""
    day = pd.Timestamp(day).date()
    inst = instrument(name)
    start = day - dt.timedelta(days=365)
    window = f"obs_date > DATE '{start}' AND obs_date <= DATE '{day}'"
    if inst.total_return:
        rows = lake.read(
            "prices_eod",
            where=f"instrument IN ({sql_str(name)}, {sql_str(name + '.TR')}) AND {window}",
            columns="instrument, obs_date, close",
            as_of=as_of,
        )
        wide = (
            rows.pivot_table(index="obs_date", columns="instrument", values="close").sort_index().dropna()
            if len(rows)
            else None
        )
        if wide is not None and name + ".TR" in wide.columns and len(wide) > 1:
            wide.index = pd.to_datetime(wide.index)
            if (wide.index[-1] - wide.index[0]).days >= MIN_HISTORY_DAYS:
                points = wide[name] * (np.exp(np.log(wide[name + ".TR"]).diff() - np.log(wide[name]).diff()) - 1.0)
                months = points.dropna().groupby(pd.Grouper(freq="ME")).sum().clip(lower=0.0)
                dates = [min(d, pd.Timestamp(day)) for d in months.index]
                return _repeat_year(day, dates, months.to_numpy() / spot, "derived", "dividend_points")
    elif inst.asset_class == "equity":
        rows = lake.read(
            "corporate_actions",
            where=f"instrument = {sql_str(name)} AND kind = 'dividend' AND {window}",
            columns="obs_date, value",
            as_of=as_of,
        )
        return _repeat_year(
            day, rows["obs_date"], rows["value"].to_numpy(dtype=float) / spot, "derived", "dividend_dates"
        )
    annual, origin, method = dividend_yield_at(lake, name, day, as_of)
    season = ASSUMED_SEASON.get(name, EVEN_SEASON)
    return _monthly_stream(day, annual, season, origin, "assumed_season" if name in ASSUMED_SEASON else method)


def dividend_yield_at(lake: Lake, name: str, day: dt.date, as_of=None) -> tuple[float, str, str]:
    """(yield, origin, method): the latest estimate at or before `day` in carry_implied, tenor 1 year."""
    rows = lake.read(
        "carry_implied",
        where=f"underlying = {sql_str(name)} AND tenor = 1.0 AND obs_date <= DATE '{day}' AND method <> 'parity'",
        order="obs_date DESC LIMIT 1",
        as_of=as_of,
    )
    if rows.empty:
        return SYNTHETIC_YIELD.get(name, 0.02), "synthetic", "assumed_yield"
    r = rows.iloc[0]
    return -float(r["carry"]), str(r["origin"]), str(r["method"])
