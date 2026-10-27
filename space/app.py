"""Option pricing lab: Gradio demo of the optlab library (Hugging Face Space).

Models are the ones calibrated on an SPX snapshot (spot rescaled to 100). Only calibrated
parameters are loaded here; the market quotes themselves are not part of the Space.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import gradio as gr
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
for root in (HERE, HERE.parent):  # the Space ships optlab/ next to app.py; the GitHub repo one level up
    if (root / "optlab").is_dir():
        sys.path.insert(0, str(root))
        break

from optlab import (BlackScholesModel, HestonModel, HestonParams, Leverage, LocalVolModel, Market,  # noqa: E402
                    SLVModel, SVISurface, american_lsm, asian_mc, barrier_mc, barrier_price,
                    barrier_shift_discrete, binomial_price, black_price, bs_price, calibrate_heston,
                    calibrate_leverage, european_mc, geometric_asian_price, heston_iv, heston_price,
                    implied_vol, simulate)

RESULTS = next(p for p in (HERE / "results", HERE.parent / "results") if p.is_dir())
FIGURES = next((p for p in (HERE / "figures", HERE.parent / "reports" / "figures") if p.is_dir()), None)
CAL = json.loads((RESULTS / "spx_calibration.json").read_text())

MARKET = Market.from_dict(CAL["market"])
SURFACE = SVISurface.from_dict(CAL["surface"])
HESTON = HestonParams(**CAL["heston"])
SLV = SLVModel(HESTON, Leverage.from_dict(CAL["slv"]["leverage"]), CAL["slv"]["mix"])
SNAPSHOT = CAL["meta"].get("snapshot", "")[:10]
T_MAX = float(SURFACE.maturities[-1])

PAPER, INK, MUTED, LINE = "#fbfaf7", "#17191e", "#5d6370", "#e3e0d7"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
plt.rcParams.update({
    "figure.facecolor": PAPER, "axes.facecolor": PAPER, "savefig.facecolor": PAPER,
    "font.family": "serif", "font.serif": ["DejaVu Serif"], "font.size": 9, "axes.edgecolor": LINE,
    "axes.labelcolor": INK, "text.color": INK, "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True,
    "grid.color": LINE, "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "lines.linewidth": 2,
})

MODELS = ["Black-Scholes", "Heston", "Local vol (Dupire)", "SLV (Heston + leverage)"]
PRODUCTS = ["European", "American", "Asian (arithmetic)", "Barrier"]


def _model(name, T, bs_vol):
    if name == "Black-Scholes":
        return BlackScholesModel(bs_vol)
    if name == "Heston":
        return HestonModel(HESTON)
    if name == "Local vol (Dupire)":
        return LocalVolModel(SURFACE)
    return SLV


def _flat_rates(T):
    r = float(MARKET.zero_rate(T))
    return r, r - float(MARKET.carry_rate(T))


def _references(product, cp, K, T, H, kind, n_fix, model_name, bs_vol):
    """Closed forms or trees that the Monte Carlo price can be checked against."""
    S0 = MARKET.spot
    F, D = float(MARKET.F(T)), float(MARKET.D(T))
    r, q = _flat_rates(T)
    refs = []
    if product == "European":
        if model_name == "Black-Scholes":
            refs.append(("Black-Scholes formula", float(black_price(F, K, T, bs_vol, D, cp))))
        elif model_name == "Heston":
            refs.append(("Heston, Fourier (Lewis)", float(heston_price(F, K, T, HESTON, D, cp)[0])))
        else:
            vol = float(SURFACE.iv(np.log(K / F), T))
            refs.append(("SVI market surface (the target)", float(black_price(F, K, T, vol, D, cp))))
    elif product == "American" and model_name == "Black-Scholes":
        refs.append(("CRR binomial tree, 2000 steps", binomial_price(S0, K, T, r, q, bs_vol, cp, True, 2000)))
        refs.append(("European (lower bound)", float(bs_price(S0, K, T, r, q, bs_vol, cp))))
    elif product == "American" and model_name == "Heston":
        refs.append(("European, Fourier (lower bound)", float(heston_price(F, K, T, HESTON, D, cp)[0])))
    elif product == "Asian (arithmetic)" and model_name == "Black-Scholes":
        fix = np.linspace(T / n_fix, T, n_fix)
        refs.append(("Geometric Asian, closed form (lower bound for a call)",
                     float(geometric_asian_price(S0, K, r, q, bs_vol, fix, cp))))
    elif product == "Barrier" and model_name == "Black-Scholes":
        Hs = barrier_shift_discrete(H, S0, bs_vol, 1 / 252)
        refs.append(("Closed form, Broadie-Glasserman-Kou daily correction",
                     barrier_price(S0, K, Hs, T, r, q, bs_vol, kind, cp)))
    return refs


def price(model_name, product, cp, strike_pct, T, barrier_kind, barrier_pct, n_fix, n_paths, bs_vol_pct, seed):
    t0 = time.time()
    cp = cp.lower()
    K, H = float(strike_pct), float(barrier_pct)
    T = float(T)
    n_paths = int(n_paths)
    atm = float(SURFACE.iv(0.0, T))
    bs_vol = atm if not bs_vol_pct else float(bs_vol_pct) / 100
    model = _model(model_name, T, bs_vol)
    rng = int(seed)
    if product == "European":
        res = european_mc(model, MARKET, K, T, cp, n_paths, rng)
    elif product == "American":
        res = american_lsm(model, MARKET, K, T, cp, 50, n_paths, rng)
    elif product == "Asian (arithmetic)":
        res = asian_mc(model, MARKET, K, T, int(n_fix), cp, n_paths=n_paths, rng=rng)
    else:
        bad = (barrier_kind.startswith("down") and H >= 100) or (barrier_kind.startswith("up") and H <= 100)
        if bad:
            return "A down barrier must be below the spot (100) and an up barrier above it.", None
        res = barrier_mc(model, MARKET, K, H, T, barrier_kind, cp, n_monitor=max(12, int(252 * T)),
                         n_paths=n_paths, rng=rng)
    elapsed = time.time() - t0

    lines = [f"### {res.price:.4f} % of spot",
             f"Monte Carlo, {n_paths:,} paths (antithetic): standard error {res.stderr:.4f}, "
             f"95 % interval [{res.price - 1.96 * res.stderr:.4f}, {res.price + 1.96 * res.stderr:.4f}], "
             f"{elapsed:.1f} s.", ""]
    refs = _references(product, cp, K, T, H, barrier_kind, int(n_fix), model_name, bs_vol)
    if refs:
        lines.append("| Reference | Value | Difference in standard errors |")
        lines.append("|---|---|---|")
        for name, val in refs:
            z = (res.price - val) / res.stderr if res.stderr > 0 else float("nan")
            lines.append(f"| {name} | {val:.4f} | {z:+.1f} |")
        lines.append("")
    lines.append(f"Forward {float(MARKET.F(T)):.3f}, discount factor {float(MARKET.D(T)):.4f}, "
                 f"ATM implied vol {100 * atm:.2f} % (SPX snapshot of {SNAPSHOT}, spot rescaled to 100)."
                 + (f" Black-Scholes vol used: {100 * bs_vol:.2f} %." if model_name == "Black-Scholes" else ""))
    return "\n".join(lines), _paths_figure(model, T, K, H if product == "Barrier" else None)


def _paths_figure(model, T, K, H):
    times = np.linspace(T / 100, T, 100)
    S = simulate(model, MARKET, times, 60, rng=1)
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.plot(np.concatenate([[0], times]), np.column_stack([np.full(len(S), MARKET.spot), S]).T,
            color=BLUE, lw=0.7, alpha=0.35)
    ax.axhline(K, color=INK, lw=1, ls="--")
    ax.text(0.0, K, " strike", va="bottom", color=INK, fontsize=8)
    if H is not None:
        ax.axhline(H, color=ORANGE, lw=1.5)
        ax.text(0.0, H, " barrier", va="bottom", color=INK, fontsize=8)
    ax.set_xlabel("time (years)")
    ax.set_ylabel("spot (start = 100)")
    ax.set_title("60 simulated paths", fontsize=9)
    fig.tight_layout()
    return fig


def surface_figure(T):
    T = float(T)
    F, D = float(MARKET.F(T)), float(MARKET.D(T))
    sd = float(np.sqrt(SURFACE.theta(T)))
    k = np.linspace(-3.5 * sd, 2.5 * sd, 160)
    K = F * np.exp(k)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.2))
    a1.plot(k, 100 * SURFACE.iv(k, T), color=BLUE, label="SVI surface (fitted to SPX)")
    a1.plot(k, 100 * heston_iv(F, K, T, HESTON, D), color=ORANGE, ls="--", lw=1.6, label="Heston (calibrated)")
    a1.set_xlabel("log-moneyness ln(K/F)")
    a1.set_ylabel("implied vol (%)")
    a1.set_title(f"Implied volatility, T = {T:.2f} y")
    a1.legend()
    a2.plot(k, 100 * SURFACE.local_vol(k, T), color=BLUE, label="Dupire local vol")
    a2.plot(k, SLV.leverage(T, k) * 100 * np.sqrt(max(HESTON.theta, 1e-4)), color=AQUA, ls="-.", lw=1.6,
            label="SLV leverage x sqrt(theta)")
    a2.set_xlabel("log-moneyness ln(S/F)")
    a2.set_ylabel("vol (%)")
    a2.set_title(f"Local vol and SLV leverage, t = {T:.2f} y")
    a2.legend()
    fig.tight_layout()
    return fig


def mix_experiment(mix, n_particles):
    """Recalibrate the leverage for a given mixing fraction and price two barriers."""
    t0 = time.time()
    model = calibrate_leverage(HESTON, SURFACE, MARKET, 1.0, mix=float(mix), n_particles=int(n_particles), rng=5)
    t_cal = time.time() - t0
    F = float(MARKET.F(1.0))
    strikes = F * np.exp(np.array([-0.2, -0.1, 0.0, 0.1]))
    S = simulate(model, MARKET, [1.0], 100_000, rng=6)[:, -1]
    D = float(MARKET.D(1.0))
    rows = []
    for K in strikes:
        cp = "call" if K >= F else "put"
        pay = np.maximum(S - K, 0) if cp == "call" else np.maximum(K - S, 0)
        iv = float(implied_vol(D * pay.mean(), F, K, 1.0, D, cp))
        rows.append(f"| {100 * K / F:.0f} % | {100 * float(SURFACE.iv(np.log(K / F), 1.0)):.2f} | {100 * iv:.2f} |")
    dao = barrier_mc(model, MARKET, 100, 80, 1.0, "down-out", "call", n_paths=100_000, rng=7)
    uao = barrier_mc(model, MARKET, 100, 120, 1.0, "up-out", "call", n_paths=100_000, rng=7)
    text = [f"Leverage calibrated with {int(n_particles):,} particles in {t_cal:.1f} s.", "",
            "**Vanillas, 1 year** (should stay on the market whatever the mix):", "",
            "| Strike / forward | Market (SVI) vol % | SLV vol % |", "|---|---|---|", *rows, "",
            "**Barriers, 1 year, 100 000 paths** (these move with the mix):", "",
            f"- down-and-out call K 100, H 80: **{dao.price:.3f}** (s.e. {dao.stderr:.3f})",
            f"- up-and-out call K 100, H 120: **{uao.price:.3f}** (s.e. {uao.stderr:.3f})"]
    return "\n".join(text)


def calibration_lab(v0, kappa, theta, xi, rho, noise_bp, seed):
    """Generate quotes from a known Heston model, add noise, calibrate, compare."""
    true = HestonParams(float(v0), float(kappa), float(theta), float(xi), float(rho))
    rng = np.random.default_rng(int(seed))
    mkt = Market.flat(100.0, 0.04, 0.012)
    quotes = []
    for T in (1 / 12, 0.25, 0.5, 1.0, 2.0):
        F, D = float(mkt.F(T)), float(mkt.D(T))
        k = np.linspace(-0.35, 0.2, 15) * np.sqrt(T)
        iv = heston_iv(F, F * np.exp(k), T, true, D) + rng.normal(0, float(noise_bp) * 1e-4, k.size)
        ok = np.isfinite(iv)
        quotes.append({"T": T, "F": F, "D": D, "K": F * np.exp(k[ok]), "iv": iv[ok]})
    t0 = time.time()
    fit, sol = calibrate_heston(quotes)
    elapsed = time.time() - t0
    names = ["v0", "kappa", "theta", "xi", "rho"]
    table = ["| Parameter | True | Calibrated |", "|---|---|---|"]
    for n, a, b in zip(names, true.as_array(), fit.as_array()):
        table.append(f"| {n} | {a:.4f} | {b:.4f} |")
    rmse = 1e4 * np.sqrt(np.mean(sol.fun**2))
    text = "\n".join(table + ["", f"Fit RMSE {rmse:.1f} bp of vol for {noise_bp} bp of noise, "
                                  f"{sol.nfev} evaluations, {elapsed:.1f} s. Feller ratio of the fit: "
                                  f"{fit.feller():.2f}."])
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    colors = plt.cm.Blues(np.linspace(0.45, 0.95, len(quotes)))
    for q, c in zip(quotes, colors):
        k = np.log(q["K"] / q["F"])
        ax.plot(k, 100 * q["iv"], "o", ms=3, color=c)
        kk = np.linspace(k.min(), k.max(), 100)
        ax.plot(kk, 100 * heston_iv(q["F"], q["F"] * np.exp(kk), q["T"], fit, q["D"]), color=c, lw=1.4,
                label=f"T = {q['T']:.2f}")
    ax.set_xlabel("log-moneyness ln(K/F)")
    ax.set_ylabel("implied vol (%)")
    ax.set_title("Noisy quotes (dots) and calibrated Heston smiles (lines)", fontsize=9)
    ax.legend(fontsize=7)
    fig.tight_layout()
    return text, fig


def exotics_markdown():
    table = CAL["exotics"]
    models = list(next(iter(table.values())).keys())
    lines = ["| Product (1 year) | " + " | ".join(models) + " |", "|---" * (len(models) + 1) + "|"]
    for pname, row in table.items():
        lines.append(f"| {pname} | " + " | ".join(f"{row[m]['price']:.3f} ({row[m]['stderr']:.3f})"
                                                   for m in models) + " |")
    return "\n".join(lines)


def errors_markdown():
    lines = ["| Expiry | T (y) | Quotes | Half bid-ask | SVI | Heston | Local vol (MC) | SLV (MC) |",
             "|---|---|---|---|---|---|---|---|"]
    for e in CAL["errors"]:
        hs = e["median_spread_bp"] / 2 if e.get("median_spread_bp") is not None else float("nan")
        lines.append(f"| {e['expiry']} | {e['T']:.2f} | {e['n_quotes']} | {hs:.1f} | {e['svi_rmse_bp']:.1f} | "
                     f"{e['heston_rmse_bp']:.0f} | {e['lv_mae_bp']:.1f} | {e['slv_mae_bp']:.1f} |")
    return "\n".join(lines)


INTRO = f"""
# Option pricing lab

