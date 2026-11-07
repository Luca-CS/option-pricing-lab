"""optlab.core: option pricing under Black-Scholes, Heston, Dupire local volatility and Heston SLV.

Closed forms, the market container, the models and their simulation, the Monte Carlo pricers and the calibration
chain (SSVI, SVI, Heston, SLV leverage), plus the reference data every other package shares: the instrument registry
(`instruments`), the snapshot type (`snapshot`), the correlation repair (`correlation`) and the registry of compiled
engines (`engines`). It needs numpy and scipy only, does no I/O and starts no thread at import, and imports no other
optlab package (ADR-0004). The names below are its public API; the modules hold the rest.
"""

from optlab.core.blackscholes import (
    barrier_price,
    barrier_shift_discrete,
    black_price,
    bs_greeks,
    bs_price,
    geometric_asian_price,
    geometric_asian_price_curve,
    implied_vol,
)
from optlab.core.heston import HestonParams, calibrate_heston, heston_iv, heston_price
from optlab.core.market import Market
from optlab.core.models import BlackScholesModel, HestonModel, Leverage, LocalVolModel, SLVModel, simulate
from optlab.core.products import american_lsm, asian_mc, barrier_mc, european_mc
from optlab.core.slv import calibrate_leverage
from optlab.core.ssvi import SSVI, arbitrage_report, fit_ssvi
from optlab.core.svi import SVISurface, fit_svi_surface, svi_arbitrage_report
from optlab.core.tree import binomial_price

__all__ = [
    "Market",
    "black_price",
    "bs_price",
    "bs_greeks",
    "implied_vol",
    "geometric_asian_price",
    "geometric_asian_price_curve",
    "barrier_price",
    "barrier_shift_discrete",
    "binomial_price",
    "HestonParams",
    "heston_price",
    "heston_iv",
    "calibrate_heston",
    "SSVI",
    "fit_ssvi",
    "arbitrage_report",
    "SVISurface",
    "fit_svi_surface",
    "svi_arbitrage_report",
    "BlackScholesModel",
    "HestonModel",
    "LocalVolModel",
    "SLVModel",
    "Leverage",
    "simulate",
    "european_mc",
    "asian_mc",
    "barrier_mc",
    "american_lsm",
    "calibrate_leverage",
]
