"""Faster evaluation of the SVI surface for path simulation.

Local volatility asks the surface for every path at one time per step. `SVISurface.derivatives` broadcasts that
scalar time over all paths, then interpolates theta, searches the slices and masks per path. With a scalar time
the same formulas need one interpolation and one slice pair: `FastSVISurface` takes that branch and returns the
same numbers bit for bit (same operations in the same order), so prices do not move.
"""

from __future__ import annotations

import numpy as np

from ..svi import SVISurface, svi_derivs


class FastSVISurface(SVISurface):
    def derivatives(self, k, T):
        if np.ndim(T) != 0:
            return super().derivatives(k, T)
        k = np.asarray(k, dtype=float)
        T = float(T)
        th, dth = float(self.theta(T)), float(self.dtheta(T))
        n = len(self.maturities)
        j = int(np.clip(np.searchsorted(self.maturities, T, side="left"), 0, n))
        if j == n:  # beyond the last pillar: last slice scaled by theta(T) / theta_n
            w, wk, wkk = svi_derivs(self.params[-1], k)
            s, ds = th / self.thetas[-1], dth / self.thetas[-1]
            return s * w, s * wk, s * wkk, ds * w
        lo = self.thetas[j - 1] if j > 0 else 0.0
        hi = self.thetas[j]
        alpha, dalpha = (th - lo) / (hi - lo), dth / (hi - lo)
        w0, wk0, wkk0 = self._slice_derivs(j - 1, k)
        w1, wk1, wkk1 = self._slice_derivs(j, k)
        return ((1 - alpha) * w0 + alpha * w1, (1 - alpha) * wk0 + alpha * wk1,
                (1 - alpha) * wkk0 + alpha * wkk1, dalpha * (w1 - w0))