European, American (Longstaff-Schwartz), Asian and barrier options under Black-Scholes, Heston,
Dupire local volatility and a Heston stochastic-local-volatility model whose leverage function is
calibrated by the particle method. The surface, the Heston parameters and the leverage come from
an SPX option snapshot of {SNAPSHOT} (Yahoo Finance, spot rescaled to 100). Code and tests:
[github.com/Luca-CS/option-pricing-lab](https://github.com/Luca-CS/option-pricing-lab).
"""

THEME = gr.themes.Default(primary_hue="blue", neutral_hue="stone",
                          font=["Iowan Old Style", "Charter", "Cambria", "Georgia", "serif"],
                          font_mono=["ui-monospace", "Consolas", "monospace"])

with gr.Blocks(title="Option pricing lab") as demo:
    gr.Markdown(INTRO)
    with gr.Tab("Price an option"):
        with gr.Row():
            with gr.Column(scale=1):
                model_in = gr.Dropdown(MODELS, value="SLV (Heston + leverage)", label="Model")
                product_in = gr.Dropdown(PRODUCTS, value="Barrier", label="Product")
                cp_in = gr.Radio(["Call", "Put"], value="Call", label="Type")
                strike_in = gr.Slider(60, 140, value=100, step=1, label="Strike (% of spot)")
                T_in = gr.Slider(0.1, round(min(2.0, T_MAX), 2), value=1.0, step=0.05, label="Maturity (years)")
                with gr.Accordion("Barrier and Asian settings", open=False):
                    kind_in = gr.Dropdown(["down-out", "down-in", "up-out", "up-in"], value="down-out",
                                          label="Barrier type (monitored daily)")
                    barrier_in = gr.Slider(50, 150, value=80, step=1, label="Barrier (% of spot)")
                    fix_in = gr.Slider(2, 52, value=12, step=1, label="Asian fixings")
                with gr.Accordion("Simulation", open=False):
                    paths_in = gr.Slider(10_000, 200_000, value=50_000, step=10_000, label="Paths")
                    vol_in = gr.Number(value=None, label="Black-Scholes vol in % (empty: ATM vol of the surface)")
                    seed_in = gr.Number(value=1, precision=0, label="Seed")
                go = gr.Button("Price", variant="primary")
            with gr.Column(scale=2):
                out_md = gr.Markdown()
                out_plot = gr.Plot()
        go.click(price, [model_in, product_in, cp_in, strike_in, T_in, kind_in, barrier_in, fix_in, paths_in,
                         vol_in, seed_in], [out_md, out_plot])

    with gr.Tab("SPX surface"):
        gr.Markdown("Calibration in three steps: raw SVI slices under no-arbitrage constraints, Heston on the "
                    "same vanillas, then the SLV leverage by particles. Errors in basis points of implied vol; "
                    "local vol and SLV are repriced by Monte Carlo against the SVI surface they target.")
        gr.Markdown(errors_markdown())
        t_slider = gr.Slider(0.05, round(T_MAX, 2), value=0.5, step=0.05, label="Maturity (years)")
        surf_plot = gr.Plot(surface_figure(0.5))
        t_slider.change(surface_figure, t_slider, surf_plot)
        if FIGURES is not None and (FIGURES / "spx_smiles.png").exists():
            gr.Image(str(FIGURES / "spx_smiles.png"), label="Fit per maturity", show_label=True)
            gr.Image(str(FIGURES / "spx_errors.png"), label="Fit errors", show_label=True)

    with gr.Tab("Model risk"):
        gr.Markdown("The four models agree (almost) on vanillas but not on path-dependent payoffs. "
                    "Prices in % of spot, Monte Carlo standard errors in brackets.")
        gr.Markdown(exotics_markdown())
        if FIGURES is not None and (FIGURES / "spx_mix.png").exists():
            gr.Image(str(FIGURES / "spx_mix.png"), label="Barrier prices against the SLV mixing fraction")
        gr.Markdown("**Try it**: recalibrate the leverage with your own mixing fraction (0: pure local vol, "
                    "1: the full Heston vol-of-vol). Vanillas stay put; barriers move.")
        with gr.Row():
            mix_in = gr.Slider(0.0, 1.0, value=0.5, step=0.05, label="Mixing fraction")
            part_in = gr.Slider(20_000, 200_000, value=50_000, step=10_000, label="Particles")
        mix_go = gr.Button("Recalibrate and price", variant="primary")
        mix_out = gr.Markdown()
        mix_go.click(mix_experiment, [mix_in, part_in], mix_out)

    with gr.Tab("Calibration lab"):
        gr.Markdown("A simulated market: quotes generated by a Heston model you choose, plus Gaussian noise. "
                    "The calibration should find the parameters back.")
        with gr.Row():
            v0_in = gr.Slider(0.005, 0.15, value=0.03, step=0.005, label="v0")
            kappa_in = gr.Slider(0.2, 6.0, value=2.0, step=0.1, label="kappa")
            theta_in = gr.Slider(0.005, 0.15, value=0.05, step=0.005, label="theta")
            xi_in = gr.Slider(0.1, 1.5, value=0.6, step=0.05, label="xi (vol of vol)")
            rho_in = gr.Slider(-0.95, 0.3, value=-0.7, step=0.05, label="rho")
        with gr.Row():
            noise_in = gr.Slider(0, 50, value=10, step=1, label="Noise (bp of vol)")
            lab_seed = gr.Number(value=0, precision=0, label="Seed")
        lab_go = gr.Button("Generate and calibrate", variant="primary")
        lab_md = gr.Markdown()
        lab_plot = gr.Plot()
        lab_go.click(calibration_lab, [v0_in, kappa_in, theta_in, xi_in, rho_in, noise_in, lab_seed],
                     [lab_md, lab_plot])

if __name__ == "__main__":
    demo.launch(theme=THEME)
