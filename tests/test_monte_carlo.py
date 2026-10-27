import numpy as np
import pytest

from optlab import (BlackScholesModel, HestonModel, HestonParams, Market, american_lsm, asian_mc, barrier_mc,
                    barrier_price, barrier_shift_discrete, binomial_price, bs_price, european_mc,
                    geometric_asian_price, heston_price, simulate)

S, r, q, vol = 100.0, 0.03, 0.01, 0.2
MARKET = Market.flat(S, r, q)
BS = BlackScholesModel(vol)
HESTON = HestonParams(0.04, 1.5, 0.04, 0.5, -0.7)


def within(res, exact, n_se=4.0, floor=0.0):
    return abs(res.price - exact) <= n_se * res.stderr + floor


def test_forward_is_matched_by_construction():
    paths = simulate(HestonModel(HESTON), MARKET, [0.5, 1.0], 200_000, rng=1)
    assert paths[:, -1].mean() == pytest.approx(MARKET.F(1.0), rel=2e-3)


@pytest.mark.parametrize("cp", ["call", "put"])
@pytest.mark.parametrize("K", [80.0, 100.0, 120.0])
def test_european_mc_matches_black_scholes(cp, K):
    res = european_mc(BS, MARKET, K, 1.0, cp, n_paths=200_000, rng=7)
    assert within(res, bs_price(S, K, 1.0, r, q, vol, cp))


def test_mc_error_shrinks_like_one_over_sqrt_n():
    se = [european_mc(BS, MARKET, 100, 1.0, "call", n_paths=n, rng=3).stderr for n in (10_000, 160_000)]
    assert se[0] / se[1] == pytest.approx(4.0, rel=0.15)
    errors = []
    for n in (4_000, 400_000):
        prices = [european_mc(BS, MARKET, 100, 1.0, "call", n_paths=n, rng=seed).price for seed in range(8)]
        errors.append(np.sqrt(np.mean((np.array(prices) - bs_price(S, 100, 1.0, r, q, vol)) ** 2)))
    assert errors[1] < errors[0] / 4


def test_mc_put_call_parity_on_same_paths():
    c = european_mc(BS, MARKET, 105, 1.0, "call", n_paths=100_000, rng=11)
    p = european_mc(BS, MARKET, 105, 1.0, "put", n_paths=100_000, rng=11)
    target = MARKET.D(1.0) * (MARKET.F(1.0) - 105)
    assert abs((c.price - p.price) - target) < 4 * np.hypot(c.stderr, p.stderr)


@pytest.mark.parametrize("K", [80.0, 100.0, 120.0])
def test_heston_mc_matches_fourier(K):
    exact = float(heston_price(MARKET.F(1.0), K, 1.0, HESTON, MARKET.D(1.0), "call")[0])
    res = european_mc(HestonModel(HESTON), MARKET, K, 1.0, "call", n_paths=200_000, rng=5)
    # full-truncation Euler has a small discretisation bias on top of the statistical error
    assert within(res, exact, floor=0.01)


def test_geometric_asian_mc_matches_closed_form():
    fixings = np.linspace(1 / 12, 1.0, 12)
    exact = float(geometric_asian_price(S, 100, r, q, vol, fixings))
    res = asian_mc(BS, MARKET, 100, 1.0, 12, "call", "geometric", n_paths=200_000, rng=2)
    assert within(res, exact)


def test_arithmetic_asian_control_variate():
    plain = asian_mc(BS, MARKET, 100, 1.0, 12, "call", n_paths=100_000, rng=4, control_variate=False)
    cv = asian_mc(BS, MARKET, 100, 1.0, 12, "call", n_paths=100_000, rng=4)
    assert cv.stderr < plain.stderr / 10
    assert abs(cv.price - plain.price) < 4 * plain.stderr
    # arithmetic >= geometric average, so the call is worth more
    assert cv.price > geometric_asian_price(S, 100, r, q, vol, np.linspace(1 / 12, 1.0, 12))


@pytest.mark.parametrize("kind,H,cp", [("down-out", 85.0, "call"), ("up-out", 120.0, "call"),
                                       ("down-in", 90.0, "put"), ("up-in", 115.0, "call")])
def test_daily_barrier_matches_broadie_glasserman_kou(kind, H, cp):
    res = barrier_mc(BS, MARKET, 100, H, 1.0, kind, cp, n_monitor=252, n_paths=200_000, rng=9)
    shifted = barrier_shift_discrete(H, S, vol, 1 / 252)
    exact = barrier_price(S, 100, shifted, 1.0, r, q, vol, kind, cp)
    assert within(res, exact, floor=0.01)


def test_barrier_mc_in_plus_out_is_vanilla():
    # same seed, same paths: every path is either knocked in or still alive
    out = barrier_mc(BS, MARKET, 100, 90, 1.0, "down-out", "call", n_paths=100_000, rng=1)
    knock_in = barrier_mc(BS, MARKET, 100, 90, 1.0, "down-in", "call", n_paths=100_000, rng=1)
    vanilla = european_mc(BS, MARKET, 100, 1.0, "call", n_paths=100_000, rng=1)
    assert abs(out.price + knock_in.price - bs_price(S, 100, 1.0, r, q, vol)) < 4 * vanilla.stderr


@pytest.mark.parametrize("K", [90.0, 100.0, 110.0])
def test_lsm_american_put_matches_tree(K):
    mkt = Market.flat(S, 0.05, 0.0)
    tree = binomial_price(S, K, 1.0, 0.05, 0.0, vol, "put", american=True)
    lsm = american_lsm(BS, mkt, K, 1.0, "put", n_exercise=50, n_paths=100_000, rng=3)
    # 50 exercise dates instead of continuous exercise: LSM sits slightly below the tree
    assert tree - 0.06 < lsm.price < tree + 4 * lsm.stderr


def test_american_at_least_european_under_heston():
    mkt = Market.flat(S, 0.05, 0.0)
    model = HestonModel(HESTON)
    am = american_lsm(model, mkt, 100, 1.0, "put", n_exercise=50, n_paths=100_000, rng=8)
    eu = float(heston_price(mkt.F(1.0), 100, 1.0, HESTON, mkt.D(1.0), "put")[0])
    assert am.price > eu


def test_lsm_american_call_without_dividend_is_european():
    mkt = Market.flat(S, 0.05, 0.0)
    lsm = american_lsm(BS, mkt, 100, 1.0, "call", n_exercise=50, n_paths=100_000, rng=6)
    assert within(lsm, bs_price(S, 100, 1.0, 0.05, 0.0, vol, "call"), floor=0.02)
