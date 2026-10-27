"""Implied-volatility repricing of vanillas by Monte Carlo, used to check that a calibrated model
reproduces the surface it was fitted to."""

from __future__ import annotations

import numpy as np

from .blackscholes import implied_vol
from .market import Market
from .models import Model, simulate


def mc_smile(model: Model, market: Market, T: float, strikes, n_paths=200_000, rng=None, **sim_kwargs):
    """Monte Carlo implied vols at T for the given strikes.

    Uses out-of-the-money options (puts below the forward, calls above) and S_T as a control
    variate (its mean F_T is known), which removes most of the noise shared across strikes.
    Returns (iv, iv_stderr).
    """
    strikes = np.asarray(strikes, dtype=float)
    F, D = float(market.F(T)), float(market.D(T))
    S = simulate(model, market, [T], n_paths, rng, **sim_kwargs)[:, -1]
    ivs, ses = [], []
    for K in strikes:
        call = K >= F
        pay = np.maximum(S - K, 0.0) if call else np.maximum(K - S, 0.0)
        c = np.cov(pay, S)
        beta = c[0, 1] / c[1, 1]
        adj = pay - beta * (S - F)
        half = len(adj) // 2
        pairs = 0.5 * (adj[:half] + adj[half:2 * half])
        price, se = D * pairs.mean(), D * pairs.std(ddof=1) / np.sqrt(half)
        cp = "call" if call else "put"
        iv = implied_vol(price, F, K, T, D, cp)
        bump = implied_vol(price + se, F, K, T, D, cp)
        ivs.append(float(iv))
        ses.append(float(bump - iv))
    return np.array(ivs), np.array(ses)
