"""The decisive test of the data plan: reconstruct SPX from volatility indices only, on days where real chains exist, and
compare with those chains. Publishes errors in volatility points per moneyness and per maturity (statistics only, never a
quote): results/surface_reconstruction.json, read by reports/surface_reconstruction.md.

    python scripts/lake_surface_test.py [--step 5]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.core.config import paths
from optlab.core.svi import SVISurface, svi_arbitrage_report
from optlab.marketdata.derive.surfaces import fit_observed, fit_shape, index_panel, reconstruct_on, static_prior
from optlab.marketdata.lake import Lake
from optlab.marketdata.quotes import clean_day, quote_days

MONEYNESS = [0, 0.7, 0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 10]
MATURITY = [0, 1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 6.0]


def errors_on(cleaned, surface) -> pd.DataFrame:
    rows = []
    for s in cleaned.slices:
        model = np.asarray(surface.iv(s["k"], s["T"]))
        half = 0.5 * np.nan_to_num(s["spread_iv"], nan=np.inf)
        rows.append(
            pd.DataFrame(
                {
                    "T": s["T"],
                    "m": np.exp(s["k"]),
                    "err": 100 * (model - s["iv"]),
                    "within": np.abs(model - s["iv"]) <= half,
                    "half": 100 * half,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def summarise(frame: pd.DataFrame) -> dict:
    f = frame.assign(mb=pd.cut(frame["m"], MONEYNESS), tb=pd.cut(frame["T"], MATURITY))

    def stats(g):
        return {
            "n": int(len(g)),
            "bias": round(float(g["err"].mean()), 3),
            "mae": round(float(g["err"].abs().mean()), 3),
            "rmse": round(float(np.sqrt((g["err"] ** 2).mean())), 3),
            "within_spread": round(float(g["within"].mean()), 3),
            "half_spread": round(float(g["half"].replace(np.inf, np.nan).median()), 3),
        }

    out = {"all": stats(f), "by_moneyness": {}, "by_maturity": {}, "grid": {}}
    for key, g in f.groupby("mb", observed=True):
        out["by_moneyness"][str(key)] = stats(g)
    for key, g in f.groupby("tb", observed=True):
        out["by_maturity"][str(key)] = stats(g)
    for (t, m), g in f.groupby(["tb", "mb"], observed=True):
        out["grid"].setdefault(str(t), {})[str(m)] = round(float(np.sqrt((g["err"] ** 2).mean())), 2)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--lake", default=None)
    args = ap.parse_args()
    lake = Lake(args.lake)
    prior = static_prior()
    days = quote_days(lake, "SPX", "historicaldata")[:: args.step]
    print(f"{len(days)} days, {days[0]} to {days[-1]}, prior {prior}", flush=True)
    panel = index_panel(lake, days[0], days[-1])
    first = fit_observed(lake, "SPX", days[0], "historicaldata")
    stale = fit_shape(SVISurface.from_dict(json.loads(first["surface"])), source=f"observed {days[0]}")
    variants = {
        "static_prior": (prior, False),
        "static_prior_skew": (prior, True),
        "stale_observed_shape": (stale, False),
    }
    frames = {k: [] for k in variants}
    observed_fit, arbitrage, seconds = [], {k: [] for k in variants}, {k: 0.0 for k in variants}
    t0 = time.perf_counter()
    for i, day in enumerate(days):
        cleaned = clean_day(lake, "SPX", day, "historicaldata")
        row = panel.loc[pd.Timestamp(day)]
        for name, (shape, skew) in variants.items():
            t1 = time.perf_counter()
            rec = reconstruct_on(row, "SPX", shape, skew)
            seconds[name] += time.perf_counter() - t1
            frames[name].append(errors_on(cleaned, rec.surface).assign(day=day))
            rep = svi_arbitrage_report(rec.surface, n_t=30)
            arbitrage[name].append(rep["butterfly_free"] and rep["calendar_free"])
        fit = json.loads(fit_observed(lake, "SPX", day, "historicaldata")["fit"])
        observed_fit.append(fit)
        print(
            f"{i + 1}/{len(days)} {day}: SVI fit {fit['rmse_bp']:.0f} bp, "
            f"static RMSE {np.sqrt((frames['static_prior'][-1]['err'] ** 2).mean()):.2f} pt",
            flush=True,
        )
    result = {
        "source": "historicaldata.net sample, SPX and SPXW",
        "days": [str(days[0]), str(days[-1])],
        "n_days": len(days),
        "n_quotes": int(sum(len(f) for f in frames["static_prior"])),
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "seconds_total": round(time.perf_counter() - t0, 1),
        "prior": {k: getattr(prior, k) for k in ("rho", "eta", "gamma", "v_inf", "kappa", "source")},
        "observed_svi": {
            "rmse_bp_mean": float(np.mean([f["rmse_bp"] for f in observed_fit])),
            "within_spread_mean": float(np.mean([f["within_spread"] for f in observed_fit])),
            "butterfly_free": float(np.mean([f["arbitrage"]["butterfly_free"] for f in observed_fit])),
            "calendar_free": float(np.mean([f["arbitrage"]["calendar_free"] for f in observed_fit])),
        },
        "variants": {},
    }
    for name in variants:
        frame = pd.concat(frames[name], ignore_index=True)
        result["variants"][name] = {
            **summarise(frame),
            "arbitrage_free_days": float(np.mean(arbitrage[name])),
            "ms_per_surface": round(1000 * seconds[name] / len(days), 2),
            "rmse_per_day": {
                str(f["day"].iloc[0]): round(float(np.sqrt((f["err"] ** 2).mean())), 2) for f in frames[name]
            },
        }
    path = paths().results / "surface_reconstruction.json"
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v["all"] for k, v in result["variants"].items()}, indent=1), flush=True)
    print(path, flush=True)


if __name__ == "__main__":
    main()
