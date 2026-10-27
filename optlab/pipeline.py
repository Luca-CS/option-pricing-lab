"""Three-step calibration: implied surface (SSVI then raw SVI), Heston on vanillas, SLV leverage by
particles; then repricing of the vanillas and of a few exotics under every model."""

from __future__ import annotations

import time

import numpy as np

from .heston import HestonParams, calibrate_heston, heston_iv
from .market import Market
from .models import BlackScholesModel, HestonModel, LocalVolModel
from .products import american_lsm, asian_mc, barrier_mc
from .slv import calibrate_leverage
from .svi import fit_svi_surface, svi_arbitrage_report
from .validation import mc_smile


def _weights(s, floor=0.002):
    """Inverse bid-ask width in vol: tight quotes count more. Uniform when no spread is given."""
    spread = s.get("spread_iv")
    if spread is None:
        return np.ones_like(np.asarray(s["k"], dtype=float))
    w = 1.0 / np.maximum(np.nan_to_num(spread, nan=0.05), floor)
    return w / w.mean()


def _thin(s, n_max):
    """Keep at most n_max strikes per maturity, evenly spread in log-moneyness."""
    if len(s["k"]) <= n_max:
        return s
    idx = np.unique(np.linspace(0, len(s["k"]) - 1, n_max).round().astype(int))
    return {key: (val[idx] if isinstance(val, np.ndarray) and val.shape == s["k"].shape else val)
            for key, val in s.items()}


def calibrate_all(market: Market, slices, mix=0.5, n_particles=200_000, n_strikes_heston=30,
                  heston_x0: HestonParams | None = None, seed=2026, log=print):
    """Run the three steps. Returns a dict of fitted objects plus timing and error statistics."""
    out = {"timings": {}}
    t = time.time()
    surface, ssvi_start = fit_svi_surface(slices, [_weights(s) for s in slices])
    out["surface"], out["ssvi"], out["timings"]["surface_s"] = surface, ssvi_start, time.time() - t
    out["arbitrage"] = svi_arbitrage_report(surface)
    log(f"SVI surface ({out['timings']['surface_s']:.1f} s) {out['arbitrage']}")

    t = time.time()
    hq = [_thin(s, n_strikes_heston) for s in slices]
    heston, hsol = calibrate_heston(hq, heston_x0, [_weights(s) for s in hq])
    out["heston"], out["timings"]["heston_s"] = heston, time.time() - t
    log(f"Heston {heston} feller={heston.feller():.2f} ({out['timings']['heston_s']:.1f} s)")

    t = time.time()
    T_max = max(s["T"] for s in slices)
    slv = calibrate_leverage(heston, surface, market, T_max, mix=mix, n_particles=n_particles, rng=seed)
    out["slv"], out["timings"]["leverage_s"] = slv, time.time() - t
    log(f"Leverage: {len(slv.leverage.times)} time steps, {n_particles} particles "
        f"({out['timings']['leverage_s']:.1f} s)")
    return out


def fit_errors(fit, market: Market, slices, n_paths=400_000, seed=7, n_mc_strikes=9, log=print):
    """Implied-vol errors per maturity, in vol points (x 1e4 = basis points).

    SVI, SSVI and Heston are evaluated on every quote; Dupire local vol and SLV are repriced by Monte
    Carlo on n_mc_strikes strikes per maturity and compared with the SVI surface they target.
    """
    surface, ssvi, heston, slv = fit["surface"], fit["ssvi"], fit["heston"], fit["slv"]
    lv = LocalVolModel(surface)
    rows = []
    for i, s in enumerate(slices):
        T, F, D = s["T"], s["F"], s["D"]
        e_svi = surface.iv(s["k"], T) - s["iv"]
        e_ssvi = ssvi.iv(s["k"], T) - s["iv"]
        e_heston = heston_iv(F, s["K"], T, heston, D) - s["iv"]
        sd = float(np.sqrt(surface.theta(T)))
        k_mc = np.linspace(max(s["k"].min(), -2.0 * sd), min(s["k"].max(), 1.5 * sd), n_mc_strikes)
        K_mc = market.F(T) * np.exp(k_mc)
        target = surface.iv(k_mc, T)
        iv_lv, se_lv = mc_smile(lv, market, T, K_mc, n_paths, seed + i)
        iv_slv, se_slv = mc_smile(slv, market, T, K_mc, n_paths, seed + i)
        row = {
            "expiry": s.get("expiry", ""), "T": T, "n_quotes": int(len(s["k"])),
            "median_spread_bp": float(np.nanmedian(s["spread_iv"]) * 1e4) if "spread_iv" in s else None,
            "svi_rmse_bp": float(np.sqrt(np.mean(e_svi**2)) * 1e4),
            "ssvi_rmse_bp": float(np.sqrt(np.mean(e_ssvi**2)) * 1e4),
            "heston_rmse_bp": float(np.sqrt(np.nanmean(e_heston**2)) * 1e4),
            "lv_mae_bp": float(np.mean(np.abs(iv_lv - target)) * 1e4),
            "slv_mae_bp": float(np.mean(np.abs(iv_slv - target)) * 1e4),
            "slv_max_bp": float(np.max(np.abs(iv_slv - target)) * 1e4),
            "mc_stderr_bp": float(np.mean(se_slv) * 1e4),
            "k_mc": k_mc.tolist(), "iv_target": target.tolist(),
            "iv_lv": iv_lv.tolist(), "iv_slv": iv_slv.tolist(),
        }
        rows.append(row)
        log(f"T={T:.3f}  SVI {row['svi_rmse_bp']:.1f} bp  SSVI {row['ssvi_rmse_bp']:.0f} bp  "
            f"Heston {row['heston_rmse_bp']:.0f} bp  "
            f"LV {row['lv_mae_bp']:.1f} bp  SLV {row['slv_mae_bp']:.1f} bp (max {row['slv_max_bp']:.1f}, "
            f"MC s.e. {row['mc_stderr_bp']:.1f})")
    return rows


