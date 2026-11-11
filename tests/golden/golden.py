"""Golden numbers: the library's outputs frozen before the refactor, compared bit for bit after every step.

    python tests/golden/golden.py record [--group NAME ...]     # write the files (A0 only, or after a documented fix)
    python tests/golden/golden.py check [--group NAME ...] [--rtol 1e-12 | --across]

Groups marked `ci` use synthetic inputs only and are versioned in `tests/golden/`; `native` ones too, but they need the
compiled engine (and a GPU for `native_gpu`), so only `--local` or `--group` runs them. Groups marked `local` read the
real lake, the showcase store or the GPU: their files go to `$OPTLAB_DATA/golden/` (default `optlab-data/golden`
beside the main checkout), never into the repository, since they derive from personal-use market data.

Every Monte Carlo number is recorded with its standard error. Same machine and same engine: equality to the last
bit. Another machine (a hosted runner, another OS): `--across`, the tolerance of ADR-0017, never widened silently:
groups with fixed inputs within `ACROSS_RTOL`; groups that recalibrate, whose fits amplify the last bits, with each
Monte Carlo number within `SE_RATIO` of its standard error and every other number within `FIT_RTOL`.

This file becomes `optlab check golden [--record]` in step A7; the case definitions stay as they are.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
SPX_MARKET = "SPX-20261104T1600-8b7fa10118"  # the showcase's market in use (close of 4 November 2026)


def local_dir() -> Path:
    from optlab.server.settings import Settings

    base = os.environ.get("OPTLAB_DATA")
    return (Path(base) if base else Settings.load().data) / "golden"


# ---------------------------------------------------------------------------------------------------------- helpers

BIG = 256  # arrays above this size are kept as a digest: sha256 of the float64 bytes, sum and evenly spaced samples


def digest(a: np.ndarray) -> dict:
    a = np.ascontiguousarray(a, dtype=np.float64)
    flat = a.ravel()
    idx = np.linspace(0, flat.size - 1, 32).astype(int)
    return {
        "digest": hashlib.sha256(a.tobytes()).hexdigest(),
        "shape": list(a.shape),
        "sum": float(flat.sum()),
        "samples": [float(v) for v in flat[idx]],
    }


def plain(x):
    """Numbers and containers as JSON-ready values, floats at full precision (repr round-trips)."""
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        if len(x) > BIG and all(
            isinstance(v, (int, float, np.floating, np.integer))
            or (isinstance(v, (list, np.ndarray)) and np.ndim(v) >= 1)
            for v in x[:4]
        ):
            try:
                return digest(np.asarray(x, dtype=np.float64))
            except TypeError, ValueError:
                pass
        return [plain(v) for v in x]
    if isinstance(x, np.ndarray):
        if x.size > BIG and np.issubdtype(x.dtype, np.number):
            return digest(x)
        return plain(x.tolist())
    if isinstance(x, (np.floating, float)):
        return float(x)
    if isinstance(x, (np.integer, int)) and not isinstance(x, bool):
        return int(x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    if x is None or isinstance(x, str):
        return x
    if hasattr(x, "price") and hasattr(x, "stderr"):  # MCResult and StructuredResult
        return {"value": float(x.price), "se": float(x.stderr)}
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {f.name: plain(getattr(x, f.name)) for f in dataclasses.fields(x)}
    raise TypeError(f"no golden form for {type(x).__name__}")


def numbers_only(x):
    """Drop timings and identifiers that change from one run to the next: a golden holds results, not clocks."""
    skip = ("seconds", "timings", "elapsed", "run_id", "created_at", "code", "started", "finished", "time_s", "_s")
    if isinstance(x, dict):
        return {
            k: numbers_only(v)
            for k, v in x.items()
            if not (k in skip or k.endswith(("_s", "_seconds")) or k in ("meta", "job"))
        }
    if isinstance(x, list):
        return [numbers_only(v) for v in x]
    return x


# ------------------------------------------------------------------------------------------------------- ci groups


def closed_forms() -> dict:
    from optlab.core import (
        HestonParams,
        Market,
        barrier_price,
        barrier_shift_discrete,
        binomial_price,
        black_price,
        bs_greeks,
        bs_price,
        geometric_asian_price,
        geometric_asian_price_curve,
        heston_iv,
        heston_price,
        implied_vol,
    )
    from optlab.core.blackscholes import black_vega

    S, r, q = 100.0, 0.03, 0.01
    Ks, Ts, vols = [70.0, 90.0, 100.0, 110.0, 140.0], [0.05, 0.5, 1.0, 3.0], [0.1, 0.25, 0.6]
    out: dict = {"bs": {}, "greeks": {}, "implied": {}, "barrier": {}, "crr": {}, "heston": {}}
    for cp in ("call", "put"):
        out["bs"][cp] = [[[bs_price(S, K, T, r, q, v, cp) for v in vols] for T in Ts] for K in Ks]
        out["greeks"][cp] = [bs_greeks(S, K, 0.7, r, q, 0.22, cp) for K in Ks]
        F, K, T = 102.0, np.array(Ks), 0.8
        D = math.exp(-r * T)
        prices = black_price(F, K, T, 0.3, D, cp)
        out["implied"][cp] = {"prices": prices, "vols": implied_vol(prices, F, K, T, D, cp)}
    out["black_vega"] = black_vega(102.0, np.array(Ks), 0.8, 0.3, 0.97)
    fixings = np.linspace(1 / 12, 1.0, 12)
    out["asian_geometric"] = [
        geometric_asian_price(S, K, r, q, 0.25, fixings, cp) for K in (90.0, 100.0, 110.0) for cp in ("call", "put")
    ]
    pillars = np.array([0.25, 1.0, 2.0])
    curve = Market(
        S,
        pillars,
        np.exp(-np.array([0.02, 0.03, 0.035]) * pillars),
        S * np.exp(np.array([0.015, 0.02, 0.023]) * pillars),
    )
    out["asian_geometric_curve"] = [
        geometric_asian_price_curve(curve, K, 0.25, fixings, "call") for K in (90.0, 100.0, 110.0)
    ]
    for kind in ("down-out", "down-in", "up-out", "up-in"):
        H = 85.0 if kind.startswith("down") else 120.0
        out["barrier"][kind] = [
            barrier_price(S, K, H, 1.0, r, q, 0.25, kind, cp) for K in (90.0, 100.0, 110.0) for cp in ("call", "put")
        ]
    out["barrier_shift"] = [barrier_shift_discrete(H, S, 0.25, 1 / 252) for H in (80.0, 120.0)]
    for american in (False, True):
        out["crr"]["american" if american else "european"] = [
            binomial_price(S, K, T, r, q, 0.25, cp, american=american, n=500)
            for K in (90.0, 100.0, 110.0)
            for T in (0.25, 1.0)
            for cp in ("call", "put")
        ]
    hp = HestonParams(0.04, 1.5, 0.05, 0.7, -0.7)
    K = np.array([60.0, 80.0, 100.0, 120.0, 160.0])
    for T in (0.1, 1.0, 3.0):
        out["heston"][str(T)] = {
            "call": heston_price(105.0, K, T, hp, 0.97, "call"),
            "put": heston_price(105.0, K, T, hp, 0.97, "put"),
            "iv": heston_iv(105.0, K, T, hp, 0.97),
        }
    return out


def _synthetic_ssvi():
    from optlab.core import SSVI

    mats = np.array([0.1, 0.25, 0.5, 1.0, 2.0])
    return SSVI.power_law(-0.7, 1.1, 0.4, mats, 0.04 * mats * (1 + 0.2 * mats))


def mc_products() -> dict:
    from optlab.core import (
        BlackScholesModel,
        HestonModel,
        HestonParams,
        LocalVolModel,
        Market,
        american_lsm,
        asian_mc,
        barrier_mc,
        calibrate_leverage,
        european_mc,
        simulate,
    )

    market = Market.flat(100.0, 0.03, 0.01)
    hp = HestonParams(0.04, 1.5, 0.04, 0.5, -0.7)
    ssvi = _synthetic_ssvi()
    models = {"bs": BlackScholesModel(0.2), "heston": HestonModel(hp), "lv": LocalVolModel(ssvi)}
    models["slv"] = calibrate_leverage(
        HestonParams(0.04, 1.5, 0.045, 0.6, -0.7),
        ssvi,
        Market.flat(100.0, 0.02, 0.01),
        1.0,
        mix=0.7,
        n_particles=20_000,
        rng=2,
    )
    out: dict = {
        "slv_leverage": {"times": models["slv"].leverage.times, "values_head": models["slv"].leverage.values[:3]}
    }
    n = 20_000
    for name, model in models.items():
        out[name] = {
            "european": [
                european_mc(model, market, K, 1.0, cp, n_paths=n, rng=7)
                for K in (80.0, 100.0, 120.0)
                for cp in ("call", "put")
            ],
            "asian": [asian_mc(model, market, 100.0, 1.0, 12, "call", n_paths=n, rng=3)],
            "barrier": [barrier_mc(model, market, 100.0, 80.0, 1.0, "down-out", "put", n_monitor=52, n_paths=n, rng=5)],
            "american": [american_lsm(model, market, 100.0, 1.0, "put", n_exercise=20, n_paths=n, rng=11)],
            "paths": simulate(model, market, [0.5, 1.0], 1_000, rng=1)[:5],
        }
    return out


def _simulated_market(noise_bp=15, seed=1):
    """Frozen copy of scripts/run_calibration.py:simulated_market (A0): Heston quotes plus noise on a flat market."""
    from optlab.core import HestonParams, Market, heston_iv

    true = HestonParams(v0=0.025, kappa=1.8, theta=0.04, xi=0.75, rho=-0.75)
    rng = np.random.default_rng(seed)
    market = Market.flat(100.0, 0.04, 0.012)
    slices = []
    for T in (1 / 24, 1 / 12, 1 / 6, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        F, D = float(market.F(T)), float(market.D(T))
        atm = np.sqrt(true.theta + (true.v0 - true.theta) * (1 - np.exp(-true.kappa * T)) / (true.kappa * T))
        k = np.linspace(-3.0, 2.0, 31) * atm * np.sqrt(T)
        K = F * np.exp(k)
        iv = heston_iv(F, K, T, true, D) + rng.normal(0, noise_bp * 1e-4, k.size)
        ok = np.isfinite(iv)
        k, K, iv = k[ok], K[ok], iv[ok]
        slices.append(
            {
                "T": T,
                "F": F,
                "D": D,
                "K": K,
                "k": k,
                "iv": iv,
                "spread_iv": np.full(k.size, 4 * noise_bp * 1e-4),
                "expiry": f"{T:.3f}y",
            }
        )
    return market, slices


_FIT: dict = {}


def _simulated_fit():
    if not _FIT:
        from optlab.core.pipeline import calibrate_all

        market, slices = _simulated_market()
        _FIT.update(
            market=market,
            slices=slices,
            fit=calibrate_all(market, slices, mix=0.5, n_particles=40_000, log=lambda *a: None),
        )
    return _FIT


def calibration_simulated() -> dict:
    from optlab.core.pipeline import exotics_table, fit_errors

    f = _simulated_fit()
    fit, market, slices = f["fit"], f["market"], f["slices"]
    lev = fit["slv"].leverage
    return {
        "surface": fit["surface"].to_dict(),
        "ssvi_start": fit["ssvi"].to_dict(),
        "arbitrage": fit["arbitrage"],
        "heston": fit["heston"].to_dict(),
        "leverage": {"times": lev.times, "x_grids": lev.x_grids, "values": lev.values},
        "errors": numbers_only(fit_errors(fit, market, slices, n_paths=40_000, log=lambda *a: None)),
        "exotics": numbers_only(exotics_table(fit, market, n_paths=40_000)),
    }


def _simulated_context():
    from optlab.structured import PricingContext
    from optlab.structured.surfaces import FastSVISurface

    f = _simulated_fit()
    fit = f["fit"]
    return PricingContext(
        f["market"],
        FastSVISurface.from_dict(fit["surface"].to_dict()),
        fit["heston"],
        fit["slv"],
        dt.date(2026, 11, 4),
        "SPX",
        snapshot_id="golden-simulated",
    )


def _quote_numbers(q) -> dict:
    out = {
        "coupon_rate": q.coupon_rate,
        "coupon_rate_se": q.coupon_rate_se,
        "upfront": q.upfront,
        "target": q.target,
        "margin": q.margin,
        "result": q.result,
        "model": q.decision.model,
        "method": q.decision.method,
        "termination": q.result.termination,
        "expected_life": q.result.expected_life,
    }
    if q.control is not None:
        out["control"] = q.control
    return out


def presets_simulated() -> dict:
    from optlab.core import engines
    from optlab.structured import PRESETS, preset, quote

    engines.set_default("numpy")
    ctx = _simulated_context()
    return {name: _quote_numbers(quote(preset(name), ctx, n_paths=20_000, batch=10_000)) for name in PRESETS}


def _desk_numbers(q) -> dict:
    d = q.desk
    out = _quote_numbers(q)
    if d is not None:
        out["desk"] = numbers_only(
            plain({k: v for k, v in vars(d).items() if isinstance(v, (int, float, dict, list, tuple))})
        )
    if q.greeks is not None:
        g = q.greeks
        out["greeks"] = {
            "delta": g.delta,
            "delta_se": g.delta_se,
            "vega": g.vega,
            "vega_se": g.vega_se,
            "smoothing_bias": g.smoothing_bias,
            "smoothing_bias_se": g.smoothing_bias_se,
            "model": g.model,
        }
    return out


def structured_extra() -> dict:
    """Forced models, the desk's cost lines on the starting table, Greeks, and a two-position book."""
    from optlab.core import engines
    from optlab.structured import Book, Position, preset, quote
    from optlab.structured.defaults import resolve

    engines.set_default("numpy")
    ctx = _simulated_context()
    out: dict = {"forced": {}, "costs": {}, "greeks": {}}
    for model in ("bs", "heston", "slv"):
        out["forced"][model] = _quote_numbers(quote(preset("phoenix"), ctx, model=model, n_paths=20_000, batch=10_000))
    for name in ("barrier_reverse_convertible", "phoenix"):
        sheet = preset(name)
        r = resolve(None, sheet)
        out["costs"][name] = _desk_numbers(
            quote(sheet, ctx, n_paths=20_000, batch=10_000, costs=None if r.missing else r.inputs())
        )
    out["greeks"]["phoenix"] = _desk_numbers(
        quote(preset("phoenix"), ctx, n_paths=20_000, batch=10_000, with_greeks=True)
    )
    book = Book([Position("brc", preset("barrier_reverse_convertible"), -1.0), Position("phx", preset("phoenix"), 2.0)])
    res = book.price(ctx, n_paths=20_000, seed=2026, batch=10_000)
    out["book"] = {
        "value": res.value,
        "se": res.stderr,
        "model": res.model,
        "method": res.method,
        "positions": [{"name": p.name, "unit_price": p.unit_price} for p in res.positions],
    }
    return out


