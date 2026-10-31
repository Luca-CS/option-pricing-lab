"""Pricing context: the calibrated market and models, loaded once from results/spx_calibration.json."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import paths
from ..heston import HestonParams
from ..market import Market
from ..models import BlackScholesModel, HestonModel, Leverage, LocalVolModel, SLVModel
from ..svi import SVISurface

DEFAULT_CALIBRATION = paths().results / "spx_calibration.json"


@dataclass
class PricingContext:
    market: Market
    surface: SVISurface
    heston: HestonParams
    slv: SLVModel
    valuation: dt.date
    underlying: str = "SPX"

    @classmethod
    def from_calibration(cls, path: str | Path | None = None, underlying: str = "SPX") -> "PricingContext":
        cal = json.loads(Path(path or DEFAULT_CALIBRATION).read_text())
        heston = HestonParams(**cal["heston"])
        slv = SLVModel(heston, Leverage.from_dict(cal["slv"]["leverage"]), cal["slv"]["mix"])
        return cls(Market.from_dict(cal["market"]), SVISurface.from_dict(cal["surface"]), heston, slv,
                   dt.date.fromisoformat(cal["meta"]["snapshot"][:10]), underlying)

    @property
    def t_max(self) -> float:
        """Last calibrated maturity: beyond it the surface is extrapolated and the SLV leverage is not fitted."""
        return float(self.surface.maturities[-1])

    def atm_vol(self, T: float) -> float:
        return float(self.surface.iv(0.0, T))

    def model(self, name: str, T: float = 1.0):
        """'bs' (ATM vol at T), 'heston', 'lv' (Dupire on SVI) or 'slv'."""
        name = name.lower()
        if name == "bs":
            return BlackScholesModel(self.atm_vol(T))
        if name == "heston":
            return HestonModel(self.heston)
        if name == "lv":
            return LocalVolModel(self.surface)
        if name == "slv":
            return self.slv
        raise KeyError(f"unknown model {name!r}, expected bs, heston, lv or slv")
