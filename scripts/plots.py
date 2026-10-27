"""Report charts, in the palette of the personal site (paper background, ink text)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from optlab import heston_iv  # noqa: E402

PAPER, INK, MUTED, LINE = "#fbfaf7", "#17191e", "#5d6370", "#e3e0d7"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"  # validated categorical slots 1-3

plt.rcParams.update({
    "figure.facecolor": PAPER, "axes.facecolor": PAPER, "savefig.facecolor": PAPER,
    "font.family": "serif", "font.serif": ["Georgia", "DejaVu Serif"],
    "font.size": 9, "axes.edgecolor": LINE, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": LINE,
    "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "lines.linewidth": 2,
})


def plot_smiles(slices, surface, heston, errors, path):
    n = len(slices)
    cols = 3
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(9, 2.6 * rows), squeeze=False)
    for ax, s, err in zip(axes.ravel(), slices, errors):
        T, k = s["T"], s["k"]
        half = 0.5 * np.nan_to_num(s.get("spread_iv", np.zeros_like(k)))
        ax.errorbar(k, 100 * s["iv"], yerr=100 * half, fmt="o", ms=2.5, color=MUTED, ecolor=LINE, elinewidth=1,
                    label="market (mid, bid-ask)")
        kk = np.linspace(k.min(), k.max(), 200)
        ax.plot(kk, 100 * surface.iv(kk, T), color=BLUE, label="SVI surface")
        ax.plot(kk, 100 * heston_iv(s["F"], s["F"] * np.exp(kk), T, heston, s["D"]), color=ORANGE, ls="--",
                lw=1.6, label="Heston")
        ax.plot(err["k_mc"], 100 * np.array(err["iv_slv"]), "D", ms=4.5, color=AQUA, mec=PAPER, mew=1,
                label="SLV (Monte Carlo)")
        expiry = s.get("expiry", "")
        title = f"{expiry}, T = {T:.2f} y" if expiry and not expiry.endswith("y") else f"T = {T:.2f} y"
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("log-moneyness ln(K/F)")
        ax.set_ylabel("implied vol (%)")
    for ax in axes.ravel()[n:]:
        ax.set_visible(False)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_errors(errors, path):
    T = np.array([e["T"] for e in errors])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.2))
    if errors[0].get("median_spread_bp") is not None:
        a1.plot(T, [e["median_spread_bp"] / 2 for e in errors], color=MUTED, ls=":", marker="s", ms=4,
                label="half bid-ask (median)")
    a1.plot(T, [e["svi_rmse_bp"] for e in errors], color=BLUE, marker="o", ms=5, label="SVI (final surface)")
    a1.plot(T, [e["ssvi_rmse_bp"] for e in errors], color=BLUE, marker="o", ms=4, ls=":", lw=1.2, mfc=PAPER,
            label="extended SSVI (start)")
    a1.plot(T, [e["heston_rmse_bp"] for e in errors], color=ORANGE, marker="^", ms=5, ls="--", label="Heston")
    a1.set_xscale("log")
    a1.set_xlabel("maturity (years, log scale)")
    a1.set_ylabel("RMSE vs quotes (bp of vol)")
    a1.set_title("Step 1-2: fit to market quotes")
    a1.legend()
    a2.fill_between(T, 0, [2 * e["mc_stderr_bp"] for e in errors], color=LINE, label="2 MC standard errors")
    a2.plot(T, [e["lv_mae_bp"] for e in errors], color=BLUE, marker="o", ms=5, label="Dupire local vol")
    a2.plot(T, [e["slv_mae_bp"] for e in errors], color=AQUA, marker="D", ms=5, label="SLV")
    a2.set_xscale("log")
    a2.set_xlabel("maturity (years, log scale)")
    a2.set_ylabel("mean |error| vs SVI surface (bp of vol)")
    a2.set_title("Step 3: Monte Carlo repricing of vanillas")
    a2.legend(loc="upper center", ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _levels(values, n=12):
    """Round contour levels between the 1st and 99th percentiles."""
    from matplotlib.ticker import MaxNLocator

    return MaxNLocator(n).tick_values(np.nanpercentile(values, 1), np.nanpercentile(values, 99))


def plot_surfaces(surface, leverage, path):
    """Local vol and leverage on the region the paths actually visit: -3.5 to +2.5 ATM standard deviations."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.4))
    T = np.linspace(max(surface.maturities[0], 1 / 52), surface.maturities[-1], 80)
    k = np.linspace(-0.5, 0.25, 151)
    KK, TT = np.meshgrid(k, T)
    sd = np.sqrt(surface.theta(TT))
    visited = (KK > -3.5 * sd) & (KK < 2.5 * sd)
    lv = np.where(visited, 100 * np.sqrt(surface.local_var(KK, TT)), np.nan)
    c1 = a1.contourf(KK, TT, lv, levels=_levels(lv), cmap="Blues", extend="both")
    fig.colorbar(c1, ax=a1, label="local vol (%)")
    a1.set_xlabel("log-moneyness ln(S/F)")
    a1.set_ylabel("maturity (years)")
    a1.set_title("Dupire local volatility from the SVI surface")
    a1.grid(False)
    times = leverage.times
    idx = np.unique(np.searchsorted(times, T).clip(0, len(times) - 1))
    tt = times[idx]
    vals = np.array([np.interp(k, leverage.x_grids[i], leverage.values[i]) for i in idx])
    KL, TL = np.meshgrid(k, tt)
    sdl = np.sqrt(surface.theta(TL))
    vals = np.where((KL > -3.5 * sdl) & (KL < 2.5 * sdl), vals, np.nan)
    c2 = a2.contourf(KL, TL, vals, levels=_levels(vals), cmap="Blues", extend="both")
    fig.colorbar(c2, ax=a2, label="L(t, x)")
    a2.set_xlabel("log-moneyness ln(S/F)")
    a2.set_ylabel("time (years)")
    a2.set_title("SLV leverage function (particle method)")
    a2.grid(False)
    fig.text(0.5, 0.01, "Blank areas lie beyond -3.5 / +2.5 ATM standard deviations, where almost no path goes.",
             ha="center", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_mix_sweep(sweep, exotics, path):
    mix = [s["mix"] for s in sweep]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.2))
    for ax, key, title in ((a1, "dao", "Down-and-out call, H 80 %"), (a2, "uao", "Up-and-out call, H 120 %")):
        y = np.array([s[key] for s in sweep])
        se = np.array([s[key + "_se"] for s in sweep])
        ax.fill_between(mix, y - 2 * se, y + 2 * se, color=LINE)
        ax.plot(mix, y, color=AQUA, marker="D", ms=5)
        ax.set_xlabel("mixing fraction (0 = local vol, 1 = full Heston vol-of-vol)")
        ax.set_ylabel("price (% of spot)")
        ax.set_title(f"{title}, 1 year: SLV, vanillas fixed")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