def _native_case(backend: str) -> dict:
    """The compiled engine on synthetic inputs (the simulated market): paths of the four models and of a basket with a
    local correlation, the fixed products of the CUDA study, the SLV leverage and a preset quoted on the engine."""
    from optlab import native
    from optlab.core import engines
    from optlab.core.models import BlackScholesModel, HestonModel
    from optlab.native import gpu
    from optlab.structured import preset, quote
    from optlab.structured.localcorr import LocalCorrelation
    from optlab.structured.multiasset import Basket

    if not native.available() or (backend == "gpu" and not native.gpu_available()):
        raise RuntimeError(f"the native engine ({backend}) is not available on this machine")
    engines.set_default("numpy")
    ctx = _simulated_context()
    out: dict = {"paths": {}, "fixed": {}}
    for kind in ("bs", "lv", "heston", "slv"):
        S = native.simulate(ctx.model(kind, 1.0), ctx.market, [0.25, 0.5, 1.0], 20_000, seed=7, backend=backend)
        out["paths"][kind] = np.asarray(S)
    names = ("SPX", "AAA", "BBB")
    corr = np.array([[1.0, 0.8, 0.6], [0.8, 1.0, 0.7], [0.6, 0.7, 1.0]])
    lv = ctx.model("lv", 1.0)
    mixer = LocalCorrelation(sigma=0.15, level=0.0, down=0.3, up=0.1)
    b = Basket(names, {n: lv for n in names}, {n: ctx.market for n in names}, corr, mixer=mixer)
    paths = native.simulate(b, None, [0.5, 1.0], 20_000, seed=9, backend=backend)
    assert isinstance(paths, dict)  # a basket: one array per name
    out["paths"]["basket_localcorr"] = {n: np.asarray(paths[n]) for n in names}
    bs, hes = BlackScholesModel(0.2), HestonModel(ctx.heston)
    m, n = ctx.market, 200_000
    out["fixed"]["bs_european"] = gpu.european(bs, m, 100.0, 1.0, "call", n_paths=n, seed=11, backend=backend)
    out["fixed"]["heston_european"] = gpu.european(hes, m, 100.0, 1.0, "put", n_paths=n, seed=11, backend=backend)
    out["fixed"]["bs_asian"] = gpu.asian(bs, m, 100.0, 1.0, 12, "call", n_paths=n, seed=11, backend=backend)
    out["fixed"]["heston_barrier"] = gpu.barrier(
        hes, m, 100.0, 80.0, 1.0, n_monitor=52, n_paths=n, seed=11, backend=backend
    )
    lev = native.calibrate_leverage(ctx.heston, ctx.surface, ctx.market, 1.0, 0.5, 20_000, rng=5, backend=backend)
    out["leverage"] = {"times": lev.leverage.times, "x_grids": lev.leverage.x_grids, "values": lev.leverage.values}
    engines.set_default(backend)  # a quote takes the process default, as on the desk
    try:
        out["phoenix"] = _quote_numbers(quote(preset("phoenix"), ctx, n_paths=20_000, batch=10_000))
    finally:
        engines.set_default("numpy")
    values = numbers_only(plain(out))
    assert isinstance(values, dict)
    return values


