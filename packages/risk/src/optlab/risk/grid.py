"""The risk grid: moneyness x tenors, and the tents that bump it.

Strikes are read in forward moneyness K / F(T), the coordinate of the surfaces (k = ln K/F) and of the simulations (sticky
moneyness): on a 1-year SPX node the forward and the spot differ by about 1 %. Tenors are years from the valuation date.

Tenor tents are a smooth partition of unity in T: between two neighbouring tenors the weight passes from one to the other
along the smootherstep s(u) = u^3 (10 - 15 u + 6 u^2), and the end tents are flat at one beyond the end tenors.

Strike tents are the hat functions of the strike nodes (linear between neighbours, flat beyond the end nodes) smoothed by a
Gaussian of width l = c(k) sqrt(T) in log moneyness, c narrow at the money and wide in the wings. A sharp tent cannot bump
implied vols by a point without butterfly arbitrage: a bump h of curvature phi'' adds about T sigma h phi'' to the Durrleman
g, so nodes 5 % apart need phi'' above -1 / (T sigma h), which the smoothing guarantees on the grid (checked on the SPX
surface for every node, h = +-1 point). The smoothing grows like the standard deviation of the log spot, against which
5 % of strike is a small step at long maturities, and is widest in the wings, where the base smile is closest to arbitrage. Smoothed hats keep exact first and second derivatives in k, and in T through l(T), so a bumped surface
keeps what the local variance needs.

Both families add up to one everywhere: the sum of all node bumps of size h is the parallel bump of size h, exactly.
"""

from __future__ import annotations

import numpy as np

STRIKES = (0.6, 0.7, 0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.4)  # forward moneyness K / F
TENORS = (1 / 12, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0)  # years
TENOR_LABELS = ("1m", "3m", "6m", "1y", "18m", "2y", "3y", "5y")
STRIKE_LABELS = tuple(f"{round(100 * m)}%" for m in STRIKES)
LOG_STRIKES = tuple(float(np.log(m)) for m in STRIKES)


def _smoother(u):
    u = np.clip(u, 0.0, 1.0)
    s = u**3 * (10.0 - 15.0 * u + 6.0 * u * u)
    ds = 30.0 * u * u * (1.0 - u) ** 2
    d2s = 60.0 * u * (1.0 - u) * (1.0 - 2.0 * u)
    return s, ds, d2s


def tents(nodes, x, order: int = 2):
    """Weights of every tent at x and their first and second derivatives in x, each of shape (len(nodes), *x.shape)."""
    nodes = np.asarray(nodes, dtype=float)
    x = np.asarray(x, dtype=float)
    n = len(nodes)
    phi = np.zeros((n, *x.shape))
    dphi = np.zeros_like(phi)
    d2phi = np.zeros_like(phi)
    if n == 1:
        phi[0] = 1.0
        return (phi, dphi, d2phi)[: order + 1]
    phi[0] = np.where(x <= nodes[0], 1.0, 0.0)
    phi[-1] = np.where(x >= nodes[-1], 1.0, 0.0)
    for i in range(n - 1):
        a, b = nodes[i], nodes[i + 1]
        inside = (x > a) & (x < b)
        if not np.any(inside):
            continue
        width = b - a
        s, ds, d2s = _smoother((x - a) / width)
        phi[i] += np.where(inside, 1.0 - s, 0.0)
        phi[i + 1] += np.where(inside, s, 0.0)
        dphi[i] += np.where(inside, -ds / width, 0.0)
        dphi[i + 1] += np.where(inside, ds / width, 0.0)
        d2phi[i] += np.where(inside, -d2s / width**2, 0.0)
        d2phi[i + 1] += np.where(inside, d2s / width**2, 0.0)
    exact = np.isin(x, nodes[1:-1])  # on an inner node: its own tent, nothing else
    if np.any(exact):
        for i in range(1, n - 1):
            phi[i] = np.where(x == nodes[i], 1.0, phi[i])
    return (phi, dphi, d2phi)[: order + 1]


STRIKE_SMOOTHING_ATM = (
    0.04  # Gaussian width of the strike tents per square root of a year at the money, in log moneyness
)
STRIKE_SMOOTHING_WING = 0.12  # the same in the wings, where the base surface is closest to butterfly arbitrage
STRIKE_SMOOTHING_SCALE = 0.15  # log moneyness over which the width passes from one to the other
SMOOTHING_T_FLOOR = 1 / 52  # the width stops shrinking below a week


def _ramps(nodes):
    """Each hat as a constant plus sum_m c_m (x - a_m)_+: [(constant, [(a_m, c_m), ...]) per tent]."""
    x = np.asarray(nodes, dtype=float)
    n = len(x)
    out = []
    for i in range(n):
        if n == 1:
            out.append((1.0, []))
        elif i == 0:
            d = x[1] - x[0]
            out.append((1.0, [(x[0], -1 / d), (x[1], 1 / d)]))
        elif i == n - 1:
            d = x[-1] - x[-2]
            out.append((0.0, [(x[-2], 1 / d), (x[-1], -1 / d)]))
        else:
            dl, dr = x[i] - x[i - 1], x[i + 1] - x[i]
            out.append((0.0, [(x[i - 1], 1 / dl), (x[i], -1 / dl - 1 / dr), (x[i + 1], 1 / dr)]))
    return out


