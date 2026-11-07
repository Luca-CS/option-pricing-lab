"""Implied volatility surfaces bumped by tents of the risk grid, with exact derivatives for Dupire.

    sigma'(k, T) = sigma(k, T) (1 + r(k, T)) + a(k, T)
    a(k, T) = parallel + sum_ij H_ij phi_i(k) psi_j(T) + sum_j t_j psi_j(T)
    r(k, T) = relative + sum_ij R_ij phi_i(k) psi_j(T)

H holds absolute node bumps (0.01 = one vol point), R relative ones (0.01 = +1 % of the vol), t an at-the-money term
structure move (every strike of a tenor). The tents phi (strikes, in log forward moneyness, smoothed more at longer
maturities) and psi (tenors) are those of `grid`, so the bumped total variance w = T sigma'^2 has exact dw/dk, d2w/dk2 and dw/dT and the
local variance is read on it like on the base surface. A skew rotation (that of `structured.surfaces.BumpedSurface`) is added in
the same layer.

For the path engine, `local_var_rows` gives the whole table of local variances (one row per step) at once: on the base surface's
own grid in k (the one `native` builds for the base), whose derivatives are computed once and kept, so a bumped table costs a
few vectorised operations instead of two surface calls per step. The grid follows the base's at-the-money standard deviation
rather than the bumped one; the engine reads any grid, and base and bumped tables then share their nodes.
"""

from __future__ import annotations

import numpy as np
from optlab.core.svi import g_function
from optlab.risk import grid
from optlab.structured.surfaces import BumpedSurface, surface_arbitrage


def _sigma_parts(surface, k, T):
    return _sigma_from_w(*surface.derivatives(k, T), T)


def _sigma_from_w(w, wk, wkk, wT, T):
    T = np.broadcast_to(np.asarray(T, dtype=float), w.shape)
    s = np.sqrt(np.maximum(w, 1e-300) / T)
    sk = wk / (2 * T * s)
    skk = (wkk - 2 * T * sk**2) / (2 * T * s)
    sT = (wT - s**2) / (2 * T * s)
    return T, s, sk, skk, sT


def _tenor(T):
    """(psi, dpsi) of the tenor tents, (n_tenors, *T.shape); a single maturity (the path engine asks one per step) once."""
    T = np.asarray(T, dtype=float)
    flat = T.reshape(-1)
    if T.ndim == 2 and T.shape[1] > 1 and np.all(T == T[:, :1]):  # a table: one maturity per row
        psi, dpsi = grid.tenor_tents(T[:, 0], order=1)
        return (
            np.broadcast_to(psi[:, :, None], (psi.shape[0], *T.shape)),
            np.broadcast_to(dpsi[:, :, None], (psi.shape[0], *T.shape)),
        )
    if flat.size and flat[0] == flat[-1] and np.all(flat == flat[0]):
        psi, dpsi = grid.scalar_tenor_tents(float(flat[0]))
        return (
            np.broadcast_to(psi[:, :1].reshape((-1,) + (1,) * T.ndim), (psi.shape[0], *T.shape)),
            np.broadcast_to(dpsi[:, :1].reshape((-1,) + (1,) * T.ndim), (psi.shape[0], *T.shape)),
        )
    return grid.tenor_tents(T, order=1)


