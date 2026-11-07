"""Local correlation: every name keeps its law, the matrix stays valid, CvC = PvP with the model, the pseudo-quotes refitted, the provision and its bump."""

import numpy as np
import pytest
from optlab.core.blackscholes import black_price
from optlab.core.market import Market
from optlab.core.models import BlackScholesModel
from optlab.structured import PricingContext, preset, quote
from optlab.structured.costs import CostInputs
from optlab.structured.localcorr import (
    W0,
    CorrelationSkew,
    LocalCorrelation,
    StochasticMix,
    basket_sigma,
    calibrate,
    low_matrix,
    spread_payoff,
    targets,
)
from optlab.structured.multiasset import Basket, BasketContext, simulate_basket
from scipy.stats import multivariate_normal, norm

SPX = PricingContext.from_calibration()
RHO = 0.7


def within(got, want, se, n=4.0):
    assert abs(got - want) <= n * se, f"{got} against {want}, {abs(got - want) / se:.1f} SE"


def flat_basket(mixer=None, rho=RHO, vols=(0.2, 0.3)):
    names = ("AAA", "BBB")
    markets = {n: Market.flat(100.0, 0.03, 0.01) for n in names}
    return Basket(
        names,
        {n: BlackScholesModel(v) for n, v in zip(names, vols)},
        markets,
        np.array([[1.0, rho], [rho, 1.0]]),
        "same",
        mixer,
    )


@pytest.fixture(scope="module")
def ctx():
    from optlab.structured.surfaces import BumpedSurface

    market = Market.flat(100.0, 0.03, 0.01)
    return BasketContext(
        ("SPX", "AAA"),
        {"SPX": SPX.market, "AAA": market},
        {"SPX": SPX.surface, "AAA": BumpedSurface(SPX.surface, 0.04)},
        np.array([[1.0, RHO], [RHO, 1.0]]),
        SPX.valuation,
        {"SPX": SPX},
    )


STEEP = LocalCorrelation(0.2, 0.2, 0.5, 0.3)


@pytest.mark.slow
def test_every_name_keeps_its_law_whatever_the_weight():
    times = np.array([0.5, 1.0])
    for mixer in (STEEP, StochasticMix(0.5, 1.0, -0.8)):
        basket = flat_basket(mixer)
        # Black-Scholes with a constant step is only exact on one step: force a grid for the weight to act
        paths = simulate_basket(_with_grid(basket), times, 200_000, 3, dt_max=1 / 26, dt_min=1 / 52)
        for name, vol in (("AAA", 0.2), ("BBB", 0.3)):
            for j, T in enumerate(times):
                s = paths[name][:, j]
                half = len(s) // 2
                F = float(basket.markets[name].F(T))
                for K in (0.85 * F, F, 1.15 * F):
                    pay = np.maximum(s - K, 0.0)
                    pay = 0.5 * (pay[:half] + pay[half:])
                    within(pay.mean(), float(black_price(F, K, T, vol, 1.0, "call")), pay.std(ddof=1) / np.sqrt(half))


def _with_grid(basket):
    """The same law on a time grid: a local vol model on a flat surface steps where Black-Scholes would jump."""
    from _structured_refs import flat_surface
    from optlab.core.models import LocalVolModel

    models = {n: LocalVolModel(flat_surface(m.vol)) for n, m in basket.models.items()}
    return Basket(basket.names, models, basket.markets, basket.corr, basket.vol_corr, basket.mixer)


def test_the_matrix_is_a_mix_with_ones_and_gives_the_measured_one_at_w0():
    c = np.array([[1.0, 0.9, 0.5], [0.9, 1.0, 0.45], [0.5, 0.45, 1.0]])
    low = low_matrix(c, W0)
    assert np.linalg.eigvalsh(low).min() > -1e-9 and np.allclose(np.diag(low), 1.0)
    back = W0 + (1 - W0) * low
    assert np.allclose(back, c, atol=1e-8)  # this one was valid: nothing repaired
    for w in (0.0, 0.3, 0.8, 1.0):
        assert np.linalg.eigvalsh(w + (1 - w) * low).min() > -1e-9
    tight = low_matrix(np.array([[1.0, 0.1, -0.5], [0.1, 1.0, 0.8], [-0.5, 0.8, 1.0]]), W0)
    assert np.linalg.eigvalsh(tight).min() > -1e-9  # repaired


def test_the_weight_rises_when_the_basket_falls_and_stays_in_its_bounds():
    m = LocalCorrelation(0.2, 0.0, 0.5, 0.3)
    y = np.array([-1.0, -0.2, 0.0, 0.2, 1.0])
    w = m.weight(1.0, y, None)
    assert np.all(np.diff(w) <= 0) and w[2] == W0 and w.min() >= 0 and w.max() <= 1
    assert m.weight(1.0, np.array([-0.2]), None)[0] == pytest.approx(W0 + 0.5)
    assert m.weight(4.0, np.array([-0.2]), None)[0] == pytest.approx(W0 + 0.25)  # in standard deviations of the basket
    assert m.bumped(0.1).down == 0.6 and m.bumped(0.1).up == 0.4 and m.bumped(-1.0).up == 0.0


