"""Where the time of a structured quote goes on this machine: simulation against script evaluation, per model and
per number of workers, and the cost of an SLV leverage calibration. It decides what an accelerator can gain.

Console output, plus a JSON summary with --out. Run it on an idle machine: other jobs slow the CPU and drop the GPU
clock. Usage: optlab bench accel [--paths 1000000] [--workers 1,6] [--engines numpy,cpu,gpu] [--out file.json]
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from optlab.core.slv import calibrate_leverage
from optlab.server.settings import Settings
from optlab.server.tools import say
from optlab.structured import PricingContext, compile_sheet, preset, price_many
from optlab.structured.multiasset import Basket

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
        desk = Desk(store=Path(tmp) / "optlab.sqlite", engine=engine)
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
            desk.close()
            engines.set_default(None)


def main(
    paths: int = 1_000_000,
    batch: int = 50_000,
    workers: str = "1,6",
    cases: str = ",".join(CASES),
    engines: str = "numpy,cpu,gpu",
    no_leverage: bool = False,
    desk: str | None = None,
    out: str | None = None,
    log=say,
) -> None:
    """`desk`: precisions of a full desk quote to time, e.g. standard,precise."""
    unknown = sorted(set(c for c in cases.split(",") if c) - set(CASES))
    if unknown:
        raise ValueError(f"--cases: {', '.join(unknown)} not a case ({', '.join(CASES)})")
    ctx = PricingContext.from_calibration(Settings.load().calibration)
    rows = []
    log(f"cpu count {os.cpu_count()}, paths {paths:,}, batch {batch:,}")
    runs = [(e, int(w)) for e in engines.split(",") for w in (workers.split(",") if e == "numpy" else ["1"])]
    for key in [c for c in cases.split(",") if c]:
        run_case(ctx, key, 20_000, 1, batch, "gpu" if "gpu" in engines else "numpy")  # warm-up: tables, CUDA context
        for e, w in runs:
            r = run_case(ctx, key, paths, w, batch, e)
            rows.append(r)
            log(
                f"{key:28s} {e:5s} workers {w:2d}  wall {r['wall']:7.2f} s  simulation {r['simulation_cpu']:7.2f} s  "
                f"evaluation {r['evaluation_cpu']:7.2f} s  share {100 * r['simulation_share']:5.1f} %  "
                f"price {r['price']:.4f} ± {r['stderr']:.4f}"
            )
    for e in () if no_leverage else engines.split(","):
        for n in (100_000, 200_000):
            t0 = time.perf_counter()
            calibrate_leverage(ctx.heston, ctx.surface, ctx.market, 1.0, mix=0.5, n_particles=n, rng=1, engine=e)
            dt_ = time.perf_counter() - t0
            rows.append({"case": "slv_leverage_1y", "engine": e, "particles": n, "wall": dt_})
            log(f"slv leverage 1y {e:5s} {n:,} particles: {dt_:.2f} s")
    if desk:
        for e in engines.split(","):
            for precision in desk.split(","):
                r = desk_quote(e, precision)
                rows.append(r)
                log(
                    f"desk quote {e:5s} {precision:9s} ({r['paths']:,} paths, greeks, SLV vega, mix band, costs): "
                    f"{r['wall']:6.1f} s, coupon {100 * r['coupon']:.3f} % ± {100 * r['coupon_se']:.3f}"
                )
    if out:
        Path(out).write_text(json.dumps(rows, indent=1))
