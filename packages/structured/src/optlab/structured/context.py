"""Pricing context: the calibrated market and models of one name, from a calibration file or the lake."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from optlab.core.heston import HestonParams
from optlab.core.market import Market
from optlab.core.models import BlackScholesModel, HestonModel, Leverage, LocalVolModel, SLVModel
from optlab.core.svi import SVISurface
from optlab.structured.surfaces import FastSVISurface

LEVERAGE_HORIZONS = (
    1.0,
    2.0,
    3.0,
    5.0,
    7.0,
    10.0,
)  # a leverage calibrated up to one of these serves every shorter product

MIX = 0.5  # the SLV mix of a context built without a calibrated leverage (a name read from the lake)
PRIOR_DYNAMICS = {"kappa": 3.09, "xi": 1.31, "rho": -0.69}  # of the versioned SPX calibration (27/10/2026)


def quick_heston(surface) -> dict:
    """Heston with the prior's dynamics, its variance levels set by the day's surface: fast and stable for a daily loop.
    The SLV leverage is what brings the vanillas back on the surface."""
    v0 = float(surface.theta(1 / 12)) * 12
    theta_ = max(float(surface.theta(2.0)) / 2.0, 1e-4)
    return {
        "v0": max(v0, 1e-4),
        "kappa": PRIOR_DYNAMICS["kappa"],
        "theta": theta_,
        "xi": PRIOR_DYNAMICS["xi"],
        "rho": PRIOR_DYNAMICS["rho"],
    }


@dataclass
class PricingContext:
    market: Market
    surface: SVISurface
    heston: HestonParams
    slv: SLVModel
    valuation: dt.date
    underlying: str = "SPX"
    snapshot_id: str | None = None  # the market every price of this context refers to
    slv_cache: dict = field(default_factory=dict, repr=False)  # leverages recalibrated per (mix, horizon)
    leverage_flat: bool = False  # read from the lake without a leverage: calibrated on the first SLV use
    payment_currency: str | None = None  # the currency this market pays in; None when it was not built with FX in mind

    @classmethod
    def from_calibration(
        cls,
        path: str | Path,
        underlying: str = "SPX",
        valuation: dt.date | str | None = None,
        slv_regression: str | None = None,
    ) -> PricingContext:
        """The calibrated market of a results/*.json file; files without a snapshot time need `valuation`.
        `slv_regression="local_linear"` recalibrates the SLV leverage at load (about 7 s) with the local linear
        regression of max(v, 0), which removes most of the ATM vanilla bias left by the stored leverage."""
        from optlab.core.snapshot import MarketSnapshot

        snap = MarketSnapshot.from_calibration_file(path, underlying, valuation, provenance=False)
        ctx = cls.from_snapshot(snap)
        if slv_regression:
            from optlab.core.slv import calibrate_leverage

            fresh = calibrate_leverage(
                ctx.heston,
                ctx.surface,
                ctx.market,
                ctx.t_max,
                mix=ctx.slv.mix,
                n_particles=200_000,
                rng=2026,
                regression=slv_regression,
                positive_part=slv_regression == "local_linear",
            )
            ctx.slv = fresh
            ctx.snapshot_id = f"{ctx.snapshot_id}+slv-{slv_regression}"
        return ctx

    @classmethod
    def from_snapshot(cls, snap) -> PricingContext:
        heston = HestonParams(**snap.heston)
        slv = SLVModel(heston, Leverage.from_dict(snap.slv["leverage"]), snap.slv["mix"])
        surface = FastSVISurface.from_dict(snap.surface)  # bit-identical to SVISurface, faster per step
        flat = (snap.provenance or {}).get("leverage", {}).get("mode") == "flat"
        return cls(
            Market.from_dict(snap.market),
            surface,
            heston,
            slv,
            snap.valuation,
            snap.underlying,
            snap.snapshot_id,
            leverage_flat=flat,
            payment_currency=snap.provenance.get("payment"),
        )

    def slv_at_mix(self, mix: float, horizon: float, n_particles: int = 200_000, seed: int = 2026):
        """The SLV model with its leverage recalibrated for another mixing fraction (cached): the vanillas stay on
        the surface while the share of stochastic vol of vol changes."""
        from optlab.core.slv import calibrate_leverage

        T_max = round(min(max(horizon, 1 / 12), self.t_max), 6)
        key = (round(float(mix), 6), T_max, n_particles, seed)
        if key not in self.slv_cache:
            self.slv_cache[key] = calibrate_leverage(
                self.heston, self.surface, self.market, T_max, mix=float(mix), n_particles=n_particles, rng=seed
            )
        return self.slv_cache[key]

    def slv_model(self, horizon: float = 1.0, mix: float | None = None):
        """The SLV model up to a horizon at a mixing fraction, the market's by default: the stored one, or a leverage calibrated once per
        horizon band when the market has none or the mix is another."""
        same = mix is None or abs(mix - self.slv.mix) < 1e-12
        if same and not self.leverage_flat:
            return self.slv
        band = next((h for h in LEVERAGE_HORIZONS if h >= horizon - 1e-9), max(horizon, LEVERAGE_HORIZONS[-1]))
        return self.slv_at_mix(self.slv.mix if same else mix, band)

    @property
    def t_max(self) -> float:
        """Last calibrated maturity: beyond it the surface is extrapolated and the SLV leverage is not fitted."""
        return float(self.surface.maturities[-1])

    def atm_vol(self, T: float) -> float:
        return float(self.surface.iv(0.0, T))

    def model(self, name: str, T: float = 1.0, fast: bool = False):
        """'bs' (ATM vol at T), 'heston', 'lv' (Dupire on SVI) or 'slv'. With `fast`, local vol is tabulated per
        step and the SLV leverage sits on uniform grids (fastmodels: about 1e-4 relative interpolation error, prices
        within a small fraction of the Monte Carlo error)."""
        name = name.lower()
        if name == "bs":
            return BlackScholesModel(self.atm_vol(T))
        if name == "heston":
            return HestonModel(self.heston)
        if name == "lv" and fast:
            from optlab.structured.fastmodels import TabulatedLocalVol

            return TabulatedLocalVol(self.surface)
        if name == "slv" and fast:
            from optlab.structured.fastmodels import fast_slv

            return fast_slv(self.slv_model(T))
        if name == "lv":
            return LocalVolModel(self.surface)
        if name == "slv":
            return self.slv_model(T)
        raise KeyError(f"unknown model {name!r}, expected bs, heston, lv or slv")
