"""Loads of the public sources into the lake: download, keep the raw file, parse, append what is new.

One failing source does not stop the others: it is reported in the summary and in quality_issues, and the next load
tries again. `fetch` is injected so that the tests run offline on small fixtures.
"""

from __future__ import annotations

import datetime as dt
import io
import time

import pandas as pd

from ..log import get_logger
from .lake import Lake, new_run_id
from .sources import curl_get, fx, http_get, rates, simple, yahoo
from .universe import REFERENCE_INSTRUMENT, as_rows, enabled_underlyings

LOG = get_logger(__name__)
START = "2015-01-01"  # the replay period starts in 2018; earlier history feeds rolling windows
MIN_TWO_SIDED = 0.3


def load_cboe(lake: Lake, fetch=http_get) -> list[dict]:
    out = []
    for symbol in simple.CBOE_VOL:
        raw = lake.store_raw("cboe", f"{symbol}_History.csv", fetch(simple.cboe_url(symbol)), simple.cboe_url(symbol))
        rows = simple.parse_cboe(raw.content, symbol).assign(index_name=symbol)
        out.append(lake.ingest("vol_indices", rows, "cboe", "observed", raw_sha256=raw.sha256))
    for symbol, tenor in simple.CBOE_CORR.items():
        raw = lake.store_raw("cboe", f"{symbol}_History.csv", fetch(simple.cboe_url(symbol)), simple.cboe_url(symbol))
        rows = simple.parse_cboe(raw.content, symbol)
        rows = rows.assign(index_name=symbol, tenor=tenor, correlation=rows["close"] / 100.0)
        out.append(lake.ingest("correl_implied", rows, "cboe", "observed", raw_sha256=raw.sha256))
    return out


def load_stoxx(lake: Lake, fetch=http_get) -> list[dict]:
    raw = lake.store_raw("stoxx", "h_v2tx.txt", fetch(simple.V2TX_URL), simple.V2TX_URL)
    rows = simple.parse_stoxx(raw.content).assign(index_name="V2TX")
    return [lake.ingest("vol_indices", rows, "stoxx", "observed", raw_sha256=raw.sha256)]


def load_fred(lake: Lake, fetch=curl_get, pause=lambda: time.sleep(3.0)) -> list[dict]:
    out = []
    for series, (curve, tenor) in simple.FRED_RATES.items():
        url = simple.fred_url(series, START)
        raw = lake.store_raw("fred", f"{series}.csv", fetch(url), url)
        pause()  # FRED drops the connection on quick series of requests
        rows = simple.parse_fred(raw.content, series).rename(columns={"value": "rate"}).assign(curve=curve, tenor=tenor)
        rows = rows[pd.to_datetime(rows["obs_date"]) >= START]
        out.append(lake.ingest("rate_points", rows, "fred", "observed", raw_sha256=raw.sha256))
    return out


def load_treasury(lake: Lake, fetch=http_get, start_year: int = 2015, end_year: int | None = None) -> list[dict]:
    out = []
    for year in range(start_year, (end_year or dt.date.today().year) + 1):
        url = simple.treasury_url(year)
        raw = lake.store_raw("treasury", f"par_{year}.csv", fetch(url), url)
        rows = simple.parse_treasury(raw.content).assign(curve=simple.TREASURY_CURVE)
        out.append(lake.ingest("rate_points", rows, "treasury", "observed", raw_sha256=raw.sha256))
    return out


def load_ecb(lake: Lake, fetch=http_get) -> list[dict]:
    out = []
    for label, tenor in simple.ECB_TENORS.items():
        url = simple.ecb_yc_url(label, START)
        raw = lake.store_raw("ecb", f"YC_{label}.csv", fetch(url, headers=simple.ECB_HEADERS), url)
        rows = simple.parse_ecb(raw.content).rename(columns={"value": "rate"}).assign(curve=simple.ECB_CURVE, tenor=tenor)
        out.append(lake.ingest("rate_points", rows, "ecb", "observed", raw_sha256=raw.sha256))
    return out


