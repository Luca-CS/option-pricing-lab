"""Does the reconstruction hold when the VIX is high? Reconstruct SPX from volatility indices only, compare with real SPY chains
(DoltHub, up to about 2 months, 2019-2026) and SPX chains (2022) by level of the VIX. The crash of February to May 2020 is kept out
of the fit of the slope, so the figures on it are out of sample. Statistics only: results/surface_regimes.json.

    python scripts/lake_regime_test.py [--step 8]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.config import paths
from optlab.marketdata.derive.surfaces import LEVEL_REF, index_panel, reconstruct_on, static_prior
from optlab.marketdata.lake import Lake
from optlab.marketdata.pit import real_close
from optlab.marketdata.quotes import clean_day, quote_days

CRASH = (pd.Timestamp("2020-02-20"), pd.Timestamp("2020-05-15"))
BUCKETS = [0, 20, 30, 40, 60, 100]


def slices_of(chain) -> list:
    out = []
    for s in chain.slices:
        k, iv = np.asarray(s["k"]), np.asarray(s["iv"])
        keep = (np.abs(k / np.median(iv * np.sqrt(s["T"]))) < 2.5) & np.isfinite(iv)
        if 0.03 <= s["T"] <= 2.2 and keep.sum() >= 5:
            out.append((float(s["T"]), k[keep], iv[keep]))
    return out


def sample(lake: Lake, panel: pd.DataFrame, step: int) -> list:
    """Every SPY day of the crash and of a VIX above 27, one in `step` otherwise, and one SPX day in three of 2022."""
    days = []
    for day in pd.to_datetime(quote_days(lake, "SPY", "dolthub")):
        if day in panel.index:
            days.append(("SPY", "dolthub", day))
    spy = [
        d for i, d in enumerate(days) if CRASH[0] <= d[2] <= CRASH[1] or panel.loc[d[2], "VIX"] > 27 or i % step == 0
    ]
    spx = [
        ("SPX", "historicaldata", d)
        for d in pd.to_datetime(quote_days(lake, "SPX", "historicaldata"))[::3]
        if d in panel.index
    ]
    out = []
    for name, source, day in spy + spx:
        try:
            kw = {"spot": 0.1 * real_close(lake, "SPX", day.date())[0]} if name == "SPY" else {}
            chain = clean_day(lake, name, day.date(), source, **kw)
        except KeyError, ValueError:
            continue
        slices = slices_of(chain)
        if slices:
            out.append({"day": day, "slices": slices})
    return out


def errors(prior, days: list, panel: pd.DataFrame) -> list:
    out = []
    for d in days:
        row = panel.loc[d["day"]]
        surface = reconstruct_on(row, "SPX", prior).surface
        for T, k, iv in d["slices"]:
            o = np.argsort(k)
            atm = float(np.interp(0.0, k[o], iv[o]))
            sd = atm * np.sqrt(T)
            model_atm = float(np.sqrt(surface.theta(T) / T))
            skew = float(surface.iv(np.array([-sd]), T)[0]) - model_atm - (float(np.interp(-sd, k[o], iv[o])) - atm)
            out.append(
                {
                    "vix": float(row["VIX"]),
                    "err": np.asarray(surface.iv(k, T)) - iv,
                    "atm": model_atm - atm,
                    "skew": skew,
                }
            )
    return out


def table(rows: list) -> dict:
    e = pd.DataFrame(
        [
            {
                "vix": r["vix"],
                "mse": np.mean(r["err"] ** 2),
                "bias": np.mean(r["err"]),
                "atm": r["atm"],
                "skew": r["skew"],
            }
            for r in rows
        ]
    )
    e["bucket"] = pd.cut(e["vix"], BUCKETS)
    out = {}
    for bucket, g in e.groupby("bucket", observed=True):
        out[str(bucket)] = {
            "slices": len(g),
            "rmse": round(100 * float(np.sqrt(g["mse"].mean())), 2),
            "bias": round(100 * float(g["bias"].mean()), 2),
            "atm_gap": round(100 * float(g["atm"].mean()), 2),
            "skew_gap": round(100 * float(g["skew"].mean()), 2),
        }
    return out


def fit_power(prior, days: list, panel: pd.DataFrame) -> float:
    def residuals(x):
        p = dataclasses.replace(prior, level_power=float(x[0]), level_ref=LEVEL_REF)
        return np.concatenate(
            [r["err"] / np.sqrt(len(r["err"])) * (2.0 if r["vix"] > 30 else 1.0) for r in errors(p, days, panel)]
        )

    return float(least_squares(residuals, [0.1], bounds=([0.0], [1.0]), diff_step=0.05, max_nfev=12).x[0])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--step", type=int, default=8)
    args = ap.parse_args()
    t0 = time.perf_counter()
    lake = Lake()
    panel = index_panel(lake, "2018-06-01")
    days = sample(lake, panel, args.step)
    crash = [d for d in days if CRASH[0] <= d["day"] <= CRASH[1]]
    rest = [d for d in days if not CRASH[0] <= d["day"] <= CRASH[1]]
    print(f"{len(days)} days, {len(crash)} in the crash, {time.perf_counter() - t0:.0f} s", flush=True)
    current = static_prior()
    flat = dataclasses.replace(current, level_power=0.0)
    power = fit_power(flat, rest, panel)
    print(f"slope power fitted away from the crash: {power:.3f}", flush=True)
    out = {
        "run_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "n_days": len(days),
        "n_crash_days": len(crash),
        "power_fitted_without_crash": round(power, 3),
        "power_in_use": current.level_power,
        "level_ref": current.level_ref,
        "constant_slope": {"all": table(errors(flat, days, panel)), "crash": table(errors(flat, crash, panel))},
        "slope_falling_with_level": {
            "all": table(errors(current, days, panel)),
            "crash": table(errors(current, crash, panel)),
        },
        "seconds": round(time.perf_counter() - t0, 1),
    }
    path = paths().results / "surface_regimes.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    print(path)


if __name__ == "__main__":
    main()
