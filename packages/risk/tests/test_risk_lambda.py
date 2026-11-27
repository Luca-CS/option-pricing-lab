"""The parallel lambda shift of a basket's correlation: the matrix it gives, valid at any size with no repair, and the
lambda-vega of a worst-of put against the bivariate closed form, each pair moved by its room to 1 (or to 0)."""

import numpy as np
import pytest
from _risk_refs import FLAT, VALUATION, VOL, Q, R, day, within, worst_put_shifted

from optlab.core import Market
from optlab.risk import RiskGroup, RiskSettings
from optlab.risk.greeks import GreekOptions, compute
from optlab.risk.shocks import Shock, moved_corr
from optlab.structured.multiasset import BasketContext
from optlab.structured.portfolio import Book, Position


def test_the_shift_moves_every_pair_by_its_room():
    c = np.array([[1.0, 0.6, 0.3], [0.6, 1.0, -0.2], [0.3, -0.2, 1.0]])
    up = moved_corr(c, Shock(corr_lambda=0.5), ["A", "B", "C"])
    down = moved_corr(c, Shock(corr_lambda=-0.5), ["A", "B", "C"])
    np.testing.assert_allclose(up, c + 0.5 * (1 - c))
    np.testing.assert_allclose(down[np.triu_indices(3, 1)], 0.5 * c[np.triu_indices(3, 1)])
    for m in (up, down, moved_corr(c, Shock(corr_lambda=0.99), ["A", "B", "C"])):
        assert np.all(np.linalg.eigvalsh(m) >= -1e-12) and np.allclose(np.diag(m), 1.0)
    assert (Shock(corr_lambda=0.1) + Shock(corr_lambda=0.2)).corr_lambda == pytest.approx(0.3)


def test_the_lambda_vega_of_a_worst_of_put_against_the_bivariate_reference():
    rho, K, T, v2, q2 = 0.6, 1.0, 1.0, 0.25, 0.03
    ctx = BasketContext(
        ("SPX", "SX5E"),
        {"SPX": FLAT, "SX5E": Market.flat(100.0, R, q2)},
        {"SPX": VOL, "SX5E": v2},
        np.array([[1, rho], [rho, 1]]),
        VALUATION,
    )
    end = day(VALUATION, 365)
    script = (
        f"underlying SPX\nunderlying SX5E\nparam N = 100\nevent {end} pay {end}\n"
        f"  pay N * max({K} - min(SPX / 100, SX5E / 100), 0) as redemption\nend\n"
    )
    rep = compute(
        [RiskGroup("worst-of", Book([Position("wop", script, -1.0)]), ctx, 9)],
        options=GreekOptions(kxt="none", curves=False, sticky_strike=False, theta=False),
        settings=RiskSettings(n_paths=60_000, engine="numpy"),
    )
    g = rep.positions[0]["greeks"]
    D = np.exp(-R * T)

    def V(r_):
        return -100 * D * worst_put_shifted(K, T, (VOL, v2), r_, R, (Q, q2))

    ref = (V(rho + 0.02 * (1 - rho)) - V(0.98 * rho)) / 4  # per +0.01 of lambda
    within(g["corr_lambda|*|"][0], ref, g["corr_lambda|*|"][1])
    assert np.sign(g["corr_lambda|*|"][0]) == np.sign(g["corr|*|"][0])
    assert abs(g["corr_lambda|*|"][0]) < abs(g["corr|*|"][0])  # each pair moves by its room, a fraction of a point