def load_fx(lake: Lake, fetch=http_get, fred_fetch=curl_get, pause=lambda: time.sleep(3.0)) -> list[dict]:
    """The ECB's reference rates (EURUSD observed, the crosses derived), then the H.10 rates kept as an independent check."""
    out, per_euro, hashes = [], {}, []
    for currency in fx.ECB_CURRENCIES:
        url = simple.ecb_fx_url(currency, START)
        raw = lake.store_raw("ecb", f"EXR_{currency}.csv", fetch(url, headers=simple.ECB_HEADERS), url)
        per_euro[currency] = fx.parse_ecb_fx(raw.content, currency)
        hashes.append(raw.sha256)
    rows = fx.ecb_pairs(per_euro)
    for origin, group in rows.groupby("origin"):
        out.append(lake.ingest("fx_spots", group, "ecb", origin, raw_sha256=",".join(hashes)))
    for pair, series in fx.FRED_PAIRS.items():
        url = simple.fred_url(series, START)
        raw = lake.store_raw("fred", f"{series}.csv", fred_fetch(url), url)
        pause()  # FRED drops the connection on quick series of requests
        out.append(lake.ingest("fx_spots", fx.parse_fred_fx(raw.content, pair), "fred", "observed", raw_sha256=raw.sha256))
    return out


def _since_start(rows: pd.DataFrame) -> pd.DataFrame:
    return rows[pd.to_datetime(rows["obs_date"]) >= START]


def load_mof(lake: Lake, fetch=http_get) -> list[dict]:
    """JGB par yields: the history, which stops at the end of the last month, then the current month."""
    out = []
    for name, url in (("jgbcme_all.csv", rates.MOF_HISTORY_URL), ("jgbcme.csv", rates.MOF_CURRENT_URL)):
        raw = lake.store_raw("mof", name, fetch(url), url)
        rows = _since_start(rates.parse_mof_jgb(raw.content)).assign(curve=rates.JGB_CURVE)
        out.append(lake.ingest("rate_points", rows, "mof", "observed", raw_sha256=raw.sha256))
    return out


def load_boj(lake: Lake, fetch=http_get) -> list[dict]:
    url = rates.boj_call_url(START[:4] + START[5:7])
    raw = lake.store_raw("boj", "call_rate.csv", fetch(url), url)
    rows = _since_start(rates.parse_boj_call(raw.content)).rename(columns={"value": "rate"}).assign(curve=rates.CALL_CURVE, tenor=1 / 365)
    return [lake.ingest("rate_points", rows, "boj", "observed", raw_sha256=raw.sha256)]


def load_boe(lake: Lake, fetch=http_get) -> list[dict]:
    """The gilt curve, history then current month, and SONIA. Each workbook is kept as its own raw file, not the zip: a zip of 39 MB
    changes every month, while the workbooks of past years do not."""
    out = []
    for url in (rates.GLC_HISTORY_URL, rates.GLC_CURRENT_URL):
        for name, book in rates.glc_workbooks(fetch(url), int(START[:4])).items():
            raw = lake.store_raw("boe", name, book, url)
            rows = _since_start(rates.parse_glc_workbook(raw.content)).assign(curve=rates.GLC_CURVE)
            out.append(lake.ingest("rate_points", rows, "boe", "observed", raw_sha256=raw.sha256))
    url = rates.boe_series_url()
    raw = lake.store_raw("boe", f"{rates.SONIA_SERIES}.csv", fetch(url), url)
    rows = _since_start(rates.parse_boe_series(raw.content)).rename(columns={"value": "rate"}).assign(curve=rates.SONIA_CURVE, tenor=1 / 365)
    out.append(lake.ingest("rate_points", rows, "boe", "observed", raw_sha256=raw.sha256))
    return out


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_parquet(buf)
    return buf.getvalue()


def load_yahoo(lake: Lake, download=yahoo.download_history, instruments=None) -> list[dict]:
    """Prices and corporate actions of the enabled names, plus the total-return indices kept as their own series."""
    out = []
    for inst in instruments if instruments is not None else enabled_underlyings():
        for name, ticker in ((inst.id, inst.yahoo), (f"{inst.id}.TR", inst.total_return)):
            if not ticker:
                continue
            history = download(ticker, START)
            raw = lake.store_raw("yahoo", f"{name}.parquet", _parquet_bytes(history), f"yahoo:{ticker}")
            prices, actions = yahoo.parse_history(history, name)
            out.append(lake.ingest("prices_eod", prices, "yahoo", "observed", raw_sha256=raw.sha256))
            if len(actions):
                out.append(lake.ingest("corporate_actions", actions, "yahoo", "observed", raw_sha256=raw.sha256))
    return out


