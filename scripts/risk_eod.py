"""End-of-day risk runs of a desk store on the lake's closes, the explained P&L, the VaR and the ladder, from the console.

    python scripts/risk_eod.py eod --date 2026-11-04 [--store PATH]
    python scripts/risk_eod.py backfill --from 2026-10-01 --to 2026-11-04 [--no-greeks]
    python scripts/risk_eod.py pnl --from 2026-10-01 --to 2026-11-04
    python scripts/risk_eod.py var [--workers 5] [--days 500]
    python scripts/risk_eod.py ladder

Runs go to `risk.sqlite` beside the desk store (by default the showcase's when it exists); `--precision max` takes 2 million
paths on the engine. Every run is stored with its code version and parameters, and a run asked again with the same key is read
back: a backfill started twice computes nothing the second time.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from optlab import native
    from optlab.app.api import PRECISION
    from optlab.config import paths
    from optlab.marketdata.lake import Lake
    from optlab.risk import RiskSettings
    from optlab.risk import pnl as P
    from optlab.risk.store import RiskStore

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("eod", "backfill", "pnl", "var", "ladder"))
    showcase = paths().store.parent / "showcase" / "desk.sqlite"
    ap.add_argument("--store", default=str(showcase if showcase.exists() else paths().store / "desk.sqlite"))
    ap.add_argument("--date")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--precision", default="standard", choices=sorted(PRECISION))
    ap.add_argument("--engine", default="auto")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--days", type=int, default=500)
    ap.add_argument("--no-greeks", action="store_true")
    ap.add_argument("--market", help="snapshot of the desk store for var and ladder (default: the one in use)")
    args = ap.parse_args()
    native.set_default(args.engine)
    n_paths, batch = PRECISION[args.precision]
    settings = RiskSettings(n_paths=n_paths, batch=batch, engine=args.engine)
    store_path = Path(args.store)
    risk = RiskStore(store_path.parent / "risk.sqlite")
    lake = Lake()
    t0 = time.perf_counter()

    if args.command in ("eod", "backfill", "pnl"):
        from optlab.app.repository import Repository
        trades = Repository(store_path).trades()
        if args.command == "eod":
            r = P.eod(lake, trades, args.date, risk, settings, greeks=not args.no_greeks)
            print(f"{r['run_id']}{' (reused)' if r.get('reused') else ''}: {len(r['positions'])} trades, value {r['value']:,.0f} "
                  f"± {r['value_se']:,.0f}")
        elif args.command == "backfill":
            P.backfill(lake, trades, args.start, args.end, risk, settings, greeks=not args.no_greeks, log=print)
        else:
            c = P.cumulative(risk, args.start, args.end)
            print(f"P&L {c['from']} to {c['to']}: {c['total']:,.0f}")
            for s in c["order"]:
                print(f"  {s:10s} cascade {c['steps'][s]:>14,.0f}   greeks {c['predicted'][s]:>14,.0f}   unexplained {c['unexplained'][s]:>12,.0f}")
    else:
        from optlab.app.api import Desk
        from optlab.app.risk_api import RiskApi
        desk = Desk(store_path, engine=args.engine)
        try:
            if args.market:
                desk.use_snapshot({"snapshot_id": args.market})
            api = RiskApi(desk)
            body = {"options": {"precision": args.precision}, "workers": args.workers, "days": args.days}
            job = api.var(body)["job"] if args.command == "var" else api.ladder(body)["job"]
            while (j := desk.job(job))["state"] in ("queued", "running"):
                time.sleep(1)
            if j["state"] != "done":
                raise SystemExit(f"{args.command} failed: {j.get('error')}")
            res = j["result"]
            if args.command == "var":
                for k in ("one_day", "ten_day", "stressed"):
                    if res.get(k):
                        r = res[k]
                        print(f"{k:9s} VaR {r['var']:>12,.0f} (SE of its scenario {r['var_se']:,.0f})  ES {r['es']:>12,.0f}  on {r['var_date']}  "
                              f"window {r['start']} to {r['end']}  {r['seconds']:.0f} s")
            else:
                for rule in res["rules"]:
                    print(rule, "  ".join(f"{p:+.0%} {res['book'][f'pnl@{rule}@{p:+.4f}'][0]:,.0f}" for p in res["points"]))
            print(f"stored as {res['run_id']}")
        finally:
            desk.jobs.shutdown()
    print(f"{time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
