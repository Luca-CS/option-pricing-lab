"""Importers of option archives dropped in the local inbox: the historicaldata.net sample and OptionsDX files.

The historicaldata.net sample may be evaluated without a licence, without redistribution: nothing from it goes into the
repository, into docs/ or into a report except statistics. OptionsDX and Kaggle files need Luca's accounts. The OptionsDX
parser follows its published column names and has not been run on a real file yet.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from pathlib import Path

import pandas as pd

from ..lake import Lake, new_run_id

HD_SOURCE, OPTIONSDX_SOURCE = "historicaldata", "optionsdx"
# The archive reports SPXW under its own underlying; everything on the S&P 500 index is stored under SPX.
HD_UNDERLYING = {"SPX": "SPX", "SPXW": "SPX", "SPY": "SPY", "TSLA": "TSLA", "KO": "KO"}


def parse_historicaldata(content, keep=("SPX", "SPXW", "SPY")) -> pd.DataFrame:
    """option_quotes rows from one daily CSV of the 34-column format. A missing side stays NaN."""
    columns = ["contract", "underlying", "expiration", "type", "strike", "quote_date", "bid", "ask", "volume",
               "open_interest", "underlying_close", "settlement_time", "iv", "delta", "gamma", "theta", "vega"]
    df = pd.read_csv(io.BytesIO(content) if isinstance(content, bytes) else io.StringIO(content), usecols=columns)
    df = df[df["underlying"].isin(keep)]
    return pd.DataFrame({
        "underlying": df["underlying"].map(HD_UNDERLYING), "root": df["underlying"],
        "expiry": pd.to_datetime(df["expiration"]).dt.date, "strike": df["strike"],
        "cp": df["type"].str[0].str.upper(), "obs_date": pd.to_datetime(df["quote_date"]).dt.date, "bid": df["bid"],
        "ask": df["ask"], "iv": df["iv"], "delta": df["delta"], "gamma": df["gamma"], "theta": df["theta"],
        "vega": df["vega"], "volume": df["volume"], "open_interest": df["open_interest"],
        "underlying_close": df["underlying_close"], "settlement": df["settlement_time"]}).reset_index(drop=True)


def import_historicaldata(lake: Lake, zip_path, keep=("SPX", "SPXW", "SPY")) -> list[dict]:
    """Load the daily files of the sample. The zip stays in the inbox: the lake's manifest records its hash."""
    zip_path = Path(zip_path)
    run = new_run_id(HD_SOURCE)
    out = []
    with zipfile.ZipFile(zip_path) as z:
        for name in sorted(n for n in z.namelist() if n.endswith("_options.csv")):
            rows = parse_historicaldata(z.read(name), keep)
            if len(rows):
                out.append(lake.ingest("option_quotes", rows, HD_SOURCE, "observed", run))
    return out


def parse_optionsdx(content, underlying: str) -> pd.DataFrame:
    """OptionsDX end-of-day files: one row per strike and expiry, with the call and the put side by side. Their
    headers look like ` [C_BID]`. Quote and expiry dates are ISO."""
    df = pd.read_csv(io.BytesIO(content) if isinstance(content, bytes) else io.StringIO(content), skipinitialspace=True)
    df.columns = [c.strip().strip("[]") for c in df.columns]
    rows = []
    for cp, side in (("C", "C_"), ("P", "P_")):
        rows.append(pd.DataFrame({
            "underlying": underlying, "root": underlying, "expiry": pd.to_datetime(df["EXPIRE_DATE"]).dt.date,
            "strike": pd.to_numeric(df["STRIKE"], errors="coerce"), "cp": cp,
            "obs_date": pd.to_datetime(df["QUOTE_DATE"]).dt.date,
            "bid": pd.to_numeric(df[f"{side}BID"], errors="coerce"), "ask": pd.to_numeric(df[f"{side}ASK"], errors="coerce"),
            "iv": pd.to_numeric(df[f"{side}IV"], errors="coerce"), "delta": pd.to_numeric(df[f"{side}DELTA"], errors="coerce"),
            "volume": pd.to_numeric(df[f"{side}VOLUME"], errors="coerce"),
            "underlying_close": pd.to_numeric(df["UNDERLYING_LAST"], errors="coerce"), "settlement": "PM"}))
    return pd.concat(rows, ignore_index=True)


def import_optionsdx(lake: Lake, folder, underlying: str = "SPX") -> list[dict]:
    """Load every .txt or .csv file of an inbox folder."""
    run = new_run_id(OPTIONSDX_SOURCE)
    out = []
    for path in sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in (".txt", ".csv")):
        out.append(lake.ingest("option_quotes", parse_optionsdx(path.read_bytes(), underlying), OPTIONSDX_SOURCE,
                               "observed", run))
    return out


def snapshot_time(day) -> dt.datetime:
    """End-of-day files are read at the 16:00 New York close."""
    from zoneinfo import ZoneInfo
    d = pd.Timestamp(day).date()
    return dt.datetime.combine(d, dt.time(16, 0), tzinfo=ZoneInfo("America/New_York"))
