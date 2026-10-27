"""Cox-Ross-Rubinstein binomial tree: the reference price for American options under Black-Scholes."""

from __future__ import annotations

import numpy as np


def _crr(S, K, T, r, q, vol, n, phi, american):
    dt = T / n
    u = np.exp(vol * np.sqrt(dt))
    d = 1.0 / u
    p = (np.exp((r - q) * dt) - d) / (u - d)
    if not 0.0 < p < 1.0:
        raise ValueError("CRR probability outside (0, 1): increase the number of steps")
    disc = np.exp(-r * dt)
    j = np.arange(n + 1)
    spots = S * u ** (n - 2 * j)
    values = np.maximum(phi * (spots - K), 0.0)
    for step in range(n - 1, -1, -1):
        values = disc * (p * values[:-1] + (1 - p) * values[1:])
        if american:
            spots = S * u ** (step - 2 * np.arange(step + 1))
            values = np.maximum(values, phi * (spots - K))
    return float(values[0])


def binomial_price(S, K, T, r, q, vol, cp="call", american=True, n=2000):
    """CRR price, averaged over n and n + 1 steps to damp the odd-even oscillation."""
    phi = 1.0 if str(cp).lower().startswith("c") else -1.0
    return 0.5 * (_crr(S, K, T, r, q, vol, n, phi, american)
                  + _crr(S, K, T, r, q, vol, n + 1, phi, american))
