import numpy as np
import pytest

from optlab import (HestonParams, Market, barrier_price, binomial_price, black_price, bs_greeks, bs_price,
                    geometric_asian_price, heston_iv, heston_price, implied_vol)

S, r, q, vol = 100.0, 0.03, 0.01, 0.2


def test_mini_codes_reference_values():
    # the values quoted in Luca-CS/Mini_codes for S=100, K=110, r=3 %, vol=25 %, T=1 month
    assert bs_price(100, 110, 1 / 12, 0.03, 0.0, 0.25, "call") == pytest.approx(0.3544, abs=1e-4)
    assert bs_price(100, 110, 1 / 12, 0.03, 0.0, 0.25, "put") == pytest.approx(10.0797, abs=1e-4)


@pytest.mark.parametrize("K", [70.0, 100.0, 140.0])
@pytest.mark.parametrize("T", [0.05, 1.0, 5.0])
def test_put_call_parity_black_scholes(K, T):
    c = bs_price(S, K, T, r, q, vol, "call")
    p = bs_price(S, K, T, r, q, vol, "put")
    assert c - p == pytest.approx(S * np.exp(-q * T) - K * np.exp(-r * T), abs=1e-10)


@pytest.mark.parametrize("T", [0.1, 1.0, 3.0])
def test_put_call_parity_heston(T):
    p = HestonParams(0.04, 1.5, 0.05, 0.7, -0.7)
    F, D = 105.0, 0.97
    K = np.array([60.0, 90.0, 105.0, 130.0, 180.0])
    c = heston_price(F, K, T, p, D, "call")
    put = heston_price(F, K, T, p, D, "put")
    assert np.allclose(c - put, D * (F - K), atol=1e-10)
    assert np.all(put >= -1e-10) and np.all(c >= D * np.maximum(F - K, 0) - 1e-8)


def test_heston_with_zero_volofvol_is_black_scholes():
    # xi -> 0 and v0 = theta: constant variance theta
    p = HestonParams(0.04, 2.0, 0.04, 1e-4, 0.0)
    K = np.array([80.0, 100.0, 125.0])
    assert np.allclose(heston_iv(100.0, K, 1.0, p), 0.2, atol=1e-5)


def test_implied_vol_roundtrip():
    rng = np.random.default_rng(0)
    F = 100.0
    K = rng.uniform(50, 150, 200)
    T = rng.uniform(0.02, 3.0, 200)
    sig = rng.uniform(0.05, 1.2, 200)
    D = np.exp(-0.03 * T)
    for cp in ("call", "put"):
        price = black_price(F, K, T, sig, D, cp)
        intrinsic = D * np.maximum((F - K) if cp == "call" else (K - F), 0.0)
        ok = price - intrinsic > 1e-8 * F  # below that the time value is lost in rounding
        iv = implied_vol(price, F, K, T, D, cp)
        assert np.allclose(iv[ok], sig[ok], atol=1e-6)


def test_greeks_against_finite_differences():
    K, T, h = 105.0, 0.7, 1e-3
    g = bs_greeks(S, K, T, r, q, vol, "call")
    f = lambda s=S, v=vol, t=T: bs_price(s, K, t, r, q, v, "call")  # noqa: E731
    assert g["delta"] == pytest.approx((f(s=S + h) - f(s=S - h)) / (2 * h), rel=1e-6)
    assert g["gamma"] == pytest.approx((f(s=S + h) - 2 * f() + f(s=S - h)) / h**2, rel=1e-4)
    assert g["vega"] == pytest.approx((f(v=vol + h) - f(v=vol - h)) / (2 * h), rel=1e-6)
    assert g["theta"] == pytest.approx(-(f(t=T + h) - f(t=T - h)) / (2 * h), rel=1e-5)


def test_geometric_asian_single_fixing_is_european():
    assert geometric_asian_price(S, 100, r, q, vol, [1.0]) == pytest.approx(bs_price(S, 100, 1.0, r, q, vol))


def test_geometric_asian_below_european():
    fixings = np.linspace(1 / 12, 1.0, 12)
    assert geometric_asian_price(S, 100, r, q, vol, fixings) < bs_price(S, 100, 1.0, r, q, vol)


@pytest.mark.parametrize("kind,H", [("down", 85.0), ("up", 120.0)])
@pytest.mark.parametrize("cp", ["call", "put"])
@pytest.mark.parametrize("K", [90.0, 100.0, 110.0])
def test_barrier_in_out_parity_and_bounds(kind, H, cp, K):
    out = barrier_price(S, K, H, 1.0, r, q, vol, f"{kind}-out", cp)
    knock_in = barrier_price(S, K, H, 1.0, r, q, vol, f"{kind}-in", cp)
    vanilla = bs_price(S, K, 1.0, r, q, vol, cp)
    assert out + knock_in == pytest.approx(vanilla, abs=1e-10)
    assert -1e-10 <= out <= vanilla + 1e-10


def test_barrier_far_away_is_vanilla():
    assert barrier_price(S, 100, 1.0, 1.0, r, q, vol, "down-out", "call") == pytest.approx(
        bs_price(S, 100, 1.0, r, q, vol, "call"), abs=1e-8)


@pytest.mark.parametrize("cp", ["call", "put"])
def test_binomial_european_converges_to_black_scholes(cp):
    exact = bs_price(S, 100, 1.0, r, q, vol, cp)
    errors = [abs(binomial_price(S, 100, 1.0, r, q, vol, cp, american=False, n=n) - exact) for n in (100, 400, 1600)]
    assert errors[-1] < 2e-3
    assert errors[2] < errors[0]


def test_american_put_above_european_and_intrinsic():
    for K in (80.0, 100.0, 120.0):
        am = binomial_price(S, K, 1.0, 0.05, 0.0, vol, "put", american=True)
        eu = binomial_price(S, K, 1.0, 0.05, 0.0, vol, "put", american=False)
        assert am >= eu - 1e-12
        assert am >= max(K - S, 0.0)
    # with a positive rate the early-exercise premium of an ATM put is strictly positive
    assert binomial_price(S, 100, 1.0, 0.05, 0.0, vol, "put") - bs_price(S, 100, 1.0, 0.05, 0.0, vol, "put") > 0.1


def test_american_call_without_dividend_is_european():
    am = binomial_price(S, 100, 1.0, 0.05, 0.0, vol, "call", american=True)
    assert am == pytest.approx(bs_price(S, 100, 1.0, 0.05, 0.0, vol, "call"), abs=2e-3)


def test_market_flat_curves():
    m = Market.flat(100.0, 0.03, 0.01)
    for T in (0.1, 1.0, 7.0):
        assert m.D(T) == pytest.approx(np.exp(-0.03 * T))
        assert m.F(T) == pytest.approx(100 * np.exp(0.02 * T))
    assert m.zero_rate(2.0) == pytest.approx(0.03)
