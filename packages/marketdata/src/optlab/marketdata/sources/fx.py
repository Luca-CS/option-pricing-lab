"""FX spots from two public sources, both pure parsers: the ECB's reference rates and the Fed's H.10 noon rates through FRED.

A pair is written base then quote, and its rate is units of quote per unit of base (EURUSD = dollars per euro), which is the
domestic-per-foreign convention of `carry.cip_forward`.

The ECB fixes one rate per currency against the euro at about 14:15 CET, so EURUSD is observed and USDJPY and GBPUSD are crosses
of two rates of the same fix (origin = derived). H.10 quotes each pair directly at noon in New York (18:00 CET), a week or so late:
it is kept as an independent check. Neither is synchronous with the close of an index, which the realised correlation has to allow for.
"""

from __future__ import annotations

import io

import pandas as pd
from optlab.marketdata.sources.simple import _text, parse_fred

ECB_CURRENCIES = ("USD", "JPY", "GBP")
FRED_PAIRS = {"EURUSD": "DEXUSEU", "USDJPY": "DEXJPUS", "GBPUSD": "DEXUSUK"}
PLAUSIBLE = {
    "EURUSD": (0.7, 1.8),
    "USDJPY": (60.0, 300.0),
    "GBPUSD": (0.8, 2.5),
    "EURJPY": (60.0, 300.0),
    "EURGBP": (0.4, 1.3),
}


def _in_range(rows: pd.DataFrame, pair: str, column: str) -> pd.DataFrame:
    """A rate far outside any level seen since 1999 means a misread file, not a market move: stop the load."""
    low, high = PLAUSIBLE[pair]
    bad = rows[(rows[column] <= low) | (rows[column] >= high)]
    if len(bad):
        raise ValueError(
            f"{pair}: {len(bad)} rates outside [{low}, {high}], first {bad[column].iloc[0]:g} on {bad['obs_date'].iloc[0]}"
        )
    return rows


def parse_ecb_fx(content, currency: str) -> pd.DataFrame:
    """obs_date, value: units of `currency` per euro. Refuses a file holding another series; a day without a rate is dropped."""
    text = _text(content)
    if text.lstrip().startswith("<"):
        raise ValueError("the ECB answered with an HTML page, not a CSV")
    df = pd.read_csv(io.StringIO(text), usecols=["CURRENCY", "CURRENCY_DENOM", "TIME_PERIOD", "OBS_VALUE"])
    held = set(zip(df["CURRENCY"], df["CURRENCY_DENOM"]))
    if held - {(currency, "EUR")}:
        raise ValueError(f"expected {currency} per EUR, the file holds {sorted(held)}")
    out = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(df["TIME_PERIOD"], errors="coerce"),
            "value": pd.to_numeric(df["OBS_VALUE"], errors="coerce"),
        }
    ).dropna()
    out = out[out["value"] > 0]
    out["obs_date"] = out["obs_date"].dt.date
    out = out.reset_index(drop=True)
    return _in_range(out, f"EUR{currency}", "value")


def ecb_pairs(per_euro: dict) -> pd.DataFrame:
    """pair, obs_date, rate, origin from {currency: parse_ecb_fx(...)}. A cross needs both of its rates on the day."""
    usd = per_euro["USD"].rename(columns={"value": "usd"})
    jpy = usd.merge(per_euro["JPY"].rename(columns={"value": "jpy"}), on="obs_date")
    gbp = usd.merge(per_euro["GBP"].rename(columns={"value": "gbp"}), on="obs_date")
    rows = [
        pd.DataFrame({"pair": "EURUSD", "obs_date": usd["obs_date"], "rate": usd["usd"], "origin": "observed"}),
        pd.DataFrame(
            {"pair": "USDJPY", "obs_date": jpy["obs_date"], "rate": jpy["jpy"] / jpy["usd"], "origin": "derived"}
        ),
        pd.DataFrame(
            {"pair": "GBPUSD", "obs_date": gbp["obs_date"], "rate": gbp["usd"] / gbp["gbp"], "origin": "derived"}
        ),
    ]
    return pd.concat([_in_range(r, r["pair"].iloc[0], "rate") for r in rows if len(r)], ignore_index=True)


def parse_fred_fx(content, pair: str) -> pd.DataFrame:
    """pair, obs_date, rate from a FRED H.10 file, already quoted in the pair's convention."""
    rows = parse_fred(content, FRED_PAIRS[pair]).rename(columns={"value": "rate"})
    return _in_range(rows[rows["rate"] > 0], pair, "rate").assign(pair=pair)