@pytest.mark.slow
def test_a_flat_weight_is_the_constant_correlation_against_the_bivariate_normal():
    """A weight of W0 everywhere is the measured matrix, on the digital that reads the correlation directly."""
    basket = _with_grid(flat_basket(LocalCorrelation(0.2)))
    n, T = 400_000, 1.0
    paths = simulate_basket(basket, [T], n, 5, dt_max=1 / 12, dt_min=1 / 26)
    F = float(basket.markets["AAA"].F(T))
    both = (paths["AAA"][:, 0] > F) & (paths["BBB"][:, 0] > F)
    half = n // 2
    pair = 0.5 * (both[:half].astype(float) + both[half:].astype(float))
    d = np.array([-0.5 * 0.2 * np.sqrt(T), -0.5 * 0.3 * np.sqrt(T)])
    want = float(multivariate_normal.cdf(d, cov=[[1, RHO], [RHO, 1]]))
    within(pair.mean(), want, pair.std(ddof=1) / np.sqrt(half))


def test_a_cvc_and_a_pvp_on_one_strike_are_one_instrument_with_the_model():
    paths = simulate_basket(_with_grid(flat_basket(STEEP)), [1.0], 20_000, 8, dt_max=1 / 12, dt_min=1 / 26)
    x = np.column_stack([paths[n][:, 0] / 100.0 for n in ("AAA", "BBB")])
    for strike in (0.9, 1.0, 1.1):
        assert np.allclose(spread_payoff("cvc", strike, x), spread_payoff("pvp", strike, x), atol=1e-12)


@pytest.mark.slow
def test_the_pseudo_quotes_are_refitted_and_the_correlation_is_higher_below(ctx):
    got = calibrate(ctx, ("SPX", "AAA"), n_paths=40_000)
    assert got["origin"] == "synthetic" and "assumption" in got["note"]
    mixer = got["mixer"]
    assert mixer.down > 0 and mixer.down > mixer.up / 3
    for line in got["lines"]:
        assert abs(line["model"] - line["pseudo_quote"]) <= 4 * line["se"] + 0.03, line
    # a constant correlation cannot do it: at the correlation of the money the wings are off
    flat, wanted = calibrate_flat(ctx), targets(ctx, ("SPX", "AAA"))
    off = max(abs(a - r["price"]) for a, r in zip(flat, wanted))
    fit = max(abs(line["model"] - line["pseudo_quote"]) for line in got["lines"])
    assert off > 1.5 * fit


def calibrate_flat(ctx):
    from optlab.structured.localcorr import model_quotes

    return model_quotes(
        ctx,
        ("SPX", "AAA"),
        LocalCorrelation(basket_sigma(ctx, ("SPX", "AAA"))),
        targets(ctx, ("SPX", "AAA")),
        1.0,
        40_000,
        2027,
    )[0]


@pytest.mark.slow
def test_the_stochastic_alternative_also_fits_and_is_kept_for_comparison(ctx):
    got = calibrate(ctx, ("SPX", "AAA"), n_paths=20_000, form="stochastic")
    assert isinstance(got["mixer"], StochasticMix) and got["mixer"].rho < 0
    with pytest.raises(ValueError, match="form"):
        calibrate(ctx, ("SPX", "AAA"), form="guess")


@pytest.mark.slow
def test_a_worst_of_has_both_provisions_and_says_what_the_skew_rests_on(ctx):
    s = preset(
        "phoenix",
        **{"general.basket": "worst_of", "general.underlyings": ["SPX", "AAA"], "general.maturity_months": 12},
    )
    q = quote(s, ctx, n_paths=20_000, batch=10_000, costs=CostInputs({"corr_skew": 0.02}, mix=0.5))
    d = q.desk
    assert d.final_price == pytest.approx(98.0, abs=1e-9)
    line = d.line("local_corr")
    assert line.kind == "model" and line.origin == "synthetic" and "assumption" in line.note and len(line.fit) == 3
    assert (
        line.se < q.result.stderr
    )  # on the base's market factor: an independent run would give 1.4 times the error of one price
    bump = d.line("corr_skew")
    assert bump.used == 0.02 and bump.kind == "bump" and bump.se > 0
    assert sum(x.cost for x in d.lines) == pytest.approx(d.final_price - d.base_price, abs=1e-10)
    kept = dict(ctx.correlation_cache)
    quote(s, ctx, n_paths=4000, batch=4000, costs=CostInputs(mix=0.5))
    assert list(ctx.correlation_cache) == list(kept)  # calibrated once per basket and date


def test_a_flat_name_has_no_pseudo_quote_so_no_correlation_skew():
    market = Market.flat(100.0, 0.03, 0.01)
    c = BasketContext(
        ("SPX", "FLAT"),
        {"SPX": SPX.market, "FLAT": market},
        {"SPX": SPX.surface, "FLAT": 0.25},
        np.array([[1.0, 0.5], [0.5, 1.0]]),
        SPX.valuation,
        {"SPX": SPX},
    )
    q = quote(
        preset(
            "phoenix",
            **{"general.basket": "worst_of", "general.underlyings": ["SPX", "FLAT"], "general.maturity_months": 12},
        ),
        c,
        n_paths=4000,
        batch=4000,
        costs=CostInputs({"corr_skew": 0.02}, mix=0.5),
    )
    assert {x.key for x in q.desk.lines} == {"slv"}


def test_the_assumption_can_be_replaced():
    assert CorrelationSkew(0.5, 0.25).offset(0.9) == pytest.approx(0.05) and norm.cdf(0.0) == 0.5