def _field(matrix, k, T, phi_cache=None, tenor=None):
    """sum_ij M_ij phi_i(k, T) psi_j(T) and its derivatives in k, k twice and T; zero rows are skipped."""
    k = np.asarray(k, dtype=float)
    Tb = np.broadcast_to(np.asarray(T, dtype=float), np.broadcast(k, np.asarray(T)).shape)
    kb = np.broadcast_to(k, Tb.shape)
    rows = _rows(matrix)
    out = np.zeros(Tb.shape), np.zeros(Tb.shape), np.zeros(Tb.shape), np.zeros(Tb.shape)
    if not len(rows):
        return out
    psi, dpsi = tenor if tenor is not None else _tenor(Tb)
    phi, dphi, d2phi, phiT = phi_cache if phi_cache is not None else grid.strike_tents(kb, Tb, set(rows.tolist()))
    f, fk, fkk, fT = out
    for i in rows:
        a = np.tensordot(matrix[i], psi, axes=(0, 0))  # sum_j M_ij psi_j(T)
        aT = np.tensordot(matrix[i], dpsi, axes=(0, 0))
        f = f + phi[i] * a
        fk = fk + dphi[i] * a
        fkk = fkk + d2phi[i] * a
        fT = fT + phi[i] * aT + phiT[i] * a
    return f, fk, fkk, fT


def _rows(matrix):
    return np.flatnonzero(np.any(matrix != 0.0, axis=1))


def _term(vector, tenor):
    psi, dpsi = tenor
    v = np.asarray(vector, dtype=float)
    return np.tensordot(v, psi, axes=(0, 0)), np.tensordot(v, dpsi, axes=(0, 0))


class TentBumpedSurface:
    """A surface moved by absolute and relative tents of the grid (see the module docstring)."""

    def __init__(
        self,
        surface,
        nodes=None,
        relative_nodes=None,
        parallel: float = 0.0,
        relative: float = 0.0,
        term=None,
        skew: float = 0.0,
    ):
        self.base = surface
        self.skew = float(skew)  # the rotation of structured.surfaces.BumpedSurface, in the same layer
        shape = (len(grid.STRIKES), len(grid.TENORS))
        self.H = np.zeros(shape) if nodes is None else np.asarray(nodes, dtype=float).reshape(shape)
        self.R = np.zeros(shape) if relative_nodes is None else np.asarray(relative_nodes, dtype=float).reshape(shape)
        self.term = np.zeros(shape[1]) if term is None else np.asarray(term, dtype=float).reshape(shape[1])
        self.parallel, self.relative = float(parallel), float(relative)
        self.maturities = surface.maturities
        self._rows = set(_rows(self.H).tolist()) | set(_rows(self.R).tolist())
        self._has = (bool(np.any(self.H)), bool(np.any(self.R)), bool(np.any(self.term)))

    def derivatives(self, k, T):
        T_, s, sk, skk, sT = _sigma_parts(self.base, k, T)
        return self._assemble(T_, s, sk, skk, sT, np.broadcast_to(np.asarray(k, dtype=float), s.shape))

    def _assemble(self, T_, s, sk, skk, sT, kb):
        has_h, has_r, has_t = self._has
        cache = grid.strike_tents(kb, T_, self._rows) if self._rows else None
        tenor = _tenor(T_) if (self._rows or has_t) else None
        zero = np.zeros(s.shape)
        a, ak, akk, aT = _field(self.H, kb, T_, cache, tenor) if has_h else (zero, zero, zero, zero)
        r, rk, rkk, rT = _field(self.R, kb, T_, cache, tenor) if has_r else (zero, zero, zero, zero)
        if has_t:
            t, tT = _term(self.term, tenor)
            a, aT = a + t, aT + tT
        a = a + self.parallel
        if self.skew:
            b, bk, bkk, bT = _SKEW(self.skew)._bump(kb, T_)
            a, ak, akk, aT = a + b, ak + bk, akk + bkk, aT + bT
        r = r + self.relative
        s2 = s * (1 + r) + a
        sk2 = sk * (1 + r) + s * rk + ak
        skk2 = skk * (1 + r) + 2 * sk * rk + s * rkk + akk
        sT2 = sT * (1 + r) + s * rT + aT
        return T_ * s2**2, 2 * T_ * s2 * sk2, 2 * T_ * (sk2**2 + s2 * skk2), s2**2 + 2 * T_ * s2 * sT2

    def w(self, k, T):
        return self.derivatives(k, T)[0]

    def theta(self, T):
        """ATM total implied variance (the SLV leverage calibration reads it)."""
        T = np.asarray(T, dtype=float)
        return self.derivatives(np.zeros(T.shape), T)[0]

    def iv(self, k, T):
        return np.sqrt(np.maximum(self.w(k, T), 0.0) / np.asarray(T, dtype=float))

    def local_var(self, k, T, floor=1e-4, cap=4.0):
        T = np.maximum(np.asarray(T, dtype=float), 1.0 / 365)
        w, wk, wkk, wT = self.derivatives(k, T)
        g = g_function(w, wk, wkk, np.broadcast_to(k, w.shape))
        return np.clip(wT / np.maximum(g, 1e-6), floor, cap)

    def local_var_rows(self, t0, n_x: int, sd_range=(-8.0, 6.0), floor=1e-4, cap=4.0):
        """(lo, inv_dx, rows) of the local variance at each step start, on the base's grid (see the module docstring)."""
        lo, inv, k, T, w, wk, wkk, wT = _base_rows(self.base, np.asarray(t0, dtype=float), n_x, sd_range)
        Tm = np.broadcast_to(T[:, None], k.shape)
        w2, wk2, wkk2, wT2 = self._assemble(*_sigma_from_w(w, wk, wkk, wT, Tm), k)
        g = g_function(w2, wk2, wkk2, k)
        return lo, inv, np.clip(wT2 / np.maximum(g, 1e-6), floor, cap)


