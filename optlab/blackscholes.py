"""Closed forms under Black-Scholes: vanillas, Greeks, implied volatility, discrete geometric
Asian and continuously monitored single barriers (Reiner-Rubinstein)."""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr

SQRT_2PI = np.sqrt(2.0 * np.pi)


def _npdf(x):
    return np.exp(-0.5 * x * x) / SQRT_2PI


def _sign(cp) -> np.ndarray:
    """+1 for a call, -1 for a put. Accepts 'call'/'put', 'c'/'p' or +-1."""
    if isinstance(cp, str):
        return np.array(1.0 if cp.lower().startswith("c") else -1.0)
    return np.sign(np.asarray(cp, dtype=float))


def black_price(F, K, T, vol, D=1.0, cp="call"):
    """Black (1976) price of a European option on the forward F, discounted with D."""
    F, K, T, vol, D = np.broadcast_arrays(*(np.asarray(a, dtype=float) for a in (F, K, T, vol, D)))
    phi = _sign(cp)
    sd = vol * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = np.log(F / K) / sd + 0.5 * sd
    d2 = d1 - sd
    price = D * phi * (F * ndtr(phi * d1) - K * ndtr(phi * d2))
    intrinsic = D * np.maximum(phi * (F - K), 0.0)
    return np.where(sd > 0, price, intrinsic)


def bs_price(S, K, T, r, q, vol, cp="call"):
    """Black-Scholes price with continuous rate r and dividend yield q."""
    return black_price(S * np.exp((r - q) * np.asarray(T)), K, T, vol, np.exp(-r * np.asarray(T)), cp)


def bs_greeks(S, K, T, r, q, vol, cp="call") -> dict:
    """Delta, gamma, vega (per 1.00 of vol), theta (per year) and rho."""
    phi = _sign(cp)
    sd = vol * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * vol**2) * T) / sd
    d2 = d1 - sd
    dq, dr = np.exp(-q * T), np.exp(-r * T)
    delta = phi * dq * ndtr(phi * d1)
    gamma = dq * _npdf(d1) / (S * sd)
    vega = S * dq * _npdf(d1) * np.sqrt(T)
    theta = (-S * dq * _npdf(d1) * vol / (2 * np.sqrt(T))
             - phi * r * K * dr * ndtr(phi * d2) + phi * q * S * dq * ndtr(phi * d1))
    rho = phi * K * T * dr * ndtr(phi * d2)
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


def black_vega(F, K, T, vol, D=1.0):
    sd = vol * np.sqrt(T)
    d1 = np.log(F / K) / sd + 0.5 * sd
    return D * F * _npdf(d1) * np.sqrt(T)


def implied_vol(price, F, K, T, D=1.0, cp="call", lo=1e-4, hi=5.0, tol=1e-10):
    """Black implied volatility, vectorised bisection followed by Newton polishing.

    Returns NaN where the price is outside the no-arbitrage bounds.
    """
    price, F, K, T, D = np.broadcast_arrays(*(np.asarray(a, dtype=float) for a in (price, F, K, T, D)))
    phi = np.broadcast_to(_sign(cp), price.shape)
    intrinsic = D * np.maximum(phi * (F - K), 0.0)
    upper = np.where(phi > 0, D * F, D * K)
    valid = (price > intrinsic + 1e-14 * D * F) & (price < upper)
    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    for _ in range(80):
        m = 0.5 * (a + b)
        too_high = black_price(F, K, T, m, D, phi) > price
        b = np.where(too_high, m, b)
        a = np.where(too_high, a, m)
        if np.nanmax(b - a) < 1e-7:
            break
    vol = 0.5 * (a + b)
    for _ in range(3):
        diff = black_price(F, K, T, vol, D, phi) - price
        vega = black_vega(F, K, T, vol, D)
        step = np.where(vega > 1e-12, diff / np.maximum(vega, 1e-300), 0.0)
        vol = np.clip(vol - step, lo, hi)
        if np.nanmax(np.abs(diff)) < tol:
            break
    return np.where(valid, vol, np.nan)


def geometric_asian_price(S, K, r, q, vol, fixings, cp="call"):
    """Discretely monitored geometric-average Asian with fixed strike, paid at the last fixing.

    ln G = mean(ln S_ti) is Gaussian, with mean ln S + (r - q - vol^2/2) * mean(t_i)
    and variance vol^2 * mean_ij(min(t_i, t_j)).
    """
    t = np.asarray(fixings, dtype=float)
    T = t[-1]
    mean = np.log(S) + (r - q - 0.5 * vol**2) * t.mean()
    var = vol**2 * np.minimum.outer(t, t).mean()
    fwd_g = np.exp(mean + 0.5 * var)
    return black_price(fwd_g, K, 1.0, np.sqrt(var), np.exp(-r * T), cp)


def barrier_price(S, K, H, T, r, q, vol, kind="down-out", cp="call"):
    """Continuously monitored single barrier without rebate (Reiner-Rubinstein, as in Haug).

    kind is 'down-out', 'down-in', 'up-out' or 'up-in'. Knock-ins come from in + out = vanilla.
    If the spot is already through the barrier the option is knocked (out: 0, in: vanilla).
    """
    direction, style = kind.split("-")
    vanilla = bs_price(S, K, T, r, q, vol, cp)
    breached = (S <= H) if direction == "down" else (S >= H)
    if breached:
        return 0.0 if style == "out" else float(vanilla)
    out = _barrier_out(S, K, H, T, r, q, vol, direction, _sign(cp))
    return float(out if style == "out" else vanilla - out)


def _barrier_out(S, K, H, T, r, q, vol, direction, phi):
    b = r - q
    sd = vol * np.sqrt(T)
    mu = (b - 0.5 * vol**2) / vol**2
    eta = 1.0 if direction == "down" else -1.0
    carry = S * np.exp((b - r) * T)
    disc = K * np.exp(-r * T)
    x1 = np.log(S / K) / sd + (1 + mu) * sd
    x2 = np.log(S / H) / sd + (1 + mu) * sd
    y1 = np.log(H * H / (S * K)) / sd + (1 + mu) * sd
    y2 = np.log(H / S) / sd + (1 + mu) * sd
    A = phi * carry * ndtr(phi * x1) - phi * disc * ndtr(phi * x1 - phi * sd)
    B = phi * carry * ndtr(phi * x2) - phi * disc * ndtr(phi * x2 - phi * sd)
    C = (phi * carry * (H / S) ** (2 * (mu + 1)) * ndtr(eta * y1)
         - phi * disc * (H / S) ** (2 * mu) * ndtr(eta * y1 - eta * sd))
    D = (phi * carry * (H / S) ** (2 * (mu + 1)) * ndtr(eta * y2)
         - phi * disc * (H / S) ** (2 * mu) * ndtr(eta * y2 - eta * sd))
    call = phi > 0
    if direction == "down" and call:
        return A - C if K > H else B - D
    if direction == "up" and call:
        return 0.0 if K > H else A - B + C - D
    if direction == "down":  # put
        return A - B + C - D if K > H else 0.0
    return B - D if K > H else A - C  # up-and-out put


BGK_BETA = 0.5825971579  # -zeta(1/2) / sqrt(2 pi)


def barrier_shift_discrete(H, S, vol, dt):
    """Broadie-Glasserman-Kou: a barrier monitored every dt behaves like a continuous barrier
    moved away from the spot by exp(beta * vol * sqrt(dt))."""
    up = H > S
    return H * np.exp((1 if up else -1) * BGK_BETA * vol * np.sqrt(dt))
