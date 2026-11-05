"""Compute the derived and synthetic tables of the lake from what it holds: curves, carry, bank, surfaces, correlation.

    python scripts/lake_derive.py [--only curves carry bank fx ...] [--lake PATH]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.marketdata.derive.bank import build_bank
from optlab.marketdata.derive.carry import build_carry, build_parity_carry
from optlab.marketdata.derive.correl import build_basket_quotes, build_premium, build_realized
from optlab.marketdata.derive.curves import build_discount_curves
from optlab.marketdata.derive.fx import build_fx_correl, build_fx_forwards, build_fx_vols
from optlab.marketdata.derive.surfaces import build_observed, build_reconstructed
from optlab.marketdata.lake import Lake
from optlab.marketdata.quotes import quote_days
from optlab.marketdata.universe import enabled_names, enabled_underlyings

BASKETS = (("SPX", "NDX"), ("SPX", "RUT"), ("SPX", "AAPL"), ("SPX", "SX5E"), ("SPX", "NDX", "RUT", "SX5E"),
           ("AAPL", "AMZN", "GOOGL", "GS", "IBM"))


def spread_days(lake):
    """Month ends since 2018: pseudo-quotes for the Lab and the worst-ofs do not need every day."""
    days = lake.read("prices_eod", where="instrument = 'SPX' AND obs_date >= DATE '2018-02-01'", columns="obs_date")["obs_date"]
    days = pd.Series(pd.to_datetime(days).sort_values().to_numpy())
    return days.groupby(days.dt.to_period("M")).max().tolist()


def observed(lake):
    """Fits on every day of the archives, and on the Yahoo snapshots with long enough chains."""
    out = [build_observed(lake, "SPX", quote_days(lake, "SPX", "historicaldata"), "historicaldata", log=print)]
    for name in ("SPX", "NDX", "RUT"):
        out.append(build_observed(lake, name, quote_days(lake, name, "yahoo"), "yahoo", log=print))
    return out


def fx(lake):
    """Forwards by covered parity, realised volatility, and each name against each pair."""
    return build_fx_forwards(lake) + build_fx_vols(lake) + build_fx_correl(lake)


STEPS = {"curves": build_discount_curves, "carry": lambda lake: build_carry(lake, enabled_underlyings()), "bank": build_bank, "fx": fx,
         "reconstructed": lambda lake: build_reconstructed(lake, enabled_names()),
         "realized": lambda lake: build_realized(lake, enabled_names()), "premium": build_premium,
         "baskets": lambda lake: build_basket_quotes(lake, BASKETS, spread_days(lake), log=print), "observed": observed,
         "parity": lambda lake: build_parity_carry(lake, "SPX", "historicaldata")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--lake", default=None)
    args = ap.parse_args()
    lake = Lake(args.lake)
    for name, step in STEPS.items():
        if args.only and name not in args.only:
            continue
        t0 = time.perf_counter()
        result = step(lake)
        rows = result if isinstance(result, list) else [result]
        print(f"{name}: {sum(r['n_new'] for r in rows)} new, {sum(r['n_changed'] for r in rows)} changed, "
              f"{time.perf_counter() - t0:.1f} s", flush=True)


if __name__ == "__main__":
    main()
