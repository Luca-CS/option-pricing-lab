"""R5: historical VaR by full revaluation on the lake's factors."""

import numpy as np
import pytest
from _paths import real_lake
from _risk_refs import book, flat_ctx, forward_contract, vanilla

from optlab.risk import RiskGroup
from optlab.risk import var as V
from optlab.risk.greeks import GreekOptions, compute


@pytest.fixture(scope="module")
def lake():
    from optlab.marketdata.lake import Lake

    lk = Lake(real_lake())
    try:
        if (
            lk.read("vol_surfaces", where="underlying = 'SPX' AND layer = 'reconstructed'", columns="count(*) AS n")[
                "n"
            ].iloc[0]
            < 600
        ):
            pytest.skip("no reconstructed SPX surfaces in the lake")
    except Exception:  # noqa: BLE001
        pytest.skip("no lake")
    return lk


def test_var_and_es_statistics():
    x = np.arange(-50, 50, dtype=float)  # 100 scenarios
    st = V.var_es(x)
    assert st["var"] == 50.0  # 1 % of 100 scenarios: the worst one
    assert st["es"] == pytest.approx(49.0)  # 2.5 %: the mean of the three worst (lower quantile at index 2)
    assert V.kupiec(5, 500)["p_value"] == pytest.approx(1.0)
    assert V.kupiec(15, 500)["p_value"] < 0.01
    assert V.kupiec(0, 500)["lr"] > 0


@pytest.mark.lake
def test_full_reval_var_of_a_forward_is_the_quantile_of_the_returns_times_the_position(lake):
    ctx = flat_ctx()
    b = book(forward_contract(365))
    g = [RiskGroup("SPX", b, ctx, 1)]
    out = V.run_var(g, lake, "2026-11-04", days=250, ten_day=False, factors=("spot",))
    levels, _ = V.factor_levels(lake, ["SPX"], ["USD"], "2025-09-01", "2026-11-04")
    r = np.asarray(out["one_day"]["pnl"])
    F, D = float(ctx.market.F(1.0)), float(ctx.market.D(1.0))
    c = 100 * D * F / 100.0
    ch = V.changes(levels, 1)
    rets = np.expm1(
        ch.loc[[__import__("datetime").date.fromisoformat(d) for d in out["one_day"]["dates"]], "spot:SPX"].to_numpy()
    )
    np.testing.assert_allclose(r, c * rets, rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(out["one_day"]["var"], -c * np.quantile(rets, 0.01, method="lower"), rtol=1e-12)
    assert out["one_day"]["var_se"] == 0.0
    assert out["one_day"]["factors"]["spot"][0] == pytest.approx(
        r[out["one_day"]["dates"].index(out["one_day"]["var_date"])]
    )


@pytest.mark.lake
def test_full_reval_of_a_vanilla_stays_close_to_delta_gamma_vega(lake):
    ctx = flat_ctx()
    b = book(vanilla(100.0, 365))
    g = [RiskGroup("SPX", b, ctx, 1)]
    rep = compute(g, options=GreekOptions(kxt="none", sticky_strike=False, theta=False))
    out = V.run_var(g, lake, "2026-11-04", days=250, ten_day=True, greeks_report=rep)
    one = out["one_day"]
    assert one["var"] > 0 and 0.7 < one["es"] / one["var"] < 1.5  # ES 97.5 % and VaR 99 % are close for a thin tail
    gap = abs(one["taylor"]["var"] - one["var"]) / one["var"]
    assert gap < 0.1, gap  # measured and reported: a one-year vanilla is nearly quadratic over a day
    assert out["ten_day"]["var"] > one["var"]
    total = sum(v[0] for v in one["factors"].values())
    assert total == pytest.approx(one["pnl"][one["dates"].index(one["var_date"])])
    assert set(one["contributions"]["es"]) == {"p0"}
    np.testing.assert_allclose(one["contributions"]["es"]["p0"], -one["es"], rtol=1e-12)


def test_processes_get_the_engine_of_the_parent():
    """engines.set_default lives in the parent process (the desk sets it): the workers are told the engine explicitly."""
    from optlab.core import engines

    engines.set_default("gpu")
    try:
        st = V.worker_settings(V.RiskSettings(), 5)
        assert st.engine == "gpu" and st.eval_threads == 1
        assert V.worker_settings(V.RiskSettings(), 1).engine is None
        assert V.worker_settings(V.RiskSettings(engine="cpu", eval_threads=2), 5).engine == "cpu"
    finally:
        engines.set_default(None)