def exotics_table(fit, market: Market, T=1.0, n_paths=200_000, seed=11):
    """Same exotics under four models that (nearly) agree on vanillas: the model-risk table."""
    surface, heston, slv = fit["surface"], fit["heston"], fit["slv"]
    S0 = market.spot
    atm = float(surface.iv(0.0, T))
    models = {"Black-Scholes (ATM vol)": BlackScholesModel(atm), "Heston": HestonModel(heston),
              "Local vol (Dupire)": LocalVolModel(surface), f"SLV (mix {slv.mix:.2f})": slv}
    products = {
        "Down-and-out call, K 100 %, H 80 %": lambda m: barrier_mc(m, market, S0, 0.8 * S0, T, "down-out", "call",
                                                                   n_paths=n_paths, rng=seed),
        "Up-and-out call, K 100 %, H 120 %": lambda m: barrier_mc(m, market, S0, 1.2 * S0, T, "up-out", "call",
                                                                  n_paths=n_paths, rng=seed),
        "Down-and-in put, K 100 %, H 85 %": lambda m: barrier_mc(m, market, S0, 0.85 * S0, T, "down-in", "put",
                                                                 n_paths=n_paths, rng=seed),
        "Asian call, 12 monthly fixings, K 100 %": lambda m: asian_mc(m, market, S0, T, 12, "call",
                                                                       n_paths=n_paths, rng=seed),
        "American put, K 100 %, 50 dates": lambda m: american_lsm(m, market, S0, T, "put", 50,
                                                                  n_paths=n_paths // 2, rng=seed),
    }
    table = {}
    for pname, f in products.items():
        table[pname] = {}
        for mname, model in models.items():
            res = f(model)
            table[pname][mname] = {"price": res.price / S0 * 100, "stderr": res.stderr / S0 * 100}
    return table


def mix_sweep(fit, market: Market, mixes=(0.0, 0.25, 0.5, 0.75, 1.0), T=1.0, n_particles=200_000,
              n_paths=200_000, seed=13, log=print):
    """Down-and-out and up-and-out prices as the share of stochastic volatility goes from 0 (local vol)
    to 1 (full Heston vol-of-vol), each point with its own leverage calibration."""
    surface, heston = fit["surface"], fit["heston"]
    S0 = market.spot
    out = []
    for mix in mixes:
        slv = calibrate_leverage(heston, surface, market, T, mix=mix, n_particles=n_particles, rng=seed)
        dao = barrier_mc(slv, market, S0, 0.8 * S0, T, "down-out", "call", n_paths=n_paths, rng=seed)
        uao = barrier_mc(slv, market, S0, 1.2 * S0, T, "up-out", "call", n_paths=n_paths, rng=seed)
        atm, _ = mc_smile(slv, market, T, [market.F(T)], n_paths, seed)
        out.append({"mix": mix, "dao": dao.price / S0 * 100, "dao_se": dao.stderr / S0 * 100,
                    "uao": uao.price / S0 * 100, "uao_se": uao.stderr / S0 * 100,
                    "atm_iv_error_bp": float((atm[0] - surface.iv(0.0, T)) * 1e4)})
        log(f"mix {mix:.2f}: DOC {out[-1]['dao']:.3f}  UOC {out[-1]['uao']:.3f}  "
            f"ATM err {out[-1]['atm_iv_error_bp']:.1f} bp")
    return out
