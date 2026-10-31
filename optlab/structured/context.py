"""Pricing context: the calibrated market and models, loaded once from results/spx_calibration.json."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from ..config import paths
from ..heston import HestonParams
from ..market import Market
from ..models import BlackScholesModel, HestonModel, Leverage, LocalVolModel, SLVModel
from ..svi import SVISurface
from .surfaces import FastSVISurface

DEFAULT_CALIBRATION = paths().results / "spx_calibration.json"


@dataclass
class PricingContext:
    market: Market
    surface: SVISurface
    heston: HestonParams
    slv: SLVModel
    valuation: dt.date
    underlying: str = "SPX"
    snapshot_id: str | None = None  # the market every price of this context refers to

    @classmethod
    def from_calibration(cls, path: str | Path | None = None, underlying: str = "SPX",
                         valuation: dt.date | str | None = None) -> "PricingContext":
        """The calibrated market of a results/*.json file; files without a snapshot time need `valuation`."""
        from ..marketdata import MarketSnapshot
        snap = MarketSnapshot.from_calibration_file(path or DEFAULT_CALIBRATION, underlying, valuation,
                                                    provenance=False)
        return cls.from_snapshot(snap)

    @classmethod
    def from_snapshot(cls, snap) -> "PricingContext":
        heston = HestonParams(**snap.heston)
        slv = SLVModel(heston, Leverage.from_dict(snap.slv["leverage"]), snap.slv["mix"])
        surface = FastSVISurface.from_dict(snap.surface)  # bit-identical to SVISurface, faster per step
        return cls(Market.from_dict(snap.market), surface, heston, slv, snap.valuation, snap.underlying,
                   snap.snapshot_id)

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
            from .fastmodels import TabulatedLocalVol
            return TabulatedLocalVol(self.surface)
        if name == "slv" and fast:
            from .fastmodels import fast_slv
            return fast_slv(self.slv)
        if name == "lv":
            return LocalVolModel(self.surface)
        if name == "slv":
            return self.slv
        raise KeyError(f"unknown model {name!r}, expected bs, heston, lv or slv")