_BASE_ROWS: dict = {}


def _base_rows(base, t0, n_x, sd_range):
    """The base surface on the grid `native` builds for it, kept per (surface, steps): (lo, inv_dx, k, T, w, wk, wkk, wT)."""
    key = (id(base), t0.tobytes(), n_x, tuple(sd_range))
    hit = _BASE_ROWS.get(key)
    if hit is not None and hit[0] is base:
        return hit[1]
    n = len(t0)
    lo, inv, k = np.empty(n), np.empty(n), np.empty((n, n_x))
    T = np.maximum(t0, 1.0 / 365)
    out = [np.empty((n, n_x)) for _ in range(4)]
    for i, t in enumerate(T):
        sd = float(np.sqrt(base.w(0.0, float(t))))
        a, b = sd_range[0] * sd, sd_range[1] * sd
        lo[i], inv[i] = a, (n_x - 1) / (b - a)
        k[i] = np.linspace(a, b, n_x)
        for o, v in zip(out, base.derivatives(k[i], float(t))):
            o[i] = v
    rows = (lo, inv, k, T, *out)
    _BASE_ROWS[key] = (base, rows)
    while len(_BASE_ROWS) > 8:
        _BASE_ROWS.pop(next(iter(_BASE_ROWS)))
    return rows


class _SKEW(BumpedSurface):
    """The skew rotation alone, to read its bump (BumpedSurface._bump) on a grid."""

    def __init__(self, skew: float):
        self.vol, self.skew = 0.0, float(skew)


def bumped_surface(
    surface, parallel: float = 0.0, relative: float = 0.0, skew: float = 0.0, nodes=None, relative_nodes=None, term=None
):
    """The surface with every move applied in one layer; the base object itself when nothing moves (its cached tables stay
    valid)."""
    if np.isscalar(surface):  # a flat Black-Scholes name: only the parallel moves apply
        return float(surface) * (1 + relative) + parallel
    moved = parallel or relative or any(np.any(np.asarray(x)) for x in (nodes, relative_nodes, term) if x is not None)
    if moved:
        return TentBumpedSurface(surface, nodes, relative_nodes, parallel, relative, term, skew)
    return BumpedSurface(surface, 0.0, skew) if skew else surface


def arbitrage(surface, horizon: float) -> dict:
    """Static arbitrage of a (bumped) surface: butterfly and calendar, from `structured.surfaces.surface_arbitrage`."""
    return surface_arbitrage(surface, horizon)
