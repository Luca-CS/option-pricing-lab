"""The lambda shift (Langnau's family anchored at the measured matrix): zero gives the base's paths bit for bit, each
name keeps its own law whatever the shape, the correlation rises when the basket falls, and a worst-of put is worth
less for it (the names fall together: the worst is less bad), so a note that sells it is worth more to its investor."""

import dataclasses

import numpy as np
import pytest

from optlab.core import BlackScholesModel, Market
from optlab.structured.localcorr import LambdaShift
from optlab.structured.multiasset import Basket, simulate_basket

NAMES = ("A", "B", "C")
VOLS = {"A": 0.2, "B": 0.25, "C": 0.3}
CORR = np.array([[1.0, 0.7, 0.5], [0.7, 1.0, 0.6], [0.5, 0.6, 1.0]])
TIMES = np.array([0.5, 1.0])
N = 100_000


def basket(mixer=None):
    return Basket(
        NAMES,
        {n: BlackScholesModel(v) for n, v in VOLS.items()},
        {n: Market.flat(100.0, 0.03, 0.01) for n in NAMES},
        CORR,
        mixer=mixer,
    )


def test_a_shift_of_zero_is_the_base_bit_for_bit():
    base = simulate_basket(basket(), TIMES, 4_000, 7)
    zero = simulate_basket(basket(LambdaShift(0.22, 0.0, 0.6, 0.0)), TIMES, 4_000, 7)
    for n in NAMES:
        np.testing.assert_array_equal(base[n], zero[n])


@pytest.mark.parametrize("shape", [(0.6, 0.6, 0.0, 1.0), (0.6, 0.6, 0.2, 1.0)])
def test_each_name_keeps_its_law_and_the_correlation_rises_in_a_fall(shape):
    mixer = LambdaShift(0.22, *shape)
    base = simulate_basket(basket(), TIMES, N, 8)
    shifted = simulate_basket(basket(mixer), TIMES, N, 8)
    for n in NAMES:
        F = 100.0 * np.exp(0.02 * 1.0)
        for K in (80.0, 100.0, 120.0):
            a, b = np.maximum(base[n][:, -1] - K, 0.0), np.maximum(shifted[n][:, -1] - K, 0.0)
            d = 0.5 * ((b - a)[: N // 2] + (b - a)[N // 2 :])
            assert abs(d.mean()) < 4 * d.std(ddof=1) / np.sqrt(len(d)) + 1e-12, (n, K)
        assert abs(shifted[n][:, -1].mean() / F - 1) < 4 * shifted[n][:, -1].std() / F / np.sqrt(N)
    perf = {k: {n: p[n][:, -1] / 100.0 for n in NAMES} for k, p in (("base", base), ("shift", shifted))}

    def corr_when_falling(p):
        level = np.mean([p[n] for n in NAMES], axis=0)
        down = level < np.quantile(level, 0.1)
        r = np.log(np.array([p[n][down] for n in NAMES]))
        return np.corrcoef(r)[np.triu_indices(3, 1)].mean()

    assert corr_when_falling(perf["shift"]) > corr_when_falling(perf["base"]) + 0.05


def test_a_worst_of_put_is_worth_less_with_the_shift_and_the_bump_lowers_it():
    def worst_put(mixer):
        p = simulate_basket(basket(mixer), TIMES, N, 9)
        worst = np.min([p[n][:, -1] / 100.0 for n in NAMES], axis=0)
        return np.maximum(0.9 - worst, 0.0)

    base, shifted = worst_put(None), worst_put(LambdaShift(0.22, 0.6, 0.6))
    bumped = worst_put(LambdaShift(0.22, 0.6, 0.6).bumped(0.1))
    d = 0.5 * ((shifted - base)[: N // 2] + (shifted - base)[N // 2 :])
    assert d.mean() < -4 * d.std(ddof=1) / np.sqrt(len(d))
    assert bumped.mean() < shifted.mean()
    assert dataclasses.replace(LambdaShift(0.22, 0.9), lam_dn=0.9).bumped(0.2).lam_dn == pytest.approx(0.95)
