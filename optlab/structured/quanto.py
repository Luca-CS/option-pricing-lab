"""Quanto: a performance in one currency paid at a fixed rate in another.

With X the domestic price of a unit of the foreign currency, rho the correlation of S with X and sigma their volatilities, S under the
domestic measure drifts by -rho sigma_S sigma_X more than under the foreign one, and everything is discounted on the domestic curve:

    F_quanto(T) = F(T) exp(-rho sigma_X int_0^T sigma_S(t) dt)        D = D_domestic

So a name's `Market` is all that changes, and every pricer built on `Market` follows, including the coordinate x = ln(S / F) of the
simulations. A smile keeps its shape in forward moneyness.

Exact under Black-Scholes. On a surface, sigma_S(t) is the forward at-the-money volatility, so the adjustment is deterministic: under
local vol the true drift is -rho sigma_X sigma_LV(t, S), which is state dependent. rho and sigma_X are constants over the product's
life. A vol bump keeps the adjustment of the base market, so a vega does not see its small effect on the quanto forward.
"""

from __future__ import annotations

import numpy as np

from optlab.market import Market
from optlab.marketdata.universe import _BY_ID


def integrated_vol(T, vol) -> np.ndarray:
    """int_0^T sigma(t) dt. `vol` is a flat volatility or a surface, read at the money: the forward volatility is constant between
    the maturities of the surface and the times asked."""
    T = np.atleast_1d(np.asarray(T, dtype=float))
    if np.isscalar(vol):
        return float(vol) * T
    grid = np.unique(np.concatenate([np.asarray(vol.maturities, dtype=float), T[T > 0]]))
    total = np.maximum.accumulate(np.array([float(vol.iv(0.0, t)) ** 2 * t for t in grid]))
    steps = np.sqrt(np.diff(total, prepend=0.0) * np.diff(grid, prepend=0.0))
    return np.interp(T, np.concatenate([[0.0], grid]), np.concatenate([[0.0], np.cumsum(steps)]))


def quanto_market(market: Market, discount, rho: float, vol_fx: float, vol) -> Market:
    """The same name for a payer in another currency: `discount` is T -> D(T) of that currency, `vol` the name's flat vol or surface."""
    if not -1.0 <= rho <= 1.0:
        raise ValueError(f"rho must be in [-1, 1], got {rho}")
    if vol_fx < 0.0:
        raise ValueError(f"the FX volatility must not be negative, got {vol_fx}")
    t = np.asarray(market.to_dict()["pillars"], dtype=float)
    forwards = np.asarray(market.F(t)) * np.exp(-rho * vol_fx * integrated_vol(t, vol))
    return Market(market.spot, t, np.asarray(discount(t), dtype=float), forwards)


def payment_warnings(sheet, ctx) -> list[str]:
    """What the market in use does about the sheet's payment currency. A market built for another currency, or an explicit currency it
    cannot honour, is an error. A market without FX (flat inputs, a calibration file) still prices a mixed basket on the sheet's
    defaults, and the quote says the performances are not adjusted."""
    pay = sheet.payment_currency
    held = getattr(ctx, "payment_currency", None)
    if held is not None:
        if held != pay:
            raise ValueError(f"the term sheet pays in {pay}, this market was built to pay in {held}")
        return []
    others = [n for n in sheet.underlyings if n in _BY_ID and _BY_ID[n].currency != pay]
    if not others:
        return []
    what = ", ".join(f"{n} ({_BY_ID[n].currency})" for n in others)
    if sheet.get("general.payment_currency") != "native":
        raise ValueError(
            f"paying {what} in {pay} needs a quanto adjustment and this market has no FX: use the lake's market "
            "(pit.market_at, pit.basket_at)"
        )
    return [f"{what} paid in {pay}: performances are not adjusted for FX, this market has no FX inputs"]
