"""Update the historical data lake from the public sources and print the quality report.

    python scripts/lake_update.py [--only cboe stoxx fred treasury ecb yahoo universe] [--lake PATH]

Data goes to OPTLAB_LAKE, or the stable `optlab-data` folder: never into the repository.
"""

from __future__ import annotations

import argparse

from optlab.marketdata.ingest import update_all
from optlab.marketdata.lake import Lake
from optlab.marketdata.quality import report_text, run_report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--lake", default=None)
    args = ap.parse_args()
    lake = Lake(args.lake)
    print(f"lake: {lake.root}", flush=True)
    update_all(lake, args.only, log=lambda m: print(m, flush=True))
    print(report_text(lake, run_report(lake)))


if __name__ == "__main__":
    main()
