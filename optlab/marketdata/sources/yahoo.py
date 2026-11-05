"""Yahoo Finance through yfinance: end-of-day prices, dividends and splits, and the option chain of the day.

Personal use only. `auto_adjust=False`: closes are the traded levels (a fixing is a traded level), with the adjusted
close next to them. Today's close can be NaN on European indices and shares while the market is open: those rows are
dropped here and arrive with a later load.
"""

from __future__ import annotations

import datetime as dt
import warnings

import pandas as pd

PRICE_COLUMNS = ["instrument", "obs_date", "open", "high", "low", "close", "adj_close", "volume"]
ACTION_COLUMNS = ["instrument", "kind", "obs_date", "value"]


def parse_history(raw: pd.DataFrame, instrument: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(prices, corporate actions) from `history(actions=True, auto_adjust=False)`. The date is the market's local
    date: the time-zone-aware index is read as it is, never converted."""
    if raw.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS), pd.DataFrame(columns=ACTION_COLUMNS)
    days = [t.date() for t in raw.index]
    close = raw["Close"].to_numpy()
    prices = pd.DataFrame({"instrument": instrument, "obs_date": days, "open": raw["Open"].to_numpy(),
                           "high": raw["High"].to_numpy(), "low": raw["Low"].to_numpy(), "close": close,
                           "adj_close": raw["Adj Close"].to_numpy() if "Adj Close" in raw else close,
                           "volume": raw["Volume"].to_numpy()})
    prices = prices[prices["close"].notna() & (prices["close"] > 0)].reset_index(drop=True)
    actions = []
    for col, kind in (("Dividends", "dividend"), ("Stock Splits", "split")):
        if col in raw:
            values = raw[col].to_numpy()
            hit = values > 0
            if hit.any():
                actions.append(pd.DataFrame({"instrument": instrument, "kind": kind,
                                             "obs_date": [d for d, h in zip(days, hit) if h], "value": values[hit]}))
    return prices, (pd.concat(actions, ignore_index=True) if actions else pd.DataFrame(columns=ACTION_COLUMNS))


def download_history(ticker: str, start: str = "2015-01-01") -> pd.DataFrame:
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=True)


def parse_chain(raw: pd.DataFrame, underlying: str, obs_date: dt.date, spot: float) -> pd.DataFrame:
    """option_quotes rows from a chain frame (contractSymbol, strike, bid, ask, volume, openInterest,
    impliedVolatility, type, expiry). Monthly SPX contracts settle in the morning, SPXW and the rest at the close."""
    root = raw["contractSymbol"].str.extract(r"^([A-Z]+)\d")[0]
    return pd.DataFrame({
        "underlying": underlying, "root": root, "expiry": pd.to_datetime(raw["expiry"]).dt.date,
        "strike": raw["strike"], "cp": raw["type"].str[0].str.upper(), "obs_date": obs_date, "bid": raw["bid"],
        "ask": raw["ask"], "iv": raw.get("impliedVolatility"), "volume": raw.get("volume"),
        "open_interest": raw.get("openInterest"), "underlying_close": spot,
        "settlement": ["AM" if r == "SPX" else "PM" for r in root]})


def download_chain(ticker: str, min_days: int = 0, max_days: int = 1200) -> tuple[pd.DataFrame, float, dt.date]:
    """All expiries of the day as one frame: (frame, spot, market date of the snapshot)."""
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tk = yf.Ticker(ticker)
        last = tk.history(period="5d")
        spot, day = float(last["Close"].dropna().iloc[-1]), last["Close"].dropna().index[-1].date()
        rows = []
        for expiry in tk.options:
            if not min_days <= (dt.date.fromisoformat(expiry) - day).days <= max_days:
                continue
            chain = tk.option_chain(expiry)
            for kind, df in (("call", chain.calls), ("put", chain.puts)):
                keep = df[["contractSymbol", "strike", "bid", "ask", "volume", "openInterest",
                           "impliedVolatility"]].copy()
                keep["type"], keep["expiry"] = kind, expiry
                rows.append(keep)
    return pd.concat(rows, ignore_index=True), spot, day
