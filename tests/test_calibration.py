import numpy as np
import pytest

from optlab import (SSVI, HestonParams, LocalVolModel, Market, SVISurface, arbitrage_report, calibrate_heston,
                    calibrate_leverage, fit_ssvi, fit_svi_surface, heston_iv, svi_arbitrage_report)
from optlab.svi import ssvi_slice_to_svi
from optlab.validation import mc_smile

MATURITIES = np.array([0.1, 0.25, 0.5, 1.0, 2.0])
TRUE_SSVI = SSVI.power_law(-0.7, 1.1, 0.4, MATURITIES, 0.04 * MATURITIES * (1 + 0.2 * MATURITIES))
MARKET = Market.flat(100.0, 0.02, 0.01)


def synthetic_ssvi_quotes(noise=0.002, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for T in MATURITIES:
        k = np.linspace(-2.5, 1.5, 25) * 0.2 * np.sqrt(T)
        out.append({"T": T, "k": k, "iv": TRUE_SSVI.iv(k, T) + rng.normal(0, noise, k.size)})
    return out


def test_true_surface_is_arbitrage_free():
    rep = arbitrage_report(TRUE_SSVI)
    assert rep["butterfly_free"] and rep["calendar_free"]


def test_ssvi_fit_recovers_parameters():
    fit, _ = fit_ssvi(synthetic_ssvi_quotes())
    assert np.allclose(fit.rhos, TRUE_SSVI.rhos, atol=0.06)
    assert np.allclose(fit.thetas, TRUE_SSVI.thetas, rtol=0.03)
    assert np.allclose(fit.psis, TRUE_SSVI.psis, rtol=0.08)
    rep = arbitrage_report(fit)
    assert rep["butterfly_free"] and rep["calendar_free"]
    # fitted vols within the noise level of the quotes
    errs = np.concatenate([fit.iv(s["k"], s["T"]) - TRUE_SSVI.iv(s["k"], s["T"]) for s in synthetic_ssvi_quotes()])
    assert np.sqrt(np.mean(errs**2)) < 0.002


def test_ssvi_slice_is_a_raw_svi_slice():
    k = np.linspace(-0.5, 0.3, 41)
    for T in (0.25, 1.0):
        th, psi, rho = (float(np.asarray(v)) for v in TRUE_SSVI.params(T))
        surf = SVISurface([T], [ssvi_slice_to_svi(th, psi, rho)])
        assert np.allclose(surf.w(k, T), TRUE_SSVI.w(k, T), atol=1e-12)


def test_svi_surface_fit_is_arbitrage_free_and_tight():
    quotes = synthetic_ssvi_quotes()
    surf, _ = fit_svi_surface(quotes)
    rep = svi_arbitrage_report(surf)
    assert rep["butterfly_free"] and rep["calendar_free"]
    assert np.all(surf.params[:, 2] <= 1e-9)  # rho capped at 0
    errs = np.concatenate([surf.iv(s["k"], s["T"]) - TRUE_SSVI.iv(s["k"], s["T"]) for s in quotes])
    assert np.sqrt(np.mean(errs**2)) < 0.002
    # interpolation between pillars stays close to the true surface
    k = np.linspace(-0.3, 0.15, 11)
    assert np.max(np.abs(surf.iv(k, 0.7) - TRUE_SSVI.iv(k, 0.7))) < 0.004


def test_svi_dupire_matches_finite_differences():
    surf, _ = fit_svi_surface(synthetic_ssvi_quotes())
    k, T, h = np.array([-0.2, 0.0, 0.1]), 0.6, 1e-5
    w, wk, wkk, wT = surf.derivatives(k, T)
    assert np.allclose(wk, (surf.w(k + h, T) - surf.w(k - h, T)) / (2 * h), rtol=1e-5)
    assert np.allclose(wkk, (surf.w(k + h, T) - 2 * w + surf.w(k - h, T)) / h**2, rtol=1e-3)
    assert np.allclose(wT, (surf.w(k, T + h) - surf.w(k, T - h)) / (2 * h), rtol=1e-4)


def test_heston_calibration_recovers_parameters():
    true = HestonParams(0.03, 2.0, 0.05, 0.6, -0.65)
    quotes = []
    for T in (0.1, 0.25, 0.5, 1.0, 2.0):
        F, D = MARKET.F(T), MARKET.D(T)
        K = F * np.exp(np.linspace(-0.4, 0.25, 15) * np.sqrt(T))
        quotes.append({"T": T, "F": F, "D": D, "K": K, "iv": heston_iv(F, K, T, true, D)})
    fit, sol = calibrate_heston(quotes)
    assert np.allclose(fit.as_array(), true.as_array(), rtol=0.02, atol=1e-3)
    assert np.max(np.abs(sol.fun)) < 1e-5


def test_dupire_local_vol_reproduces_the_surface():
    lv = LocalVolModel(TRUE_SSVI)
    for T in (0.25, 1.0):
        F = MARKET.F(T)
        K = F * np.exp(np.linspace(-0.3, 0.2, 6) * np.sqrt(T))
        iv, se = mc_smile(lv, MARKET, T, K, n_paths=200_000, rng=1)
        err = iv - TRUE_SSVI.iv(np.log(K / F), T)
        assert np.max(np.abs(err)) < 0.0015  # 15 bp, about 3 standard errors


@pytest.fixture(scope="module")
def slv_model():
    heston = HestonParams(0.04, 1.5, 0.045, 0.6, -0.7)
    return calibrate_leverage(heston, TRUE_SSVI, MARKET, 1.0, mix=0.7, n_particles=200_000, rng=2)


@pytest.mark.parametrize("T", [0.25, 1.0])
def test_slv_reproduces_vanillas(slv_model, T):
    F = MARKET.F(T)
    K = F * np.exp(np.linspace(-0.3, 0.2, 6) * np.sqrt(T))
    iv, se = mc_smile(slv_model, MARKET, T, K, n_paths=400_000, rng=3)
    err = iv - TRUE_SSVI.iv(np.log(K / F), T)
    # Monte Carlo standard error ~3-4 bp per strike, mostly common to all strikes; with 200,000
    # particles the leverage leaves a bias of about +5 bp at three months. Tolerance: ~3 standard errors.
    assert np.mean(np.abs(err)) < 0.0010
    assert np.max(np.abs(err)) < 0.0015


def test_slv_leverage_is_not_trivial(slv_model):
    # with stochastic variance the leverage must bend away from 1 to recover the local vol
    L = slv_model.leverage.values[len(slv_model.leverage.times) // 2]
    assert L.max() - L.min() > 0.1