def native_cpu() -> dict:
    return _native_case("cpu")


def native_gpu() -> dict:
    return _native_case("gpu")


def _node() -> str:
    node = shutil.which("node") or os.environ.get("OPTLAB_NODE")
    if not node:
        raise RuntimeError("node is not on PATH (or set OPTLAB_NODE)")
    return node


def labjs() -> dict:
    """The JavaScript pricers of the Pages demo (docs/lab.js), frozen until session 2 replaces them."""
    js = (ROOT / "docs" / "lab.js").read_text(encoding="utf-8")
    cases = [
        (100, 100, 1.0, 0.04, 0.01, 0.20, "true"),
        (100, 90, 0.5, 0.03, 0.0, 0.35, "false"),
        (100, 120, 2.0, 0.05, 0.02, 0.15, "true"),
        (100, 105, 0.25, 0.0, 0.01, 0.25, "false"),
    ]
    h = '{"v0": 0.0228466, "kappa": 3.0892636, "theta": 0.0537246, "xi": 1.3077452, "rho": -0.6879607}'
    parts = {
        "bs": [f"W.bs({S},{K},{T},{r},{q},{v},{c}).price" for S, K, T, r, q, v, c in cases],
        "crr": [
            f"[W.crr({S},{K},{T},{r},{q},{v},{c},400,true),W.crr({S},{K},{T},{r},{q},{v},{c},400,false)]"
            for S, K, T, r, q, v, c in cases
        ],
        "american_pde": [f"W.american({S},{K},{T},{r},{q},{v},{c})" for S, K, T, r, q, v, c in cases],
        "exercise_boundary": ["W.exerciseBoundary(100,100,1,0.04,0.01,0.2,false)"],
        "geo_asian": [f"W.geoAsian({S},{K},{T},{r},{q},{v},12,{c})" for S, K, T, r, q, v, c in cases],
        "levy_asian": [f"W.levyAsian(100,{K},1.0,0.04,0.01,0.2,12,true)" for K in (90, 100, 110)],
        "asian_mc": ["W.asianMC(100,100,1.0,0.04,0.01,0.2,12,true,40000,7)"],
        "barrier": [
            f"[W.barrier(100,100,{H},1.0,0.04,0.01,0.25,'{kind}',true,false),"
            f"W.barrier(100,100,{H},1.0,0.04,0.01,0.25,'{kind}',true,true)]"
            for kind, H in (("down-out", 80), ("down-in", 85), ("up-out", 120), ("up-in", 130))
        ],
        "heston_fourier": [
            f"W.hestonPrice(100,{K},{T},0.04,0.01,{h},{c})"
            for K, T, c in ((100, 1.0, "true"), (119, 1.0, "true"), (85, 0.5, "false"), (110, 2.0, "false"))
        ],
        "lsm": [
            "(() => { const st = {product: 'american', call: false, model: 'bs', K: 100, T: 1, v: 0.2, r: 0.04, q: 0.01};"
            " const c = W.convergence(W.payoffs(st, W.simulate(st, 20000, 50, 3)).pay, 100); return c[c.length - 1]; })()"
        ],
        "heston_qe_barrier": [
            "(() => { const st = {product: 'european', call: true, model: 'heston', K: 119, T: 1, v: 0.2,"
            f" r: 0.04, q: 0.01, heston: {h}}};"
            " const c = W.convergence(W.payoffs(st, W.simulate(st, 40000, 252, 5)).pay, 100);"
            " return c[c.length - 1]; })()"
        ],
    }
    expr = "{" + ",".join(f'"{k}": [{",".join(v)}]' for k, v in parts.items()) + "}"
    script = f"global.window = {{}};\n{js}\nconst W = window.PricerWidget;\nconsole.log(JSON.stringify({expr}));"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "golden.js"
        path.write_text(script, encoding="utf-8")
        out = subprocess.run([_node(), str(path)], capture_output=True, text=True, check=True, timeout=300)
    version = subprocess.run([_node(), "--version"], capture_output=True, text=True, check=True).stdout.strip()
    return {"node": version.split(".")[0], "values": json.loads(out.stdout)}


