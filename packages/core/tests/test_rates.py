"""Hull-White beside the equity: bonds, deflators and options against closed forms, within 4 standard errors."""

import numpy as np
import pytest

from optlab.core import BlackScholesModel, Market
from optlab.core.blackscholes import black_price
from optlab.core.models import simulate
from optlab.core.rates import B, HullWhite1F, HybridModel, simulate_hybrid

T = np.array([1.0, 2.0, 5.0, 10.0])
CURVE = Market(100.0, T, np.exp(-np.array([0.030, 0.034, 0.038, 0.041]) * T), 100.0 * np.exp(0.02 * T))
HW = HullWhite1F(0.03, 0.009)
N = 200_000


def mean_se(x):
    x = np.asarray(x, dtype=float)
    return float(x.mean()), float(x.std(ddof=1) / np.sqrt(len(x)))


def pairs(x):
    """Antithetic pair averages: the SE is read on them."""
    h = len(x) // 2
    return 0.5 * (x[:h] + x[h:])


def check(sample, exact, what, k=4.0):
    m, se = mean_se(pairs(sample))
    assert abs(m - exact) <= k * se + 1e-12, f"{what}: {m:.8f} against {exact:.8f}, {abs(m - exact) / se:.1f} SE"


@pytest.fixture(scope="module")
def hybrid():
    times = np.array([0.5, 1.0, 2.0, 3.0, 5.0, 7.0])
    model = HybridModel(BlackScholesModel(0.2), HW, rho=0.3)
    paths, rates = simulate_hybrid(model, CURVE, times, N, rng=11)
    return model, times, paths, rates


def test_the_deflator_reprices_every_zero_coupon_bond(hybrid):
    _, times, _, rates = hybrid
    for j, t in enumerate(times):
        check(rates.D[:, j], float(CURVE.D(t)), f"E[D_{t:g}]")


def test_the_bond_formula_is_the_expected_deflator(hybrid):
    _, _, _, rates = hybrid
    for T_ in (2.0, 5.0, 9.0):
        check(rates.deflator(1.0) * rates.bond(1.0, T_), float(CURVE.D(T_)), f"E[D_1 P(1, {T_:g})]")


def test_the_prepaid_forward_holds_path_by_path_and_is_a_martingale(hybrid):
    _, times, paths, rates = hybrid
    for j, t in enumerate(times):
        ratio = paths[:, j] * rates.D[:, j] / (CURVE.F(t) * CURVE.D(t))
        check(ratio, 1.0, f"E[D S / (P F)] at {t:g}")


def test_a_zero_bond_option_against_jamshidian():
    times = np.array([2.0])
    model = HybridModel(BlackScholesModel(0.2), HW, rho=0.0)
    _, rates = simulate_hybrid(model, CURVE, times, N, rng=12)
    S, K = 5.0, float(CURVE.D(5.0) / CURVE.D(2.0))
    for kind in ("call", "put"):
        bond = rates.bond(2.0, S)
        payoff = np.maximum(bond - K, 0.0) if kind == "call" else np.maximum(K - bond, 0.0)
        check(rates.deflator(2.0) * payoff, HW.zero_bond_option(CURVE, 2.0, S, K, kind), f"zero-bond {kind}")


@pytest.mark.parametrize("rho", [-0.4, 0.0, 0.4])
def test_a_european_under_black_scholes_and_hull_white_against_its_closed_form(rho):
    """Black on the forward with the total variance sigma_S^2 T + 2 rho sigma_S sigma_r (T - B_a(T))/a + V(T)."""
    vol, times = 0.2, np.array([1.0, 3.0, 5.0])
    model = HybridModel(BlackScholesModel(vol), HW, rho=rho)
    paths, rates = simulate_hybrid(model, CURVE, times, N, rng=13)
    for j, t in enumerate(times):
        total = vol**2 * t + 2 * rho * vol * HW.sigma * (t - float(B(HW.a, t))) / HW.a + float(HW.var_integral(t))
        for K in (70.0, 100.0, 130.0):
            ref = float(black_price(float(CURVE.F(t)), K, t, np.sqrt(total / t), float(CURVE.D(t)), "call"))
            check(rates.D[:, j] * np.maximum(paths[:, j] - K, 0.0), ref, f"call {K:g} at {t:g}, rho {rho}")


def test_the_spot_normals_are_the_local_vol_models():
    """A Black-Scholes equity's log-forward path is the one `simulate` draws, whatever the rates (z1 is the same)."""
    times = np.array([0.5, 1.0, 2.0])
    base = simulate(BlackScholesModel(0.2), CURVE, times, 2_000, np.random.default_rng(5))
    for rho, sigma in ((0.0, 0.009), (0.4, 0.015)):
        hw = HullWhite1F(0.03, sigma)
        paths, rates = simulate_hybrid(HybridModel(BlackScholesModel(0.2), hw, rho), CURVE, times, 2_000, rng=5)
        X = np.log(paths / CURVE.F(times)) - rates.Y - 0.5 * hw.var_integral(times)
        np.testing.assert_allclose(X, np.log(base / CURVE.F(times)), atol=1e-12)
