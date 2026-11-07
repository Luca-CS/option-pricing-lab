"""The Vol Fitting Lab: fit SVI, SSVI, Heston and SLV on a day's real quotes, with errors measured against the bid-ask.

Every fit is a run in `calibration_runs`: its inputs, a hash of the quotes it read, its settings, its parameters and its
errors. A run can be replayed from that row alone, and gives the same parameters: the fits are deterministic and the SLV
particles have a seed.

An error inside the bid-ask is no error: "within spread" is the share of quotes where the model falls between implied bid
and implied ask. Quotes are personal-use or licensed data: `quote_table` is only for the local screen.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time

import numpy as np
import pandas as pd

from ..config import REPO_ROOT
from ..data import select_maturities
from ..heston import calibrate_heston, heston_iv
from ..pipeline import _thin, _weights
from ..slv import calibrate_leverage
from ..svi import fit_svi_surface, svi_arbitrage_report
from ..validation import mc_smile
from .lake import Lake, new_run_id, sql_str
from .quotes import CleanDay, clean_day

MODELS = ("svi", "ssvi", "heston", "slv")
DEFAULTS = {
    "targets": (1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0),
    "n_strikes_heston": 25,
    "mix": 0.5,
    "n_particles": 100_000,
    "n_paths": 100_000,
    "seed": 2026,
    "n_mc_strikes": 7,
    "max_rel_spread": 0.5,
}


def _code_version() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:
        return ""


def lab_days(lake: Lake) -> list[dict]:
    """Per name and source: the dates with quotes."""
    if not lake.has("option_quotes"):
        return []
    con = lake.connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT underlying, source, obs_date, quality FROM read_parquet("
            f"'{lake._pattern('option_quotes')}', hive_partitioning=true, hive_types_autocast=false) "
            "ORDER BY underlying, source, obs_date"
        ).df()
    finally:
        con.close()
    out = []
    for (name, source), g in rows.groupby(["underlying", "source"]):
        days = [pd.Timestamp(d).date().isoformat() for d in g["obs_date"]]
        out.append(
            {
                "underlying": name,
                "source": source,
                "days": days,
                "first": days[0],
                "last": days[-1],
                "n": len(days),
                "quality": sorted(set(g["quality"])),
            }
        )
    return out


def quotes_hash(cleaned: CleanDay, slices) -> str:
    h = hashlib.sha256()
    for s in slices:
        h.update(np.round(np.asarray([s["T"], s["F"], s["D"]]), 12).tobytes())
        h.update(np.round(np.asarray(s["K"], dtype=float), 8).tobytes())
        h.update(np.round(np.asarray(s["iv"], dtype=float), 10).tobytes())
    return h.hexdigest()[:16]


def quote_table(lake: Lake, name: str, day, source: str | None = None, targets=None) -> dict:
    """Implied bid, ask and mid per maturity, for the local screen only: derived from raw quotes."""
    cleaned = clean_day(lake, name, day, source)
    slices = select_maturities(cleaned.slices, tuple(targets or DEFAULTS["targets"]))
    table = cleaned.table
    out = []
    for s in slices:
        g = table[np.isclose(table["T"], s["T"])]
        out.append(
            {
                "T": s["T"],
                "expiry": s.get("expiry"),
                "moneyness": np.exp(g["k"]).tolist(),
                "iv": g["iv"].tolist(),
                "iv_bid": g["iv_bid"].tolist(),
                "iv_ask": g["iv_ask"].tolist(),
            }
        )
    return {
        "underlying": name,
        "day": cleaned.day.isoformat(),
        "source": cleaned.source,
        "slices": out,
        "origin": "observed",
    }


def _errors(model_iv, s) -> dict:
    err = np.asarray(model_iv, dtype=float) - np.asarray(s["iv"], dtype=float)
    half = 0.5 * np.nan_to_num(np.asarray(s["spread_iv"], dtype=float), nan=0.0)
    ok = np.isfinite(err)
    return {
        "T": float(s["T"]),
        "n": int(ok.sum()),
        "rmse_bp": float(np.sqrt(np.mean(err[ok] ** 2)) * 1e4) if ok.any() else None,
        "bias_bp": float(np.mean(err[ok]) * 1e4) if ok.any() else None,
        "within_spread": float(np.mean(np.abs(err[ok]) <= half[ok])) if ok.any() else None,
        "median_half_spread_bp": float(np.median(half) * 1e4),
    }


def _overall(rows: list[dict]) -> dict:
    n = sum(r["n"] for r in rows)
    if not n:
        return {"n": 0, "rmse_bp": None, "within_spread": None}
    rmse = np.sqrt(sum(r["n"] * r["rmse_bp"] ** 2 for r in rows) / n)
    within = sum(r["n"] * r["within_spread"] for r in rows) / n
    return {"n": n, "rmse_bp": float(rmse), "within_spread": float(within)}


def _curves(fn, slices) -> list[dict]:
    """Model smile per maturity on a regular grid of log-moneyness, for the chart."""
    out = []
    for s in slices:
        k = np.linspace(s["k"].min(), s["k"].max(), 41)
        out.append({"T": float(s["T"]), "moneyness": np.exp(k).tolist(), "iv": np.asarray(fn(k, s)).tolist()})
    return out


def fit_day(cleaned: CleanDay, models=MODELS, settings: dict | None = None) -> dict:
    """{model: {params, by_maturity, overall, curves, seconds}} on one cleaned day."""
    cfg = {**DEFAULTS, **(settings or {})}
    slices = select_maturities(cleaned.slices, tuple(cfg["targets"]))
    out = {"quotes_hash": quotes_hash(cleaned, slices), "settings": cfg, "maturities": [float(s["T"]) for s in slices]}
    fits = {}
    surface = ssvi = None
    if {"svi", "ssvi", "slv"} & set(models):
        t0 = time.perf_counter()
        surface, ssvi = fit_svi_surface(slices, [_weights(s) for s in slices])
        fit_seconds = time.perf_counter() - t0
        if "svi" in models:
            rows = [_errors(surface.iv(s["k"], s["T"]), s) for s in slices]
            fits["svi"] = {
                "params": surface.to_dict(),
                "by_maturity": rows,
                "overall": _overall(rows),
                "seconds": fit_seconds,
                "curves": _curves(lambda k, s: surface.iv(k, s["T"]), slices),
                "arbitrage": svi_arbitrage_report(surface),
            }
        if "ssvi" in models:
            rows = [_errors(ssvi.iv(s["k"], s["T"]), s) for s in slices]
            fits["ssvi"] = {
                "params": ssvi.to_dict(),
                "by_maturity": rows,
                "overall": _overall(rows),
                "seconds": fit_seconds,
                "curves": _curves(lambda k, s: ssvi.iv(k, s["T"]), slices),
            }
    heston = None
    if {"heston", "slv"} & set(models):
        t0 = time.perf_counter()
        thin = [_thin(s, cfg["n_strikes_heston"]) for s in slices]
        heston, _ = calibrate_heston(thin, None, [_weights(s) for s in thin])
        seconds = time.perf_counter() - t0
        if "heston" in models:
            rows = [_errors(heston_iv(s["F"], s["K"], s["T"], heston, s["D"]), s) for s in slices]
            fits["heston"] = {
                "params": heston.to_dict(),
                "by_maturity": rows,
                "overall": _overall(rows),
                "seconds": seconds,
                "feller": heston.feller(),
                "curves": _curves(lambda k, s: heston_iv(s["F"], s["F"] * np.exp(k), s["T"], heston, s["D"]), slices),
            }
    if "slv" in models:
        t0 = time.perf_counter()
        T_max = max(s["T"] for s in slices)
        slv = calibrate_leverage(
            heston, surface, cleaned.market, T_max, mix=cfg["mix"], n_particles=cfg["n_particles"], rng=cfg["seed"]
        )
        rows = []
        curves = []
        for i, s in enumerate(slices):  # Monte Carlo smile on a few strikes, with its standard error
            k = np.linspace(
                max(s["k"].min(), -2.0 * np.sqrt(surface.theta(s["T"]))),
                min(s["k"].max(), 1.5 * np.sqrt(surface.theta(s["T"]))),
                cfg["n_mc_strikes"],
            )
            K = float(cleaned.market.F(s["T"])) * np.exp(k)
            iv, se = mc_smile(slv, cleaned.market, s["T"], K, cfg["n_paths"], cfg["seed"] + i)
            order = np.argsort(s["k"])
            market_iv = np.interp(k, s["k"][order], s["iv"][order])
            half = np.interp(k, s["k"][order], 0.5 * np.nan_to_num(s["spread_iv"][order], nan=0.0))
            err = np.asarray(iv) - market_iv
            rows.append(
                {
                    "T": float(s["T"]),
                    "n": len(k),
                    "rmse_bp": float(np.sqrt(np.mean(err**2)) * 1e4),
                    "bias_bp": float(np.mean(err) * 1e4),
                    "within_spread": float(np.mean(np.abs(err) <= half)),
                    "median_half_spread_bp": float(np.median(half) * 1e4),
                    "mc_stderr_bp": float(np.mean(se) * 1e4),
                }
            )
            curves.append(
                {
                    "T": float(s["T"]),
                    "moneyness": np.exp(k).tolist(),
                    "iv": np.asarray(iv).tolist(),
                    "se": np.asarray(se).tolist(),
                }
            )
        fits["slv"] = {
            "params": {
                "mix": cfg["mix"],
                "heston": heston.to_dict(),
                "n_particles": cfg["n_particles"],
                "seed": cfg["seed"],
            },
            "leverage_times": len(slv.leverage.times),
            "by_maturity": rows,
            "overall": _overall(rows),
            "curves": curves,
            "seconds": time.perf_counter() - t0,
        }
    out["fits"] = fits
    return out


def run_id_of(name: str, day, source: str, model: str, quotes: str, settings: dict) -> str:
    text = json.dumps([name, str(day), source, model, quotes, settings], sort_keys=True, default=str)
    return f"{model}-{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def run_fit(lake: Lake, name: str, day, source: str | None = None, models=MODELS, settings: dict | None = None) -> dict:
    """Fit and store one run per model."""
    cfg = {**DEFAULTS, **(settings or {})}
    cleaned = clean_day(lake, name, day, source, max_rel_spread=cfg["max_rel_spread"])
    result = fit_day(cleaned, models, cfg)
    version = _code_version()
    rows = []
    for model, fit in result["fits"].items():
        rid = run_id_of(name, cleaned.day, cleaned.source, model, result["quotes_hash"], cfg)
        fit["calibration_id"] = rid
        stored_fit = {k: fit[k] for k in ("by_maturity", "overall") if k in fit}
        stored_fit.update(
            quotes_hash=result["quotes_hash"], source=cleaned.source, settings=cfg, maturities=result["maturities"]
        )
        rows.append(
            {
                "underlying": name,
                "calibration_id": rid,
                "obs_date": cleaned.day,
                "model": model,
                "params": json.dumps(fit["params"], sort_keys=True),
                "fit": json.dumps(stored_fit, sort_keys=True, default=str),
                "seconds": fit["seconds"],
                "code_version": version,
            }
        )
    lake.ingest("calibration_runs", pd.DataFrame(rows), "vollab", "derived", new_run_id("vollab"))
    result.update(
        underlying=name,
        day=cleaned.day.isoformat(),
        source=cleaned.source,
        origin="observed",
        spot_scale=cleaned.spot / float(cleaned.market.spot),
    )
    return result


def list_runs(lake: Lake, underlying: str | None = None, limit: int = 200) -> list[dict]:
    if not lake.has("calibration_runs"):
        return []
    where = f"underlying = {sql_str(underlying)}" if underlying else ""
    rows = lake.read("calibration_runs", where=where, order="ingested_at DESC", columns="*")
    out = []
    for r in rows.head(limit).itertuples():
        fit = json.loads(r.fit)
        out.append(
            {
                "calibration_id": r.calibration_id,
                "underlying": r.underlying,
                "day": pd.Timestamp(r.obs_date).date().isoformat(),
                "model": r.model,
                "source": fit.get("source"),
                "rmse_bp": fit["overall"]["rmse_bp"],
                "within_spread": fit["overall"]["within_spread"],
                "seconds": float(r.seconds),
                "origin": r.origin,
                "code_version": (r.code_version or "")[:10],
                "ingested": str(r.ingested_at),
            }
        )
    return out


def reproduce(lake: Lake, calibration_id: str) -> dict:
    """Refit a run from its stored inputs and compare the parameters with the stored ones."""
    rows = lake.read("calibration_runs", where=f"calibration_id = {sql_str(calibration_id)}")
    if rows.empty:
        raise KeyError(f"no calibration run {calibration_id!r}")
    r = rows.iloc[0]
    stored = json.loads(r["fit"])
    settings = stored["settings"]
    settings["targets"] = tuple(settings["targets"])
    day = pd.Timestamp(r["obs_date"]).date()
    cleaned = clean_day(lake, r["underlying"], day, stored["source"], max_rel_spread=settings["max_rel_spread"])
    again = fit_day(cleaned, _needed(r["model"]), settings)
    same_quotes = again["quotes_hash"] == stored["quotes_hash"]
    fresh = json.dumps(again["fits"][r["model"]]["params"], sort_keys=True)
    gap = _largest_gap(json.loads(r["params"]), json.loads(fresh))
    return {
        "calibration_id": calibration_id,
        "same_quotes": same_quotes,
        "largest_parameter_gap": gap,
        "identical": fresh == r["params"],
        "stored_rmse_bp": stored["overall"]["rmse_bp"],
        "new_rmse_bp": again["fits"][r["model"]]["overall"]["rmse_bp"],
    }


def _needed(model: str) -> tuple:
    return {"svi": ("svi",), "ssvi": ("ssvi",), "heston": ("heston",), "slv": ("heston", "slv")}[model]


def _largest_gap(a, b) -> float:
    def flat(x):
        if isinstance(x, dict):
            return [v for key in sorted(x) for v in flat(x[key])]
        if isinstance(x, list):
            return [v for item in x for v in flat(item)]
        return [float(x)] if isinstance(x, (int, float)) and not isinstance(x, bool) else []

    fa, fb = np.array(flat(a)), np.array(flat(b))
    return float(np.abs(fa - fb).max()) if len(fa) == len(fb) and len(fa) else float("inf")


def public_summary(result: dict) -> dict:
    """A fit's result without curves of raw quotes: what a visitor of a shared desk may see."""
    return {
        k: result[k]
        for k in ("underlying", "day", "source", "origin", "fits", "maturities", "quotes_hash")
        if k in result
    }


__all__ = [
    "MODELS",
    "DEFAULTS",
    "lab_days",
    "quote_table",
    "fit_day",
    "run_fit",
    "list_runs",
    "reproduce",
    "public_summary",
]
