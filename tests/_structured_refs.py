"""Independent references for the structured pricer: Black-Scholes closed forms and a flat SVI surface."""

import numpy as np
from scipy.stats import norm

from optlab import BlackScholesModel, Market, SVISurface
from optlab.blackscholes import black_price
from optlab.structured.context import PricingContext

S0, R, Q, VOL = 100.0, 0.04, 0.01, 0.2
FLAT = Market.flat(S0, R, Q)


def flat_surface(vol=VOL, maturities=(0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0)):
    """SVI slices with b = 0: total variance vol^2 T at every strike."""
    return SVISurface(maturities, [[vol**2 * T, 0.0, 0.0, 0.0, 0.1] for T in maturities])


def flat_context(valuation="2026-10-27", vol=VOL) -> PricingContext:
    """Black-Scholes world: flat market, flat surface; Heston and SLV taken from the SPX calibration (unused)."""
    import datetime as dt
    spx = PricingContext.from_calibration()
    ctx = PricingContext(FLAT, flat_surface(vol), spx.heston, spx.slv, dt.date.fromisoformat(valuation))
    ctx.model = lambda name, T=1.0: BlackScholesModel(vol) if name in ("bs", "lv") else spx.model(name, T)
    return ctx


def forward(T):
    return S0 * np.exp((R - Q) * T)


def put_fwd(K, T, vol=VOL):
    """Undiscounted E[(K - S_T)+]."""
    return float(black_price(forward(T), K, T, vol, 1.0, "put"))


def call_fwd(K, T, vol=VOL):
    return float(black_price(forward(T), K, T, vol, 1.0, "call"))


def prob_below(K, T, vol=VOL):
    """P(S_T < K), the undiscounted cash-or-nothing digital put."""
    sd = vol * np.sqrt(T)
    d2 = np.log(forward(T) / K) / sd - 0.5 * sd
    return float(norm.cdf(-d2))


def rc_european(N, cpn, pay_times, T, T_pay, K, B, market=FLAT):
    """Reverse convertible with a European PDI: fixed coupons plus N D(T_pay) [1 - E[(K - perf)+ 1{perf < B}]].
    For B <= K: (K S0 - S)+ 1{S < B S0} = (B S0 - S)+ + (K - B) S0 1{S < B S0}."""
    coupons = N * cpn * sum(float(market.D(t)) for t in pay_times)
    loss = (put_fwd(B * S0, T) + (K - B) * S0 * prob_below(B * S0, T)) / S0
    return coupons + N * float(market.D(T_pay)) * (1 - loss)