# ---------------------------------------------------------------------------------------------------- local groups


def _spx_context():
    from optlab.structured import PricingContext

    return PricingContext.from_calibration(ROOT / "results" / "spx_calibration.json")


def presets_spx_numpy() -> dict:
    from optlab.core import engines
    from optlab.structured import PRESETS, preset, quote

    engines.set_default("numpy")
    ctx = _spx_context()
    return {name: _quote_numbers(quote(preset(name), ctx, n_paths=20_000, batch=10_000)) for name in PRESETS}


def presets_spx_gpu() -> dict:
    from optlab.core import engines
    from optlab.structured import PRESETS, preset, quote

    engines.set_default("gpu")
    try:
        ctx = _spx_context()
        return {name: _quote_numbers(quote(preset(name), ctx, n_paths=100_000, batch=25_000)) for name in PRESETS}
    finally:
        engines.set_default("numpy")


def _lake():
    from optlab.marketdata.lake import Lake
    from optlab.server.settings import Settings

    return Lake(Settings.load().lake)


def lake_derived() -> dict:
    from optlab.marketdata.derive.correl import correlation_at
    from optlab.marketdata.pit import market_at

    lake = _lake()
    out = {}
    for name, day in (("SPX", "2026-11-04"), ("SPX", "2020-03-23"), ("SX5E", "2026-11-04"), ("NDX", "2026-11-04")):
        snap = market_at(lake, name, day)
        # at model spot 100: the real level (spot_scale) is not recorded
        out[f"{name}@{day}"] = {
            "market": snap.market,
            "surface": snap.surface,
            "heston": snap.heston,
            "snapshot_id": snap.snapshot_id,
        }
    m, info = correlation_at(lake, ["SPX", "NDX", "SX5E"], "2026-11-04")
    out["correlation@2026-11-04"] = {"matrix": m, "info": numbers_only(plain(info))}
    return out


