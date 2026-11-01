"""Corrections from the mathematical review: Asian control variate on a curve, Lewis quadrature at short
maturities, SLV leverage and its regression, delta conventions."""

import numpy as np
import pytest
from scipy.integrate import quad

from optlab import BlackScholesModel, HestonParams, Market, asian_mc, geometric_asian_price, heston_price
from optlab.blackscholes import geometric_asian_price_curve
from optlab.heston import char_function

CURVED = Market(100.0, np.array([0.25, 0.5, 1.0]), np.exp(-np.array([0.01, 0.02, 0.05])),
                100 * np.exp(np.array([0.02, 0.025, 0.04])))
SPX_HESTON = HestonParams(0.022846642482989885, 3.0892635624407627, 0.05372458999932075, 1.307745170926575,
                          -0.6879606734541107)


# --- B2: the geometric Asian control variate follows the forward curve ---

def test_geometric_asian_on_a_flat_curve_is_the_old_formula():
    flat = Market.flat(100.0, 0.04, 0.01)
    t = np.linspace(1 / 12, 1.0, 12)
    for K in (80.0, 100.0, 120.0):
        for cp in ("call", "put"):
            assert float(geometric_asian_price_curve(flat, K, 0.2, t, cp)) == pytest.approx(
                float(geometric_asian_price(100.0, K, 0.04, 0.01, 0.2, t, cp)), abs=1e-12)


def test_asian_control_variate_is_unbiased_on_a_curved_market():
    model = BlackScholesModel(0.2)
    cv = asian_mc(model, CURVED, 100.0, 1.0, 12, "call", n_paths=200_000, rng=4)
    raw = asian_mc(model, CURVED, 100.0, 1.0, 12, "call", n_paths=2_000_000, rng=5, control_variate=False)
    assert abs(cv.price - raw.price) <= 4 * np.hypot(cv.stderr, raw.stderr)
    geo = asian_mc(model, CURVED, 100.0, 1.0, 12, "call", average="geometric", n_paths=400_000, rng=6)
    exact = float(geometric_asian_price_curve(CURVED, 100.0, 0.2, np.linspace(1 / 12, 1.0, 12)))
    assert abs(geo.price - exact) <= 4 * geo.stderr


# --- B3: Lewis quadrature accurate at short maturities ---

def lewis_reference(F, K, T, p):
    lk = np.log(F / K)

    def f(u):
        return float(np.real(np.exp(1j * u * lk) * char_function(np.array([u - 0.5j]), T, p)[0]) / (u * u + 0.25))

    edges = list(np.linspace(0.0, 50.0, 41)) + list(np.geomspace(50.0, 1e5, 121)[1:])
    total = sum(quad(f, a, b, epsabs=1e-15, epsrel=1e-13, limit=400)[0] for a, b in zip(edges[:-1], edges[1:]))
    return F - np.sqrt(F * K) / np.pi * total


@pytest.mark.parametrize("T", [0.02, 0.0415, 0.25, 2.0])
def test_heston_price_matches_adaptive_quadrature(T):
    # the 8 uniform panels missed the peak of the integrand near u = 0: 1.1e-2 of error at T = 0.02 on this grid;
    # what is left (2.4e-8 at T = 0.25) is the truncation at u_max
    for K in (70.0, 100.0, 130.0):
        assert float(heston_price(100.0, K, T, SPX_HESTON)[0]) == pytest.approx(
            lewis_reference(100.0, K, T, SPX_HESTON), abs=5e-8)


def test_short_dated_out_of_the_money_calls_are_not_negative():
    calls = heston_price(100.0, np.array([105.0, 110.0, 115.0, 130.0]), 0.02, SPX_HESTON)
    assert np.all(calls > -1e-10)  # was -9e-3 with uniform panels


# --- B4: local-linear regression of the leverage (opt-in) ---

def test_local_linear_leverage_removes_the_atm_bias_on_spx():
    # SPX, 1e6 paths: Nadaraya-Watson leaves +0.054 / +0.048 vol point at the money at 1 and 2 years, the local
    # linear regression of max(v, 0) +0.008 / +0.009 (standard error about 0.015)
    from optlab.structured import PricingContext
    from optlab.validation import mc_smile
    ctx = PricingContext.from_calibration(slv_regression="local_linear")
    for T in (1.0, 2.0):
        F = float(ctx.market.F(T))
        iv, _ = mc_smile(ctx.slv, ctx.market, T, [F], n_paths=1_000_000, rng=777)
        assert abs(iv[0] - float(ctx.surface.iv(0.0, T))) * 100 < 0.03


def test_local_linear_leverage_stays_within_tolerance_on_the_synthetic_surface():
    # on this surface both regressions are within the Monte Carlo error (a few bp per strike): no ranking claimed
    from test_calibration import MARKET, TRUE_SSVI

    from optlab import calibrate_leverage
    from optlab.validation import mc_smile
    slv = calibrate_leverage(HestonParams(0.04, 1.5, 0.045, 0.6, -0.7), TRUE_SSVI, MARKET, 1.0, mix=0.7,
                             n_particles=200_000, rng=2, regression="local_linear", positive_part=True)
    T = 1.0
    F = float(MARKET.F(T))
    K = F * np.exp(np.linspace(-0.3, 0.2, 6) * np.sqrt(T))
    iv, _ = mc_smile(slv, MARKET, T, K, n_paths=400_000, rng=3)
    assert np.max(np.abs(iv - TRUE_SSVI.iv(np.log(K / F), T))) < 0.0015


def test_local_linear_falls_back_where_bins_are_thin():
    from optlab.slv import _conditional_mean
    rng = np.random.default_rng(0)
    x = rng.standard_normal(20_000)
    v = 0.04 + 0.01 * x + 0.002 * rng.standard_normal(20_000)
    xe = np.linspace(-6, 6, 41)
    ll = _conditional_mean(x, v, xe, 0.2, regression="local_linear")
    assert np.all(np.isfinite(ll)) and np.all(ll >= 1e-8)
    inner = np.abs(xe) < 2.5
    assert np.max(np.abs(ll[inner] - (0.04 + 0.01 * xe[inner]))) < 1e-3  # linear truth recovered
    with pytest.raises(ValueError, match="unknown regression"):
        _conditional_mean(x, v, xe, 0.2, regression="spline")
