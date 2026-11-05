"""Quality report of the lake: gaps, jumps, broken bars, stale series. Issues are stored, never silently corrected."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .lake import Lake, new_run_id

MAX_MOVE = {"index": 0.2, "equity": 0.3}  # one-day moves beyond these are flagged, not removed
STALE_DAYS = 10
VOL_RANGE = (3.0, 200.0)


def _issue(dataset, item, check, severity, detail, day) -> dict:
    return {"dataset": dataset, "item": item, "check_name": check, "severity": severity, "detail": detail,
            "obs_date": pd.Timestamp(day).date()}


def check_prices(prices: pd.DataFrame, today: dt.date, max_move: float = 0.3) -> list[dict]:
    issues = []
    for name, g in prices.groupby("instrument"):
        g = g.sort_values("obs_date")
        close = g["close"].to_numpy(dtype=float)
        days = pd.to_datetime(g["obs_date"])
        moves = np.abs(np.diff(np.log(close)))
        for i in np.where(moves > max_move)[0]:
            issues.append(_issue("prices_eod", name, "large_move", "warning",
                                 f"{moves[i]:.1%} log move from {close[i]:g} to {close[i + 1]:g}", days.iloc[i + 1]))
        gaps = days.diff().dt.days.to_numpy()[1:]
        for i in np.where(gaps > 6)[0]:
            issues.append(_issue("prices_eod", name, "gap", "warning", f"no price for {int(gaps[i])} days",
                                 days.iloc[i + 1]))
        bad = (g["high"] < g["low"]) | (g["close"] > g["high"] * 1.001) | (g["close"] < g["low"] * 0.999)
        if bad.any():
            issues.append(_issue("prices_eod", name, "bar_inconsistent", "warning",
                                 f"close outside [low, high] on {int(bad.sum())} days", days[bad.to_numpy()].iloc[-1]))
        if (today - days.iloc[-1].date()).days > STALE_DAYS:
            issues.append(_issue("prices_eod", name, "stale", "warning", f"last price {days.iloc[-1].date()}",
                                 days.iloc[-1]))
    return issues


def check_vol_indices(vols: pd.DataFrame, today: dt.date) -> list[dict]:
    issues = []
    for name, g in vols.groupby("index_name"):
        g = g.sort_values("obs_date")
        days = pd.to_datetime(g["obs_date"])
        if name not in ("SKEW", "VVIX", "DSPX"):
            out = (g["close"] < VOL_RANGE[0]) | (g["close"] > VOL_RANGE[1])
            if out.any():
                issues.append(_issue("vol_indices", name, "out_of_range", "warning",
                                     f"{int(out.sum())} levels outside {VOL_RANGE}", days[out.to_numpy()].iloc[-1]))
        if (today - days.iloc[-1].date()).days > STALE_DAYS:
            issues.append(_issue("vol_indices", name, "stale", "warning",
                                 f"series stops on {days.iloc[-1].date()}: no level after this date", days.iloc[-1]))
    return issues


def run_report(lake: Lake, today: dt.date | None = None, store: bool = True) -> pd.DataFrame:
    today = today or dt.date.today()
    issues = check_prices(lake.read("prices_eod", columns="instrument, obs_date, high, low, close"), today) \
        if lake.has("prices_eod") else []
    if lake.has("vol_indices"):
        issues += check_vol_indices(lake.read("vol_indices", columns="index_name, obs_date, close"), today)
    frame = pd.DataFrame(issues, columns=["dataset", "item", "check_name", "severity", "detail", "obs_date"])
    if store and len(frame):
        lake.ingest("quality_issues", frame, "quality", "derived", new_run_id("quality"))
    return frame


def report_text(lake: Lake, issues: pd.DataFrame) -> str:
    """A small plain-text table: coverage per dataset and issue counts."""
    cov = lake.coverage()
    lines = ["dataset | origin | source | rows | first | last"]
    for r in cov.itertuples():
        lines.append(f"{r.dataset} | {r.origin} | {r.source} | {r.n} | {pd.Timestamp(r.first).date()} | "
                     f"{pd.Timestamp(r.last).date()}")
    counts = issues.groupby(["dataset", "check_name", "severity"]).size() if len(issues) else {}
    lines += ["", "issues:"] + [f"  {k}: {v}" for k, v in (counts.items() if len(issues) else [])]
    return "\n".join(lines)
