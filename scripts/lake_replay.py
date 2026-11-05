"""Replay structured products over their life on the real closes in the lake, and publish what the plan's criteria ask.

    python scripts/lake_replay.py cost             # what a valuation costs, before choosing the frequencies
    python scripts/lake_replay.py run [--every 1] [--paths 20000] [--model lv]

Writes results/replay.json: performances, events, flows and P&L in % of notional, never a real level.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from optlab.config import paths
from optlab.marketdata.lake import Lake
from optlab.marketdata.replay import ReplayConfig, cost_table, replay, result_json, strike_trade
from optlab.structured import preset

# A daily BRC struck before the fall of March 2020, and a Phoenix struck at the top of January 2022.
PRODUCTS = {
    "BRC-2020": (preset("barrier_reverse_convertible", **{"general.maturity_months": 12, "downside.barrier": 0.7,
                                                          "general.underlying": "SPX"}), dt.date(2020, 1, 15)),
    "PHX-2022": (preset("phoenix", **{"general.maturity_months": 36, "coupon.barrier": 0.7, "downside.barrier": 0.6,
                                      "general.underlying": "SPX"}), dt.date(2022, 1, 14)),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("cost", "run"))
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--paths", type=int, default=20_000)
    ap.add_argument("--model", default="lv")
    ap.add_argument("--only", default=None)
    ap.add_argument("--leverage-every", type=int, default=1)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    lake = Lake()
    cfg = ReplayConfig(model=args.model, n_paths=args.paths, batch=10_000, reval_every=args.every,
                       leverage_every=args.leverage_every)
    if args.action == "cost":
        sheet, strike = PRODUCTS["BRC-2020"]
        trade, info = strike_trade(lake, "COST", sheet, strike, 1.0, cfg)
        print("inception", {k: info[k] for k in ("coupon", "coupon_se", "seconds")}, flush=True)
        out = cost_table(lake, "SPX", dt.date(2020, 3, 2), trade)
        print(json.dumps(out, indent=1), flush=True)
        return
    results = {}
    for name, (sheet, strike) in PRODUCTS.items():
        if args.only and name != args.only:
            continue
        trade, info = strike_trade(lake, name, sheet, strike, 1.0, cfg)
        print(name, "coupon", info["coupon"], "+/-", info.get("coupon_se"), flush=True)
        r = replay(lake, trade, cfg=cfg, inception=info)
        s = result_json(r)
        results[name] = s
        print(json.dumps({k: s[k] for k in ("events", "reconciliation", "explain", "seconds")}, indent=1), flush=True)
    path = paths().results / (args.out or "replay.json")
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.write_text(json.dumps({**existing, **results}, default=str), encoding="utf-8")
    print(path, flush=True)


if __name__ == "__main__":
    main()