def load_universe(lake: Lake, as_of=None) -> dict:
    return lake.ingest("instruments", as_rows(as_of or dt.date(2018, 1, 1)), "registry", "observed")


def load_calendars(lake: Lake) -> list[dict]:
    """Per place, the weekdays traded by its reference instrument: open when a close exists, closed otherwise."""
    out = []
    for place, name in REFERENCE_INSTRUMENT.items():
        days = lake.read("prices_eod", where=f"instrument = '{name}' AND source = 'yahoo'", columns="obs_date")["obs_date"]
        if days.empty:
            continue
        days = pd.to_datetime(days)
        weekdays = pd.bdate_range(days.min(), days.max())
        rows = pd.DataFrame({"place": place, "obs_date": weekdays, "is_open": weekdays.isin(days)})
        out.append(lake.ingest("calendars", rows, "derived_prices", "derived"))
    return out


def load_yahoo_chains(lake: Lake, names=("SPX", "NDX", "RUT", "AAPL"), tickers=None, download=yahoo.download_chain) -> list[dict]:
    """One snapshot of the day's chains, on request only: Luca chose not to collect them every day."""
    tickers = tickers or {"SPX": "^SPX", "NDX": "^NDX", "RUT": "^RUT"}
    out = []
    for name in names:
        ticker = tickers.get(name, name)
        raw_frame, spot, day = download(ticker)
        raw = lake.store_raw("yahoo_chain", f"{name}_{day}.parquet", _parquet_bytes(raw_frame), f"yahoo:{ticker}")
        rows = yahoo.parse_chain(raw_frame, name, day, spot)
        two_sided = float(((rows["bid"] > 0) & (rows["ask"] > rows["bid"])).mean())
        quality = "ok" if two_sided >= MIN_TWO_SIDED else "suspect"
        if quality == "suspect":  # Yahoo blanks bid and ask outside US market hours
            issue = pd.DataFrame([{"dataset": "option_quotes", "item": name, "check_name": "market_closed", "severity": "warning",
                                   "detail": f"{two_sided:.1%} two-sided quotes: taken outside US market hours", "obs_date": day}])
            lake.ingest("quality_issues", issue, "ingest", "derived", new_run_id("ingest"))
        out.append({**lake.ingest("option_quotes", rows, "yahoo", "observed", quality=quality, raw_sha256=raw.sha256),
                    "two_sided": two_sided})
    return out


STEPS = {"universe": load_universe, "cboe": load_cboe, "stoxx": load_stoxx, "fred": load_fred,
         "treasury": load_treasury, "ecb": load_ecb, "mof": load_mof, "boj": load_boj, "boe": load_boe, "fx": load_fx, "yahoo": load_yahoo,
         "calendars": load_calendars}


def update_all(lake: Lake, only=None, log=LOG.info) -> dict:
    """Run the loads; returns {step: summaries or the error message}."""
    report = {}
    for name, step in STEPS.items():
        if only and name not in only:
            continue
        t0 = dt.datetime.now()
        try:
            result = step(lake)
            rows = result if isinstance(result, list) else [result]
            report[name] = {"ok": True, "new": sum(r["n_new"] for r in rows), "changed": sum(r["n_changed"] for r in rows),
                            "input": sum(r["n_input"] for r in rows), "seconds": (dt.datetime.now() - t0).total_seconds()}
        except Exception as exc:  # a source down is a report line, not a failed load
            report[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            issue = pd.DataFrame([{"dataset": name, "item": name, "check_name": "load_failed", "severity": "error",
                                   "detail": report[name]["error"][:500], "obs_date": dt.date.today()}])
            lake.ingest("quality_issues", issue, "ingest", "derived", new_run_id("ingest"))
        log(f"{name}: {report[name]}")
    return report

