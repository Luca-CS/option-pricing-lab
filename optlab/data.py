"""SPX option chain from Yahoo Finance (via yfinance) and its cleaning into an implied-volatility table.

Yahoo's data are for personal use: snapshots are written to data/raw/ (ignored by git) and never
published. Only calibrated parameters and charts leave the machine.

Cleaning, maturity by maturity:
1. keep SPXW contracts (PM-settled at 16:00 New York), or the monthly SPX contracts (AM-settled,
   taken at 09:30) for expiries without SPXW; two-sided quotes, relative spread below 50 %;
2. imply the discount factor D and the forward F from put-call parity, C - P = D (F - K), by a
   robust line through the strikes within 5 % of spot that have both a call and a put quote;
3. smooth the discount curve: a zero rate linear in T is fitted to the parity D of the maturities
   beyond two months (short ones carry almost no information on rates), then each forward is
   re-implied from parity with the smoothed D;
4. keep the out-of-the-money side (puts below F, calls above) and invert mid prices to Black vols.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .blackscholes import implied_vol
from .market import Market

NY = ZoneInfo("America/New_York")
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def download_chain(ticker="^SPX", min_days=7, max_days=800, out_dir=RAW_DIR) -> Path:
    """Download every expiry between min_days and max_days and save one CSV snapshot."""
    import yfinance as yf

    now = dt.datetime.now(tz=NY)
    tk = yf.Ticker(ticker)
    spot = float(tk.fast_info["lastPrice"])
    rows = []
    for expiry in tk.options:
        exp_date = dt.date.fromisoformat(expiry)
        days = (exp_date - now.date()).days
        if not min_days <= days <= max_days:
            continue
        chain = tk.option_chain(expiry)
        for kind, df in (("call", chain.calls), ("put", chain.puts)):
            df = df[["contractSymbol", "strike", "bid", "ask", "lastPrice", "volume", "openInterest",
                     "lastTradeDate"]].copy()
            df["type"] = kind
            df["expiry"] = expiry
            rows.append(df)
    data = pd.concat(rows, ignore_index=True)
    data["spot"] = spot
    data["snapshot"] = now.isoformat()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"spx_{now:%Y%m%d_%H%M}.csv"
    data.to_csv(path, index=False)
    return path


def latest_snapshot(out_dir=RAW_DIR) -> Path:
    files = sorted(Path(out_dir).glob("spx_*.csv"))
    if not files:
        raise FileNotFoundError(f"no snapshot in {out_dir}: run scripts/download_spx.py first")
    return files[-1]


def _year_fraction(snapshot: dt.datetime, expiry: str, am_settled=False) -> float:
    close = dt.time(9, 30) if am_settled else dt.time(16, 0)
    exp = dt.datetime.combine(dt.date.fromisoformat(expiry), close, tzinfo=NY)
    return (exp - snapshot).total_seconds() / (365.0 * 86400)


def _near_atm_pairs(calls, puts, spot, band):
    both = calls.merge(puts, on="strike", suffixes=("_c", "_p"))
    return both[np.abs(both["strike"] / spot - 1) < band]


def parity_forward(calls: pd.DataFrame, puts: pd.DataFrame, spot: float, band=0.05):
    """D and F from C - P = D F - D K on near-the-money strikes quoted on both sides,
    with one pass of outlier removal (residuals beyond 3 MAD)."""
    both = _near_atm_pairs(calls, puts, spot, band)
    if len(both) < 5:
        return None
    K, diff = both["strike"].to_numpy(), (both["mid_c"] - both["mid_p"]).to_numpy()
    slope, intercept = np.polyfit(K, diff, 1)
    res = diff - (slope * K + intercept)
    mad = np.median(np.abs(res - np.median(res))) + 1e-12
    keep = np.abs(res) < 3 * 1.4826 * mad
    if keep.sum() >= 5:
        slope, intercept = np.polyfit(K[keep], diff[keep], 1)
    D = -slope
    if not 0.8 < D <= 1.02:
        return None
    return float(D), float(intercept / D)


def forward_given_discount(calls, puts, spot, D, band=0.02):
    """Median of K + (C - P) / D over the strikes closest to the money."""
    both = _near_atm_pairs(calls, puts, spot, band)
    if len(both) < 3:
        both = _near_atm_pairs(calls, puts, spot, 3 * band)
    return float(np.median(both["strike"] + (both["mid_c"] - both["mid_p"]) / D))


def build_iv_table(path: Path, max_rel_spread=0.5, k_sd_range=(-3.0, 2.5)):
    """Return (market, slices, table) from a raw snapshot.

    slices is a list of dicts (T, F, D, K, k, iv, spread_iv) ready for calibration; table is the
    flat DataFrame of the retained quotes; market carries the parity-implied D(T) and F(T).
    Strikes are kept when ln(K/F) lies within k_sd_range ATM standard deviations.
    """
    raw = pd.read_csv(path)
    snapshot = dt.datetime.fromisoformat(raw["snapshot"].iloc[0])
    spot = float(raw["spot"].iloc[0])
    raw["root"] = raw["contractSymbol"].str.extract(r"^([A-Z]+)\d")[0]
    weekly = raw.groupby("expiry")["root"].transform(lambda r: (r == "SPXW").any())
    raw = raw[(raw["root"] == "SPXW") | (~weekly & (raw["root"] == "SPX"))]
    raw = raw[(raw["bid"] > 0) & (raw["ask"] > raw["bid"])].copy()
    raw["mid"] = 0.5 * (raw["bid"] + raw["ask"])
    raw = raw[(raw["ask"] - raw["bid"]) / raw["mid"] < max_rel_spread]

    # pass 1: parity per expiry, then a smooth zero-rate curve
    groups = []
    for expiry, grp in raw.groupby("expiry"):
        am = bool((grp["root"] == "SPX").all())
        T = _year_fraction(snapshot, expiry, am)
        calls = grp[grp["type"] == "call"][["strike", "mid", "bid", "ask"]]
        puts = grp[grp["type"] == "put"][["strike", "mid", "bid", "ask"]]
        fit = parity_forward(calls, puts, spot)
        if fit is not None:
            groups.append((expiry, T, calls, puts, fit[0]))
    Ts = np.array([g[1] for g in groups])
    zeros = np.array([-np.log(g[4]) / g[1] for g in groups])
    use = (Ts > 2 / 12) & (zeros > -0.02) & (zeros < 0.15)
    a, b = np.polyfit(Ts[use], zeros[use], 1, w=np.sqrt(Ts[use])) if use.sum() >= 3 else (0.0, np.median(zeros[use]))
    zero_curve = {"slope": float(a), "intercept": float(b), "n_maturities": int(use.sum())}

    # pass 2: forwards with the smoothed discount factors, then implied vols
    pillars, slices, tables = [], [], []
    for expiry, T, calls, puts, _ in groups:
        D = float(np.exp(-(a * T + b) * T))
        F = forward_given_discount(calls, puts, spot, D)
        otm = pd.concat([puts[puts["strike"] < F].assign(cp="put"), calls[calls["strike"] >= F].assign(cp="call")])
        otm = otm.sort_values("strike")
        sign = np.where(otm["cp"] == "call", 1.0, -1.0)
        K = otm["strike"].to_numpy()
        iv, iv_bid, iv_ask = (implied_vol(otm[col].to_numpy(), F, K, T, D, sign) for col in ("mid", "bid", "ask"))
        otm = otm.assign(iv=iv, iv_bid=iv_bid, iv_ask=iv_ask, k=np.log(otm["strike"] / F), T=T, F=F, D=D,
                         expiry=expiry)
        otm = otm.dropna(subset=["iv"])
        if len(otm) < 8:
            continue
        atm = float(np.interp(0.0, otm["k"], otm["iv"]))
        sd = atm * np.sqrt(T)
        otm = otm[(otm["k"] > k_sd_range[0] * sd) & (otm["k"] < k_sd_range[1] * sd)]
        pillars.append((T, D, F))
        tables.append(otm)
        slices.append({"T": T, "F": F, "D": D, "K": otm["strike"].to_numpy(), "k": otm["k"].to_numpy(),
                       "iv": otm["iv"].to_numpy(),
                       "spread_iv": (otm["iv_ask"] - otm["iv_bid"]).to_numpy(), "expiry": expiry})

    pillars = np.array(sorted(pillars))
    # spot consistent with the option quotes: intercept of ln F(T) on the three shortest pillars
    implied_spot = float(np.exp(np.polyfit(pillars[:3, 0], np.log(pillars[:3, 2]), 1)[1]))
    market = Market(implied_spot, pillars[:, 0], pillars[:, 1], pillars[:, 2])
    table = pd.concat(tables, ignore_index=True)
    meta = {"snapshot": snapshot.isoformat(), "yahoo_spot": spot, "implied_spot": implied_spot,
            "zero_rate_curve": zero_curve}
    return market, sorted(slices, key=lambda s: s["T"]), table, meta


def select_maturities(slices, targets=(1 / 24, 1 / 12, 1 / 6, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)):
    """Pick the expiry closest to each target (in years), without duplicates."""
    Ts = np.array([s["T"] for s in slices])
    chosen = []
    for t in targets:
        i = int(np.argmin(np.abs(Ts - t)))
        if i not in chosen and abs(Ts[i] - t) < 0.5 * t:
            chosen.append(i)
    return [slices[i] for i in sorted(chosen)]
