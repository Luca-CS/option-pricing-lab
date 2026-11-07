"""Where the time of a structured quote goes on this machine: simulation against script evaluation, per model and
per number of workers, and the cost of an SLV leverage calibration. It decides what an accelerator can gain.

Console output, plus a JSON summary with --out. Run it on an idle machine: other jobs slow the CPU and drop the GPU
clock. Usage: python scripts/bench_accel.py [--paths 1000000] [--workers 1,6] [--engines numpy,cpu,gpu] [--out file.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.core.slv import calibrate_leverage  # noqa: E402
from optlab.structured import PricingContext, compile_sheet, preset, price_many  # noqa: E402
from optlab.structured.multiasset import Basket  # noqa: E402

CASES = {
    "phoenix_2y_lv": ("phoenix", {}, "lv"),
    "phoenix_2y_slv": ("phoenix", {}, "slv"),
    "brc_1y_daily_lv": ("barrier_reverse_convertible", {}, "lv"),
    "phoenix_2y_worst_of_3_lv": ("phoenix", {}, "basket3"),
}


def model_for(ctx, kind, horizon):
    if kind != "basket3":
        return ctx.model(kind, horizon), ctx.market
    names = ("SPX", "AAA", "BBB")
    corr = np.array([[1.0, 0.8, 0.7], [0.8, 1.0, 0.75], [0.7, 0.75, 1.0]])
    lv = ctx.model("lv", horizon)
    return Basket(names, {n: lv for n in names}, {n: ctx.market for n in names}, corr), ctx.market


def script_for(name, kind, changes):
    sheet = preset(name, **changes)
    if kind == "basket3":
        sheet = sheet.replace(**{"general.basket": "worst_of", "general.underlyings": ("SPX", "AAA", "BBB")})
    return sheet


def run_case(ctx, key, n_paths, workers, batch, engine="numpy"):
    name, changes, kind = CASES[key]
    product = compile_sheet(script_for(name, kind, changes), ctx.valuation)
    model, market = model_for(ctx, kind, product.horizon)
    t0 = time.perf_counter()
    (res,) = price_many(
        product.program,
        model,
        market,
        [{product.coupon_param: 0.02}],
        n_paths=n_paths,
        seed=7,
        batch=batch,
        valuation=ctx.valuation,
        calendar=product.calendar,
        workers=workers,
        engine=engine,
    )
    wall = time.perf_counter() - t0
    t = res.timings
    return {
        "case": key,
        "paths": n_paths,
        "workers": workers,
        "engine": t.get("engine"),
        "kernel_ms": t.get("kernel_ms"),
        "price": res.price,
        "stderr": res.stderr,
        "wall": wall,
        "simulation_cpu": t["simulation"],
        "evaluation_cpu": t["evaluation"],
        "simulation_share": t["simulation"] / (t["simulation"] + t["evaluation"]),
    }


def desk_quote(engine, precision):
    """A Phoenix quoted as the Pricer does it: policy model (local vol base, SLV provision), Greeks with the SLV vega,
    the SLV mix band and the desk's costs, in its own temporary store."""
    import tempfile

    from optlab.core import engines
    from optlab.server.api import Desk

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        desk = Desk(store=Path(tmp) / "desk.sqlite", engine=engine)
        try:
            s = preset("phoenix").to_dict(json_safe=True)
            kind = s.pop("type")
            body = {
                "sheet": {"type": kind, "values": s},
                "options": {"precision": precision, "greeks": True, "slv_vega": True, "mix_band": True, "costs": True},
            }
            t0 = time.perf_counter()
            job = desk.quote(body)["job"]
            while (j := desk.job(job))["state"] in ("queued", "running"):
                time.sleep(0.02)
            wall = time.perf_counter() - t0
            if j["state"] != "done":
                raise RuntimeError(j.get("error"))
            res = j["result"]
            return {
                "case": "desk_quote",
                "engine": engine,
                "precision": precision,
                "paths": res["options"]["n_paths"],
                "wall": wall,
                "coupon": res.get("coupon_rate"),
                "coupon_se": res.get("coupon_rate_se"),
            }
        finally:
            desk.jobs.shutdown()
            engines.set_default(None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=1_000_000)
    ap.add_argument("--batch", type=int, default=50_000)
    ap.add_argument("--workers", default="1,6")
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--engines", default="numpy,cpu,gpu")
    ap.add_argument("--no-leverage", action="store_true")
    ap.add_argument("--desk", help="precisions of a full desk quote to time, e.g. standard,precise")
    ap.add_argument("--out")
    args = ap.parse_args()
    ctx = PricingContext.from_calibration()
    rows = []
    print(f"cpu count {os.cpu_count()}, paths {args.paths:,}, batch {args.batch:,}")
    runs = [(e, int(w)) for e in args.engines.split(",") for w in (args.workers.split(",") if e == "numpy" else ["1"])]
    for key in [c for c in args.cases.split(",") if c]:
        run_case(
            ctx, key, 20_000, 1, args.batch, "gpu" if "gpu" in args.engines else "numpy"
        )  # warm-up: tables, CUDA context
        for e, w in runs:
            r = run_case(ctx, key, args.paths, w, args.batch, e)
            rows.append(r)
            print(
                f"{key:28s} {e:5s} workers {w:2d}  wall {r['wall']:7.2f} s  simulation {r['simulation_cpu']:7.2f} s  "
                f"evaluation {r['evaluation_cpu']:7.2f} s  share {100 * r['simulation_share']:5.1f} %  "
                f"price {r['price']:.4f} ± {r['stderr']:.4f}"
            )
    for e in () if args.no_leverage else args.engines.split(","):
        for n in (100_000, 200_000):
            t0 = time.perf_counter()
            calibrate_leverage(ctx.heston, ctx.surface, ctx.market, 1.0, mix=0.5, n_particles=n, rng=1, engine=e)
            dt_ = time.perf_counter() - t0
            rows.append({"case": "slv_leverage_1y", "engine": e, "particles": n, "wall": dt_})
            print(f"slv leverage 1y {e:5s} {n:,} particles: {dt_:.2f} s")
    if args.desk:
        for e in args.engines.split(","):
            for precision in args.desk.split(","):
                r = desk_quote(e, precision)
                rows.append(r)
                print(
                    f"desk quote {e:5s} {precision:9s} ({r['paths']:,} paths, greeks, SLV vega, mix band, costs): "
                    f"{r['wall']:6.1f} s, coupon {100 * r['coupon']:.3f} % ± {100 * r['coupon_se']:.3f}"
                )
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
