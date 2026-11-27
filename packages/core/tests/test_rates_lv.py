"""Local vol under stochastic rates: the recalibrated local variance keeps the surface's vanillas, where Dupire alone
misses them by the rates' share of the variance; the correction is the closed Gaussian one under Black-Scholes."""

import numpy as np
import pytest

from optlab.core import Market
from optlab.core.blackscholes import implied_vol
from optlab.core.models import LocalVolModel
from optlab.core.rates import HullWhite1F, HybridModel, simulate_hybrid
from optlab.core.rates_lv import calibrate_local_var_under_rates, gaussian_correction
from optlab.core.ssvi import SSVI

pytestmark = pytest.mark.slow  # calibrations of 100,000 particles to 5 years: the full suite runs them

PILLARS = np.array([0.25, 0.5, 1.0, 2.0, 3.0, 5.0])
SURFACE = SSVI.power_law(-0.7, 1.0, 0.45, PILLARS, (0.155 + 0.011 * PILLARS) ** 2 * PILLARS)
T = np.array([1.0, 2.0, 5.0, 10.0])
CURVE = Market(100.0, T, np.exp(-np.array([0.030, 0.034, 0.038, 0.041]) * T), 100.0 * np.exp(0.02 * T))
HW = HullWhite1F(0.03, 0.008)
TIMES = np.array([1.0, 3.0, 5.0])
MONEYNESS = (0.8, 1.0, 1.2)


def implied(model, seed=7, n=200_000):
    """Implied vols (and their SEs) of calls on the hybrid's paths, at TIMES x MONEYNESS of the forward."""
    paths, rates = simulate_hybrid(model, CURVE, TIMES, n, rng=seed)
    out, ses = {}, {}
    for j, t in enumerate(TIMES):
        F, D = float(CURVE.F(t)), float(CURVE.D(t))
        for m in MONEYNESS:
            K = m * F
            sample = rates.D[:, j] * np.maximum(paths[:, j] - K, 0.0)
            pairs = 0.5 * (sample[: n // 2] + sample[n // 2 :])
            price, se = pairs.mean(), pairs.std(ddof=1) / np.sqrt(len(pairs))
            iv = float(implied_vol(price, F, K, t, D, "call"))
            vega = float(implied_vol(price + se, F, K, t, D, "call")) - iv
            out[(t, m)], ses[(t, m)] = iv, vega
    return out, ses


@pytest.fixture(scope="module")
def deterministic():
    """The discretisation's own error: Dupire under rates of zero volatility, on the same grid and seed."""
    return implied(HybridModel(LocalVolModel(SURFACE), HullWhite1F(0.03, 0.0), 0.0))


@pytest.mark.parametrize("rho", [0.4, -0.4])
def test_the_recalibrated_local_vol_keeps_the_smile_and_dupire_alone_does_not(rho, deterministic):
    det, _ = deterministic
    table = calibrate_local_var_under_rates(SURFACE, CURVE, HW, rho, 5.0, n_particles=100_000)
    got, se = implied(HybridModel(LocalVolModel(table), HW, rho))
    naive, _ = implied(HybridModel(LocalVolModel(SURFACE), HW, rho))
    for key, iv in got.items():
        surface_iv = float(SURFACE.iv(np.log(key[1]), key[0]))
        allowed = 4 * se[key] + abs(det[key] - surface_iv) + 0.0005
        assert abs(iv - surface_iv) <= allowed, (key, iv, surface_iv, se[key])
    miss = naive[(5.0, 1.0)] - float(SURFACE.iv(0.0, 5.0))
    assert np.sign(miss) == np.sign(rho) and abs(miss) > 0.004  # 40 bp of vol and more at 5 years
    assert table.floored == 0.0


def test_the_correction_is_the_gaussian_one_under_black_scholes():
    """A flat surface: the particles' correction at the money is c_G(T) with the recalibrated equity vol."""
    flat = SSVI(PILLARS, 0.2**2 * PILLARS, np.zeros_like(PILLARS), np.zeros_like(PILLARS))
    table = calibrate_local_var_under_rates(flat, CURVE, HW, 0.4, 3.0, n_particles=100_000)
    for t in (1.0, 2.0, 3.0):
        i = int(np.searchsorted(table.times, t)) - 1
        c = 0.04 - float(np.interp(0.0, table.k_grids[i], table.values[i]))
        sigma_s = lambda u, table=table: np.sqrt([table.local_var(0.0, max(v, 1e-9)) for v in np.atleast_1d(u)])  # noqa: E731 (the recalibrated vol along time)
        ref = gaussian_correction(HW, 0.4, sigma_s, t)
        assert abs(c - ref) < 0.15 * abs(ref) + 2e-5, (t, c, ref)