def _trades_pnl():
    from optlab.marketdata.replay import struck_sheet
    from optlab.structured import preset
    from optlab.structured.trade import Trade

    brc = preset(
        "barrier_reverse_convertible",
        **{
            "general.maturity_months": 12,
            "downside.barrier": 0.7,
            "general.underlying": "SPX",
            "general.notional": 100.0,
            "wrapper.solve": "upfront",
            "coupon.rate": 0.0257,
        },
    )
    s20 = dt.date(2020, 1, 15)
    brc26 = preset(
        "barrier_reverse_convertible",
        **{
            "general.maturity_months": 12,
            "downside.barrier": 0.7,
            "general.underlying": "SPX",
            "general.notional": 100.0,
            "wrapper.solve": "upfront",
            "coupon.rate": 0.06,
        },
    )
    s26 = dt.date(2026, 6, 15)
    return (
        Trade.from_term_sheet("BRC-2020", struck_sheet(brc, s20), 1.0, s20, s20),
        Trade.from_term_sheet("BRC-2026", struck_sheet(brc26, s26), -1.0, s26, s26),
    )


def pnl_cascade() -> dict:
    from optlab.core import engines
    from optlab.risk import RiskSettings
    from optlab.risk import pnl as P

    engines.set_default("numpy")
    settings = RiskSettings(n_paths=20_000, batch=10_000, engine="numpy")
    lake = _lake()
    t20, t26 = _trades_pnl()
    return {
        "2020-03-13": numbers_only(plain(P.explain_day(lake, [t20], "2020-03-12", "2020-03-13", None, settings))),
        "2026-11-04": numbers_only(plain(P.explain_day(lake, [t26], "2026-11-03", "2026-11-04", None, settings))),
    }


