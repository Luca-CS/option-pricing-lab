"""The public CSV sources: Cboe, STOXX, FRED, US Treasury and ECB. One `*_url` and one pure `parse_*` per source."""

from __future__ import annotations

import io
import re

import pandas as pd

# --- Cboe: MM/DD/YYYY; most files OHLC, VVIX and SKEW have DATE and one value column ---
CBOE_VOL = ("VIX", "VIX9D", "VIX3M", "VIX6M", "VIX1Y", "VIX1D", "SKEW", "VVIX", "DSPX", "VIXEQ", "VXN", "RVX", "VXD",
            "VXAPL", "VXAZN", "VXGOG", "VXGS", "VXIBM")
CBOE_CORR = {"COR1M": 1 / 12, "COR3M": 0.25, "COR6M": 0.5, "COR1Y": 1.0}
CBOE_FIRST_VALID = {"VVIX": "2006-06-01"}  # the first rows are not usable levels (15.71 on 15/03/2006)


def cboe_url(symbol: str) -> str:
    return f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"


def _text(content) -> str:
    return content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content


def parse_cboe(content, symbol: str) -> pd.DataFrame:
    """obs_date, open, high, low, close; the first three stay missing in one-value files."""
    df = pd.read_csv(io.StringIO(_text(content)))
    df.columns = [c.strip().upper() for c in df.columns]
    dates = pd.to_datetime(df["DATE"], format="%m/%d/%Y", errors="coerce")
    others = [c for c in df.columns if c not in ("DATE", "OPEN", "HIGH", "LOW", "CLOSE")]
    if "CLOSE" not in df.columns and not others:
        raise ValueError(f"{symbol}: no value column in {list(df.columns)}")
    out = pd.DataFrame({"obs_date": dates})
    for col in ("OPEN", "HIGH", "LOW"):
        out[col.lower()] = pd.to_numeric(df[col], errors="coerce") if col in df.columns else float("nan")
    out["close"] = pd.to_numeric(df["CLOSE"] if "CLOSE" in df.columns else df[others[0]], errors="coerce")
    out = out.dropna(subset=["obs_date"])
    out = out[out["close"] > 0]
    if symbol in CBOE_FIRST_VALID:
        out = out[out["obs_date"] >= CBOE_FIRST_VALID[symbol]]
    out["obs_date"] = out["obs_date"].dt.date
    return out.reset_index(drop=True)


# --- STOXX: `Date;Symbol;Indexvalue`, DD.MM.YYYY ---
V2TX_URL = "https://www.stoxx.com/document/Indices/Current/HistoricalData/h_v2tx.txt"


def parse_stoxx(content, symbol: str = "V2TX") -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(_text(content)), sep=";", engine="python", usecols=[0, 1, 2],
                     names=["date", "symbol", "value"], header=0)
    dates = pd.to_datetime(df["date"].astype(str).str.strip(), format="%d.%m.%Y", errors="coerce")
    out = pd.DataFrame({"obs_date": dates, "close": pd.to_numeric(df["value"], errors="coerce"),
                        "symbol": df["symbol"].astype(str).str.strip()})
    out = out[(out["symbol"] == symbol) & out["close"].notna() & out["obs_date"].notna()]
    out["obs_date"] = out["obs_date"].dt.date
    return out[["obs_date", "close"]].reset_index(drop=True)


# --- FRED public graph CSV (no key): `observation_date,SERIES`, empty when not published ---
# Series -> (curve, tenor in years), quoted in percent per year. Not used: ICE BofA credit (truncated to three years and
# under copyright).
FRED_RATES = {"DFF": ("USD.FF", 1 / 365), "SOFR": ("USD.SOFR", 1 / 365), "ECBESTRVOLWGTTRMDMNRT": ("EUR.ESTR", 1 / 365),
              "DGS10": ("USD.CMT10", 10.0), "DBAA": ("USD.BAA", 20.0), "DAAA": ("USD.AAA", 20.0)}


def fred_url(series: str) -> str:
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


def parse_fred(content, series: str) -> pd.DataFrame:
    """obs_date, value (missing values dropped)."""
    df = pd.read_csv(io.StringIO(_text(content)))
    if series not in df.columns:
        raise ValueError(f"{series}: columns {list(df.columns)}")
    out = pd.DataFrame({"obs_date": pd.to_datetime(df[df.columns[0]], errors="coerce"),
                        "value": pd.to_numeric(df[series], errors="coerce")}).dropna()
    out["obs_date"] = out["obs_date"].dt.date
    return out.reset_index(drop=True)


# --- US Treasury par curve: one CSV per year, MM/DD/YYYY, percent, some tenors empty in early years ---
TREASURY_CURVE = "USD.TREASURY"


def treasury_url(year: int) -> str:
    return ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
            f"{year}/all?type=daily_treasury_yield_curve&_format=csv")


def treasury_tenor(label: str) -> float | None:
    m = re.fullmatch(r"\s*([\d.]+)\s*(Mo|Month|Yr|Year)s?\s*", label)
    if not m:
        return None
    return float(m[1]) if m[2].startswith("Y") else float(m[1]) / 12


def parse_treasury(content) -> pd.DataFrame:
    """Long format: obs_date, tenor (years), rate (percent)."""
    df = pd.read_csv(io.StringIO(_text(content)))
    dates = pd.to_datetime(df["Date"], format="%m/%d/%Y", errors="coerce")
    parts = []
    for col in df.columns:
        tenor = treasury_tenor(col)
        if tenor is not None:
            parts.append(pd.DataFrame({"obs_date": dates, "tenor": tenor,
                                       "rate": pd.to_numeric(df[col], errors="coerce")}).dropna())
    out = pd.concat(parts, ignore_index=True)
    out["obs_date"] = out["obs_date"].dt.date
    return out


# --- ECB Data Portal: AAA euro curve (spot rates, YC) and reference rates (EXR) ---
ECB_CURVE = "EUR.AAA"
ECB_TENORS = {"3M": 0.25, "6M": 0.5, "1Y": 1.0, "2Y": 2.0, "3Y": 3.0, "5Y": 5.0, "7Y": 7.0, "10Y": 10.0, "20Y": 20.0}
ECB_HEADERS = {"Accept": "text/csv"}


def ecb_yc_url(tenor: str, start: str = "2015-01-01") -> str:
    return ("https://data-api.ecb.europa.eu/service/data/YC/"
            f"B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{tenor}?format=csvdata&startPeriod={start}")


def ecb_fx_url(currency: str = "USD", start: str = "2015-01-01") -> str:
    return f"https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A?format=csvdata&startPeriod={start}"


def parse_ecb(content) -> pd.DataFrame:
    """obs_date, value (percent for YC, units of the currency per euro for EXR)."""
    text = _text(content)
    if text.lstrip().startswith("<"):
        raise ValueError("the ECB answered with an HTML page, not a CSV")
    df = pd.read_csv(io.StringIO(text), usecols=["TIME_PERIOD", "OBS_VALUE"])
    out = pd.DataFrame({"obs_date": pd.to_datetime(df["TIME_PERIOD"], errors="coerce"),
                        "value": pd.to_numeric(df["OBS_VALUE"], errors="coerce")}).dropna()
    out["obs_date"] = out["obs_date"].dt.date
    return out.reset_index(drop=True)
