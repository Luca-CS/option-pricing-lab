"""Replay structured products over their life on the real closes in the lake, and publish what the plan's criteria ask.

    optlab lake replay cost             # what a valuation costs, before choosing the frequencies
    optlab lake replay run [--every 1] [--paths 20000] [--model lv] [--costs]

Writes <results>/replay.json: performances, events, flows and P&L in % of notional, never a real level.
"""

from __future__ import annotations

import datetime as dt
import json

from optlab.marketdata.lake import Lake
from optlab.marketdata.replay import ReplayConfig, cost_table, replay, result_json, strike_trade
from optlab.server.settings import Settings
from optlab.server.tools import say
from optlab.structured import preset
from optlab.structured.defaults import resolve

# A daily BRC struck before the fall of March 2020, and a Phoenix struck at the top of January 2022.
PRODUCTS = {
    "BRC-2020": (
        preset(
            "barrier_reverse_convertible",
            **{"general.maturity_months": 12, "downside.barrier": 0.7, "general.underlying": "SPX"},
        ),
        dt.date(2020, 1, 15),
    ),
    "PHX-2022": (
        preset(
            "phoenix",
            **{
                "general.maturity_months": 36,
                "coupon.barrier": 0.7,
                "downside.barrier": 0.6,
                "general.underlying": "SPX",
            },
        ),
        dt.date(2022, 1, 14),
    ),
}


def main(
    action: str,
    every: int = 1,
    paths: int = 20_000,
    model: str = "lv",
    only: str | None = None,
    leverage_every: int = 1,
    out: str | None = None,
    costs: bool = False,
    log=say,
) -> None:
    """`cost`: what one valuation costs; `run`: the products over their life (`costs`: dealt at the desk's client
    price, the lines of the quote becoming the trade's reserves)."""
    if action not in ("cost", "run"):
        raise ValueError(f"action: {action!r} is not cost or run")
    if only is not None and only not in PRODUCTS:
        raise ValueError(f"--only: {only!r} is not one of {', '.join(PRODUCTS)}")
    lake = Lake(Settings.load().lake)
    cfg = ReplayConfig(model=model, n_paths=paths, batch=10_000, reval_every=every, leverage_every=leverage_every)
    if action == "cost":
        sheet, strike = PRODUCTS["BRC-2020"]
        trade, info = strike_trade(lake, "COST", sheet, strike, 1.0, cfg)
        log("inception", {k: info[k] for k in ("coupon", "coupon_se", "seconds")})
        log(json.dumps(cost_table(lake, "SPX", dt.date(2020, 3, 2), trade), indent=1))
        return
    results = {}
    for name, (sheet, strike) in PRODUCTS.items():
        if only and name != only:
            continue
        lines = resolve(None, sheet).inputs() if costs else None
        trade, info = strike_trade(lake, name, sheet, strike, -1.0 if costs else 1.0, cfg, costs=lines)
        log(name, "coupon", info["coupon"], "+/-", info.get("coupon_se"))
        r = replay(lake, trade, cfg=cfg, inception=info)
        s = result_json(r)
        results[name] = s
        log(json.dumps({k: s[k] for k in ("events", "reconciliation", "explain", "seconds")}, indent=1))
    path = Settings.load().results / (out or "replay.json")
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.write_text(json.dumps({**existing, **results}, default=str), encoding="utf-8")
    log(path)
