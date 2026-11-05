"""Update the historical data lake from the public sources and print the quality report.

    python scripts/lake_update.py [--only cboe stoxx fred treasury ecb mof boj boe fx yahoo universe] [--lake PATH]

Data goes to OPTLAB_LAKE, or the stable `optlab-data` folder: never into the repository.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.marketdata.ingest import load_yahoo_chains, update_all
from optlab.marketdata.lake import Lake
from optlab.marketdata.quality import report_text, run_report
from optlab.marketdata.sources.archives import import_historicaldata
from optlab.marketdata.sources.dolthub import Dolt, load_dolthub


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--lake", default=None)
    ap.add_argument("--yahoo-chains", action="store_true", help="one snapshot of the day's chains")
    ap.add_argument("--import-sample", default=None, help="zip of the historicaldata.net sample")
    ap.add_argument("--dolt", default=None, help="folder of the cloned DoltHub options database")
    args = ap.parse_args()
    lake = Lake(args.lake)
    print(f"lake: {lake.root}", flush=True)
    if args.only is not None or not (args.yahoo_chains or args.import_sample or args.dolt):
        update_all(lake, args.only, log=lambda m: print(m, flush=True))
    if args.yahoo_chains:
        for r in load_yahoo_chains(lake):
            print(r, flush=True)
    if args.import_sample:
        for r in import_historicaldata(lake, args.import_sample):
            print(r, flush=True)
    if args.dolt:
        for r in load_dolthub(lake, Dolt.from_environment(args.dolt)):
            print(r, flush=True)
    print(report_text(lake, run_report(lake)))


if __name__ == "__main__":
    main()
