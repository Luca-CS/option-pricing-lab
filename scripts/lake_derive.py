"""Compute the derived and synthetic tables of the lake from what it holds: curves, carry, bank, surfaces, correlation.

    python scripts/lake_derive.py [--only curves carry bank ...] [--lake PATH]
"""

from __future__ import annotations

import argparse
import time

from optlab.marketdata.derive.bank import build_bank
from optlab.marketdata.derive.carry import build_carry
from optlab.marketdata.derive.curves import build_discount_curves
from optlab.marketdata.lake import Lake
from optlab.marketdata.universe import enabled

STEPS = {"curves": build_discount_curves, "carry": lambda lake: build_carry(lake, enabled()), "bank": build_bank}


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