class _ShowcaseCopy:
    """A Desk on a copy of the showcase store, so the golden run never writes into the real one. The copy lives beside
    the data it comes from (`optlab-data/golden/work-*`, never the system temp folder) and is removed afterwards; if
    Windows still holds a file open, the folder stays there and a warning says so, but the result is kept."""

    def __init__(self, name: str):
        self.dir = local_dir() / f"work-{name}-{os.getpid()}"

    def __enter__(self):
        from optlab.server.api import Desk

        shutil.copytree(local_dir().parent / "showcase", self.dir / "showcase")
        database = self.dir / "showcase" / "optlab.sqlite"
        if not database.exists():  # a v0 showcase: imported into a database, which the golden numbers then check
            from optlab.server.persistence.migrate_v0 import migrate

            migrate(self.dir / "showcase", database)
        self.desk = Desk(database, engine="gpu", workers=6)
        self.desk.use_snapshot({"snapshot_id": SPX_MARKET})
        return self.desk

    def __exit__(self, *exc):
        import gc

        from optlab.core import engines

        self.desk.jobs.shutdown()
        self.desk.db.close()
        engines.set_default("numpy")
        del self.desk
        gc.collect()
        shutil.rmtree(self.dir, ignore_errors=True)
        if self.dir.exists():
            print(
                f"warning: {self.dir} could not be removed (a file is still open); delete it by hand", file=sys.stderr
            )
        return False


def _wait(desk, job_id: str) -> dict:
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        time.sleep(0.5)
    if j["state"] != "done":
        raise RuntimeError(f"job {job_id} {j['state']}: {j.get('error')}")
    return j["result"]


_TIMES: dict = {}


