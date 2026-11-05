"""The desk price on the real lake: the summary of each family, what the assumptions move, the fit of the correlation skew, and what it costs.

    python scripts/pricing_examples.py [--day 2026-11-04] [--paths 100000] [--workers 6]

Writes results/pricing_examples.json: prices and costs in % of notional, never a level. Reads the lake, so it does not run without it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.config import paths
from optlab.marketdata.lake import Lake
from optlab.marketdata.names import NameMarkets
from optlab.marketdata.pit import basket_at
from optlab.structured import preset, quote
from optlab.structured.compiler import compile_sheet
from optlab.structured.costs import CostInputs
from optlab.structured.defaults import resolve
from optlab.structured.localcorr import LocalCorrelation, basket_sigma, calibrate, model_quotes, targets
from optlab.structured.pricer import price_many
from optlab.structured.quote import wrapper

SINGLE = {"Phoenix 2y on SPX": ("phoenix", {}), "Athena 2y on SPX": ("athena", {}), "BRC 1y on SPX, daily 70 %": ("barrier_reverse_convertible", {}),
          "Reverse convertible 1y on SPX, 80 %": ("reverse_convertible", {"downside.barrier": 0.8}), "Phoenix 2y on AAPL": ("phoenix", {"general.underlying": "AAPL"})}
BASKETS = {"Phoenix 2y, worst of SPX, NDX, RUT": ["SPX", "NDX", "RUT"], "Phoenix 2y, worst of SPX, SX5E": ["SPX", "SX5E"]}


def summary(q) -> dict:
    d = q.desk
    return {"base": d.base_price, "base_se": d.base_se, "client": d.final_price, "client_se": d.final_se, "coupon_base": d.coupon_base, "coupon_final": d.coupon_final,
            "coupon_se": q.coupon_rate_se, "passes": d.passes, "digital_passes": d.digital_passes, "seconds": q.seconds,
            "lines": [{"key": x.key, "label": x.label, "kind": x.kind, "cost": x.cost, "se": x.se, "coupon_bp": x.coupon_bp, "used": x.used, "gap": x.gap, "note": x.note} for x in d.lines]}


def gap_table(ctx, names, n_paths, workers) -> list:
    """The SLV gap of a worst-of per mix and per way of correlating the variances."""
    sheet = preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": list(names), "wrapper.solve": "upfront", "coupon.rate": 0.12})
    p = compile_sheet(sheet, ctx.valuation)
    kw = dict(fmt=wrapper(sheet, p), n_paths=n_paths, seed=2026, batch=25_000, valuation=ctx.valuation, calendar=p.calendar, workers=workers)
    lv = price_many(p.program, ctx.model("lv", 2.0), ctx.market, [{}], **kw)[0]
    out = []
    for vol_corr in ("same", "independent"):
        for mix in (0.25, 0.5, 0.75):
            slv = price_many(p.program, ctx.model("slv", 2.0, mix=mix, spot_driven=True, vol_corr=vol_corr), ctx.market, [{}], **kw)[0]
            d = (slv.pairs - lv.pairs) * 100 / slv.notional
            out.append({"vol_corr": vol_corr, "mix": mix, "gap": float(d.mean()), "se": float(d.std(ddof=1) / np.sqrt(len(d)))})
    return out


def fit_table(ctx, names) -> dict:
    wanted = targets(ctx, names)
    flat, se = model_quotes(ctx, names, LocalCorrelation(basket_sigma(ctx, names)), wanted, 1.0, 100_000, 77)
    constant = [{"kind": r["kind"], "strike": r["strike"], "pseudo_quote": r["price"], "model": float(f), "se": float(e)} for r, f, e in zip(wanted, flat, se)]
    out = {"constant": constant}
    for form in ("local", "stochastic"):
        t0 = time.perf_counter()
        got = calibrate(ctx, names, form=form)
        m = got["mixer"]
        shorter = {}
        for T in (0.25, 0.5):
            w = targets(ctx, names, T)
            f, e = model_quotes(ctx, names, m, w, T, 100_000, 78)
            shorter[str(T)] = [{"strike": r["strike"], "pseudo_quote": r["price"], "model": float(a), "se": float(b)} for r, a, b in zip(w, f, e)]
        out[form] = {"seconds": time.perf_counter() - t0, "rmse": got["rmse"], "lines": got["lines"], "shorter": shorter,
                     "params": {k: float(v) for k, v in vars(m).items()}}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", default="2026-11-04")
    ap.add_argument("--paths", type=int, default=100_000)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    lake = Lake()
    names = NameMarkets(lake)
    out = {"day": args.day, "n_paths": args.paths, "workers": args.workers, "defaults": "starting values, first tier", "products": {}, "baskets": {}}
    for label, (name, changes) in SINGLE.items():
        sheet = preset(name, **changes)
        ctx, _ = names.context(sheet.underlyings[0], args.day)
        q = quote(sheet, ctx, control=None, n_paths=args.paths, batch=25_000, workers=args.workers, costs=resolve(None, sheet).inputs())
        out["products"][label] = summary(q)
        print(label, f"{q.seconds:.1f} s", flush=True)
        print(q.desk.table(), flush=True)
    for label, members in BASKETS.items():
        ctx, _, meta = basket_at(lake, members, args.day, layer="reconstructed")
        t0 = time.perf_counter()
        ctx.model("slv", 2.0, mix=0.5)
        leverage = time.perf_counter() - t0
        fit = fit_table(ctx, tuple(members))
        print(label, json.dumps(fit, indent=0, default=float)[:2000], flush=True)
        sheet = preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": members, "general.notional": 5e6})
        q = quote(sheet, ctx, n_paths=args.paths, batch=25_000, workers=args.workers, costs=resolve(None, sheet).inputs())
        print(q.desk.table(), flush=True)
        out["baskets"][label] = {**summary(q), "correlation": {"matrix": np.round(ctx.corr, 3).tolist(), **meta}, "leverages_seconds": leverage, "fit": fit,
                                 "slv_gap": gap_table(ctx, members, args.paths, args.workers)}
    path = paths().results / "pricing_examples.json"
    path.write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    print(path, flush=True)


if __name__ == "__main__":
    main()