_RAMPS = _ramps(LOG_STRIKES)


def smoothing_width(k, T):
    """l(k, T) = c(k) sqrt(max(T, floor)) and its derivatives (l_k, l_kk, l_T); c passes from the ATM to the wing width."""
    k = np.asarray(k, dtype=float)
    T = np.asarray(T, dtype=float)
    held = T < SMOOTHING_T_FLOOR
    root = np.sqrt(np.maximum(T, SMOOTHING_T_FLOOR))
    span, a2 = STRIKE_SMOOTHING_WING - STRIKE_SMOOTHING_ATM, STRIKE_SMOOTHING_SCALE**2
    e = np.exp(-k * k / a2)
    c = STRIKE_SMOOTHING_WING - span * e
    ck = span * e * 2 * k / a2
    ckk = span * e * (2 / a2 - 4 * k * k / a2**2)
    return c * root, ck * root, ckk * root, np.where(held, 0.0, 0.5 * c / root)


def strike_tents(k, T=1.0, rows=None):
    """Strike tents at log forward moneyness k and maturity T: (phi, dphi/dk, d2phi/dk2, dphi/dT), each (n_strikes, *shape).

    The hats are smoothed by a Gaussian whose width l(k, T) depends on the point where it is read: the result is still a
    partition of unity (the smoothing maps one to one). A ramp (x - a)_+ becomes R = u Phi(z) + l n(z), u = x - a, z = u / l,
    with dR/dl = n(z), so R_k = Phi(z) + n(z) l_k, R_kk = n(z) [(1 - z l_k)^2 / l + l_kk], R_T = n(z) l_T. `rows` computes only
    those tents (the others stay zero)."""
    from scipy.special import ndtr

    k = np.asarray(k, dtype=float)
    T = np.asarray(T, dtype=float)
    shape = np.broadcast(k, T).shape
    k = np.broadcast_to(k, shape)
    ell, lk, lkk, lT = smoothing_width(k, np.broadcast_to(T, shape))
    out = np.zeros((4, len(_RAMPS), *shape))
    cache = {}
    for i, (const, ramps) in enumerate(_RAMPS):
        if rows is not None and i not in rows:
            continue
        out[0, i] = const
        for a, c in ramps:
            if a not in cache:
                u = k - a
                z = u / ell
                pdf = np.exp(-0.5 * z * z) / np.sqrt(2 * np.pi)
                cdf = ndtr(z)
                cache[a] = (u * cdf + ell * pdf, cdf + pdf * lk, pdf * ((1 - z * lk) ** 2 / ell + lkk), pdf * lT)
            r, r1, r2, rT = cache[a]
            out[0, i] += c * r
            out[1, i] += c * r1
            out[2, i] += c * r2
            out[3, i] += c * rT
    return out[0], out[1], out[2], out[3]


def tenor_tents(T, order: int = 1):
    """Tents over the tenors, read in years."""
    return tents(TENORS, T, order)


def scalar_tenor_tents(T: float):
    """(psi, dpsi) of the tenor tents at one maturity, shape (n_tenors, 1): the same numbers as `tenor_tents`, faster."""
    nodes = TENORS
    n = len(nodes)
    psi, dpsi = np.zeros((n, 1)), np.zeros((n, 1))
    if T <= nodes[0]:
        psi[0, 0] = 1.0
    elif T >= nodes[-1]:
        psi[-1, 0] = 1.0
    else:
        i = int(np.searchsorted(nodes, T, side="right")) - 1
        a, b = nodes[i], nodes[i + 1]
        if T == a:
            psi[i, 0] = 1.0
        else:
            s, ds, _ = _smoother(np.array((T - a) / (b - a)))
            psi[i, 0], psi[i + 1, 0] = 1.0 - float(s), float(s)
            dpsi[i, 0], dpsi[i + 1, 0] = -float(ds) / (b - a), float(ds) / (b - a)
    return psi, dpsi


def weighted_factor(T) -> np.ndarray:
    """The weight of a vega at tenor T in the weighted vega: sqrt(1 year / T)."""
    return np.sqrt(1.0 / np.asarray(T, dtype=float))


def grid_json() -> dict:
    return {
        "strikes": list(STRIKES),
        "strike_labels": list(STRIKE_LABELS),
        "tenors": list(TENORS),
        "tenor_labels": list(TENOR_LABELS),
        "moneyness": "forward (K / F(T))",
        "tents": "smootherstep partition of unity, flat beyond the end nodes",
    }