def showcase_book() -> dict:
    from optlab.server.risk_api import RiskApi

    with _ShowcaseCopy("book") as desk:
        t0 = time.perf_counter()
        book = _wait(desk, desk.price_book({"options": {"precision": "standard", "greeks": True}})["job"])
        _TIMES["showcase_book_s"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        greeks = _wait(desk, RiskApi(desk).run({"options": {"precision": "standard"}})["job"])
        _TIMES["showcase_greeks_s"] = time.perf_counter() - t0
    return {"book": numbers_only(plain(book)), "greeks": numbers_only(plain(greeks))}


def showcase_var() -> dict:
    from optlab.server.risk_api import RiskApi

    with _ShowcaseCopy("var") as desk:
        t0 = time.perf_counter()
        res = _wait(desk, RiskApi(desk).var({"options": {"precision": "standard"}, "workers": 5, "days": 500})["job"])
        _TIMES["showcase_var_s"] = time.perf_counter() - t0
    return numbers_only(plain(res))


# ADR-0017, agreed with Luca on 7 November 2026: the comparison across machines
ACROSS_RTOL = 1e-12  # groups with fixed inputs
SE_RATIO = 0.1  # recalibrating groups: a Monte Carlo number moves by at most this many of its standard errors
FIT_RTOL = 2e-2  # recalibrating groups: fitted parameters and the other numbers, relative
RECALIBRATING = ("calibration_simulated", "presets_simulated", "structured_extra")
# On one machine, the GPU's reductions are not bit for bit: the fixed products add each block's partial sums with
# atomicAdd(double) and the leverage fills its histograms with atomicAdd(float), in an order that changes from run to
# run. Measured over six runs (8 November 2026): prices identical, their SE within 8.6e-16 relative, the leverage within
# 1.3e-9; the paths and everything priced on them stay bit for bit. These parts get a tolerance, the rest none.
WITHIN = {"native_gpu": {"fixed": 1e-12, "leverage": 1e-8}}

GROUPS = {
    "closed_forms": ("ci", closed_forms),
    "mc_products": ("ci", mc_products),
    "calibration_simulated": ("ci", calibration_simulated),
    "presets_simulated": ("ci", presets_simulated),
    "structured_extra": ("ci", structured_extra),
    "labjs": ("ci", labjs),
    "native_cpu": ("native", native_cpu),  # versioned (synthetic inputs), needs the compiled engine
    "native_gpu": ("native", native_gpu),  # versioned, needs the engine and a CUDA device
    "presets_spx_numpy": ("local", presets_spx_numpy),
    "lake_derived": ("local", lake_derived),
    "pnl_cascade": ("local", pnl_cascade),
    "presets_spx_gpu": ("local", presets_spx_gpu),
    "showcase_book": ("local", showcase_book),
    "showcase_var": ("local", showcase_var),
}


# ----------------------------------------------------------------------------------------------- record and check


def path_of(group: str) -> Path:
    where, _ = GROUPS[group]
    return (local_dir() if where == "local" else HERE) / f"{group}.json"


def _meta(seconds: float) -> dict:
    import scipy

    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        commit = None
    return {
        "recorded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "seconds": round(seconds, 2),
    }


def compute(group: str) -> tuple[dict, float]:
    t0 = time.perf_counter()
    values = plain(GROUPS[group][1]())
    return values, time.perf_counter() - t0


def diff(a, b, rtol: float, path: str = "") -> list[str]:
    """Paths where `b` differs from the golden `a`: exact when rtol is 0, else relative (absolute below 1e-300)."""
    if isinstance(a, dict) and isinstance(b, dict) and "digest" in a and "digest" in b:
        if a["digest"] == b["digest"]:
            return []
        if not rtol:
            return [f"{path}: array digest differs (sum {a['sum']!r} -> {b['sum']!r})"]
        return diff(
            {k: a[k] for k in ("shape", "sum", "samples")}, {k: b[k] for k in ("shape", "sum", "samples")}, rtol, path
        )
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{path}/{k}: missing" for k in a if k not in b] + [f"{path}/{k}: new" for k in b if k not in a]
        for k in a:
            if k in b:
                out += diff(a[k], b[k], rtol, f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} -> {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff(x, y, rtol, f"{path}[{i}]")]
    if isinstance(a, float) and isinstance(b, (float, int)) and not isinstance(b, bool):
        if math.isnan(a) and math.isnan(b):
            return []
        if a == b or (rtol and abs(a - b) <= rtol * max(abs(a), abs(b), 1e-300)):
            return []
        rel = abs(a - b) / max(abs(a), 1e-300)
        return [f"{path}: {a!r} -> {b!r} (rel {rel:.2e})"]
    return [] if a == b else [f"{path}: {a!r} -> {b!r}"]


def _se_of(d: dict, key: str):
    """The standard error recorded beside a Monte Carlo number: `x_se`, `base_price` -> `base_se`, or the `se` /
    `stderr` of a {value | price | cost, se} record. None for a number without one (a fitted parameter, a frequency)."""
    for cand in (f"{key}_se", key[: -len("_price")] + "_se" if key.endswith("_price") else None):
        if cand and isinstance(d.get(cand), (int, float)) and not isinstance(d.get(cand), bool):
            return float(d[cand])
    if key in ("value", "price", "cost"):
        for cand in ("se", "stderr"):
            if isinstance(d.get(cand), (int, float)) and not isinstance(d.get(cand), bool):
                return float(d[cand])
    return None


def diff_recalibrating(a, b, path: str = "", se=None, worst=None) -> list[str]:
    """`diff` for a group that recalibrates, across machines: a number with a standard error within SE_RATIO of it,
    any other within FIT_RTOL. `worst` collects the largest gap of each kind: {"se": ..., "rel": ...}."""
    worst = {} if worst is None else worst
    if isinstance(a, dict) and isinstance(b, dict) and "digest" in a and "digest" in b:
        keep = ("shape", "sum", "samples")
        return diff_recalibrating({k: a[k] for k in keep}, {k: b[k] for k in keep}, path, None, worst)
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{path}/{k}: missing" for k in a if k not in b] + [f"{path}/{k}: new" for k in b if k not in a]
        for k in a:
            if k in b:
                out += diff_recalibrating(a[k], b[k], f"{path}/{k}", _se_of(a, k), worst)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} -> {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff_recalibrating(x, y, f"{path}[{i}]", None, worst)]
    if isinstance(a, float) and isinstance(b, (float, int)) and not isinstance(b, bool):
        if a == b or (math.isnan(a) and math.isnan(b)):
            return []
        if se is not None and se > 0:
            z = abs(a - b) / se
            worst["se"] = max(worst.get("se", 0.0), z)
            return [] if z <= SE_RATIO else [f"{path}: {a!r} -> {b!r} ({z:.3f} SE, at most {SE_RATIO})"]
        rel = abs(a - b) / max(abs(a), abs(b), 1e-300)
        worst["rel"] = max(worst.get("rel", 0.0), rel)
        return [] if rel <= FIT_RTOL else [f"{path}: {a!r} -> {b!r} (rel {rel:.2e}, at most {FIT_RTOL:g})"]
    return [] if a == b else [f"{path}: {a!r} -> {b!r}"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("record", "check", "list"))
    ap.add_argument("--group", nargs="*", default=None, help="default: every ci group, plus local ones with --local")
    ap.add_argument("--local", action="store_true", help="include the engine and local groups (engine, lake, showcase)")
    ap.add_argument("--rtol", type=float, default=0.0)
    ap.add_argument("--across", action="store_true", help="the tolerance across machines of ADR-0017 (hosted runners)")
    ap.add_argument("--force", action="store_true", help="record over an existing file")
    ap.add_argument("--dump", help="check: also write the computed values to this folder (to compare platforms)")
    args = ap.parse_args(argv)
    groups = args.group or [g for g, (w, _) in GROUPS.items() if w == "ci" or args.local]
    if args.action == "list":
        for g, (w, _) in GROUPS.items():
            print(f"{g:24s} {w:6s} {path_of(g)}")
        return 0
    failed = 0
    for g in groups:
        target = path_of(g)
        values, seconds = compute(g)
        if args.action == "record":
            if target.exists() and not args.force:
                print(f"{g}: {target} exists, not overwritten (--force)")
                failed += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps({"meta": _meta(seconds) | {"times": dict(_TIMES)}, "values": values}, indent=1),
                encoding="utf-8",
            )
            print(f"{g}: recorded in {seconds:.1f} s -> {target}")
        else:
            gold = json.loads(target.read_text(encoding="utf-8"))
            if args.dump:
                Path(args.dump).mkdir(parents=True, exist_ok=True)
                (Path(args.dump) / f"{g}.json").write_text(
                    json.dumps({"meta": _meta(seconds), "values": values}, indent=1), encoding="utf-8"
                )
            if args.across and g in RECALIBRATING:
                gaps: dict = {}
                d = diff_recalibrating(gold["values"], values, worst=gaps)
                rule = f"across: {gaps.get('se', 0.0):.3f} SE and rel {gaps.get('rel', 0.0):.2e} at most"
                rule += f"; allowed {SE_RATIO} SE, rel {FIT_RTOL:g}"
            elif g in WITHIN and not args.across and not args.rtol:
                parts = WITHIN[g]
                d = [
                    x
                    for k in gold["values"]
                    for x in diff(gold["values"][k], values.get(k), parts.get(k, 0.0), f"/{k}")
                ]
                d += [f"/{k}: new" for k in values if k not in gold["values"]]
                rule = "bit for bit but " + ", ".join(f"{k} within {r:g}" for k, r in parts.items())
            else:
                rtol = ACROSS_RTOL if args.across else args.rtol
                d = diff(gold["values"], values, rtol)
                rels = [float(x) for line in d for x in re.findall(r"\(rel ([0-9.e+-]+)\)", line)]
                rule = (f"max rel {max(rels):.2e}, " if rels else "") + f"rtol {rtol:g}"
            print(f"{g}: {'OK' if not d else f'{len(d)} differences'} ({seconds:.1f} s, {rule})")
            for line in d[:20]:
                print("   ", line)
            failed += bool(d)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
