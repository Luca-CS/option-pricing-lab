"""Deterministic rates and dividends: discount curve D(T) and forward curve F(T).

Every model in the library is simulated in the log-forward coordinate x = ln(S_t / F(t)),
so a term structure of rates and dividend yields only enters through these two curves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Market:
    """Spot, discount factors and forwards on a set of pillars.

    ln D and ln(F / S0) are interpolated linearly in T (piecewise-flat short rate and carry),
    and extrapolated flat in rate beyond the last pillar.
    """

    spot: float
    pillars: np.ndarray = field(default_factory=lambda: np.array([1.0]))
    discounts: np.ndarray = field(default_factory=lambda: np.array([1.0]))
    forwards: np.ndarray | None = None

    def __post_init__(self):
        t = np.asarray(self.pillars, dtype=float)
        d = np.asarray(self.discounts, dtype=float)
        f = np.full_like(t, self.spot) if self.forwards is None else np.asarray(self.forwards, dtype=float)
        order = np.argsort(t)
        self._t = np.concatenate([[0.0], t[order]])
        self._log_d = np.concatenate([[0.0], np.log(d[order])])
        self._log_f = np.concatenate([[0.0], np.log(f[order] / self.spot)])

    @classmethod
    def flat(cls, spot: float, r: float = 0.0, q: float = 0.0) -> "Market":
        """Constant short rate r and dividend yield q."""
        t = np.array([1.0])
        return cls(spot, t, np.exp(-r * t), spot * np.exp((r - q) * t))

    def _interp(self, values: np.ndarray, T):
        T = np.asarray(T, dtype=float)
        slope_last = (values[-1] - values[-2]) / (self._t[-1] - self._t[-2])
        out = np.interp(T, self._t, values)
        beyond = T > self._t[-1]
        return np.where(beyond, values[-1] + slope_last * (T - self._t[-1]), out)

    def D(self, T):
        """Discount factor to T."""
        return np.exp(self._interp(self._log_d, T))

    def F(self, T):
        """Forward price for delivery at T."""
        return self.spot * np.exp(self._interp(self._log_f, T))

    def zero_rate(self, T):
        T = np.maximum(np.asarray(T, dtype=float), 1e-8)
        return -np.log(self.D(T)) / T

    def carry_rate(self, T):
        """Average r - q over [0, T]."""
        T = np.maximum(np.asarray(T, dtype=float), 1e-8)
        return np.log(self.F(T) / self.spot) / T

    def scaled(self, new_spot: float) -> "Market":
        """Same curves with the spot rescaled (strikes can then be read in % of spot)."""
        return Market(new_spot, self._t[1:], np.exp(self._log_d[1:]), new_spot * np.exp(self._log_f[1:]))

    def to_dict(self) -> dict:
        return {
            "spot": float(self.spot),
            "pillars": self._t[1:].tolist(),
            "discounts": np.exp(self._log_d[1:]).tolist(),
            "forwards": (self.spot * np.exp(self._log_f[1:])).tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Market":
        return cls(d["spot"], np.array(d["pillars"]), np.array(d["discounts"]), np.array(d["forwards"]))
