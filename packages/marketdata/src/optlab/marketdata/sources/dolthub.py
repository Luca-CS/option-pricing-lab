"""DoltHub `post-no-preference/options`, from a local clone read with the dolt CLI.

Short maturities only (3 to 4 expiries, about two months), an irregular calendar, no SPX. No licence was found: treated
as personal use, never versioned. Always filter by date and symbol: the table's key starts with them, and anything else
scans the whole database.
"""

from __future__ import annotations

import io
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from optlab.marketdata.lake import Lake, new_run_id

SOURCE = "dolthub"
SYMBOLS = (
    "SPY",
    "AAPL",
    "AMZN",
    "GOOGL",
    "GS",
    "IBM",
)  # mapped on 5 November 2026: no SPX, QQQ or IWM in this database


def parse_chain(content, symbols=None) -> pd.DataFrame:
    """option_quotes rows from `SELECT * FROM option_chain` as CSV."""
    df = pd.read_csv(io.BytesIO(content) if isinstance(content, bytes) else io.StringIO(content))
    if symbols:
        df = df[df["act_symbol"].isin(symbols)]
    return pd.DataFrame(
        {
            "underlying": df["act_symbol"],
            "root": df["act_symbol"],
            "expiry": pd.to_datetime(df["expiration"]).dt.date,
            "strike": df["strike"],
            "cp": df["call_put"].str[0].str.upper(),
            "obs_date": pd.to_datetime(df["date"]).dt.date,
            "bid": df["bid"],
            "ask": df["ask"],
            "iv": df["vol"],
            "delta": df["delta"],
            "gamma": df["gamma"],
            "theta": df["theta"],
            "vega": df["vega"],
            "settlement": "PM",
        }
    ).reset_index(drop=True)


def parse_vol_history(content) -> pd.DataFrame:
    """Daily 30-day implied volatility per symbol (`iv_current`, a fraction) as vol_indices rows named DOLT.<symbol>,
    in percent like the Cboe indices."""
    df = pd.read_csv(
        io.BytesIO(content) if isinstance(content, bytes) else io.StringIO(content),
        usecols=["date", "act_symbol", "iv_current"],
    ).dropna()
    return pd.DataFrame(
        {
            "index_name": "DOLT." + df["act_symbol"],
            "obs_date": pd.to_datetime(df["date"]).dt.date,
            "close": 100 * df["iv_current"],
        }
    ).reset_index(drop=True)


@dataclass(frozen=True)
class Dolt:
    executable: Path
    database: Path
    home: Path | None = None

    @classmethod
    def from_environment(cls, data, database=None) -> Dolt:
        """The Dolt binary and the options database under the data folder `data` (`Settings.data`)."""
        root = Path(data)
        db = Path(database) if database and database != "default" else root / "dolt" / "options"
        return cls(
            root / "tools" / "dolt" / "dolt-windows-amd64" / "bin" / "dolt.exe", db, root / "tools" / "dolt-home"
        )

    def sql(self, query: str, timeout: float = 1800.0) -> bytes:
        env = {**os.environ, **({"DOLT_ROOT_PATH": str(self.home)} if self.home else {})}
        out = subprocess.run(  # noqa: S603 (the dolt binary by its full path, fixed arguments, no shell)
            [str(self.executable), "sql", "-r", "csv", "-q", query],
            cwd=self.database,
            env=env,
            capture_output=True,
            timeout=timeout,
        )
        if out.returncode != 0:
            raise RuntimeError(out.stderr.decode(errors="replace")[:500])
        return out.stdout


def load_dolthub(lake: Lake, dolt: Dolt, symbols=SYMBOLS, start: str = "2019-01-01") -> list[dict]:
    """Chains and the 30-day implied volatility of the universe, year by year."""
    run = new_run_id(SOURCE)
    names = ", ".join(f"'{s}'" for s in symbols)
    out = []
    history = dolt.sql(
        f"SELECT date, act_symbol, iv_current FROM volatility_history WHERE act_symbol IN ({names}) "  # noqa: S608 (symbols and start from the code's defaults, never from a request)
        f"AND date >= '{start}'"
    )
    out.append(lake.ingest("vol_indices", parse_vol_history(history), SOURCE, "observed", run))
    for year in range(int(start[:4]), pd.Timestamp.now().year + 1):
        chain = dolt.sql(
            f"SELECT * FROM option_chain WHERE date >= '{year}-01-01' AND date <= '{year}-12-31' AND "  # noqa: S608 (symbols and start from the code's defaults, never from a request)
            f"act_symbol IN ({names})"
        )
        rows = parse_chain(chain, symbols)
        if len(rows):
            out.append(lake.ingest("option_quotes", rows, SOURCE, "observed", run))
    return out
