"""R1: the grid and its tents, tent-bumped surfaces, curve bumps, shocks and the revaluation engine on common random numbers."""

import json

import numpy as np
import pytest
from _risk_refs import S0, VOL, Q, R, book, engine_name, flat_ctx, forward_contract, vanilla, within
from optlab.core.blackscholes import bs_greeks, bs_price
from optlab.risk import CurveMove, Measure, RiskGroup, RiskSettings, Shock, VolMove, apply, grid, revalue
from optlab.risk.curves import bumped_market, forward_ratio
from optlab.risk.shocks import RiskEnv, sim_key
from optlab.risk.surfaces import TentBumpedSurface, arbitrage
from optlab.structured.context import PricingContext
from optlab.structured.greeks import ShiftedSurface
from optlab.structured.portfolio import Book, Position
from scipy.stats import norm

SPX = PricingContext.from_calibration()


def test_tents_are_a_smooth_partition_of_unity():
    x = np.linspace(-1.2, 0.6, 2001)
    for T in (0.02, 0.5, 2.0):
        phi, dphi, d2phi, phiT = grid.strike_tents(x, T)
        np.testing.assert_allclose(phi.sum(axis=0), 1.0, atol=1e-12)
        np.testing.assert_allclose(dphi.sum(axis=0), 0.0, atol=1e-10)
        np.testing.assert_allclose(phiT.sum(axis=0), 0.0, atol=1e-10)
        e = 1e-6
        up, down = grid.strike_tents(x + e, T), grid.strike_tents(x - e, T)
        np.testing.assert_allclose((up[0] - down[0]) / (2 * e), dphi, atol=1e-5)
        np.testing.assert_allclose((up[1] - down[1]) / (2 * e), d2phi, atol=1e-3)
        later, sooner = grid.strike_tents(x, T + e), grid.strike_tents(x, T - e)
        np.testing.assert_allclose((later[0] - sooner[0]) / (2 * e), phiT, atol=1e-5)
        assert phi.min() > -1e-12
    short = grid.strike_tents(np.array(grid.LOG_STRIKES), 1 / 365)[0]  # close to the plain hats at short maturities
    assert np.all(np.argmax(short, axis=0) == np.arange(len(grid.STRIKES))) and np.diag(short).min() > 0.75
    T = np.linspace(0.01, 7.0, 500)
    psi = grid.tenor_tents(T, order=0)[0]
    np.testing.assert_allclose(psi.sum(axis=0), 1.0, atol=1e-14)
    assert np.all(psi >= 0)


def test_tent_bumped_surface_has_exact_derivatives():
    rng = np.random.default_rng(3)
    H = rng.normal(0, 0.01, (len(grid.STRIKES), len(grid.TENORS)))
    Rm = rng.normal(0, 0.02, H.shape)
    s = TentBumpedSurface(SPX.surface, H, Rm, 0.003, 0.01, rng.normal(0, 0.005, len(grid.TENORS)))
    k = np.linspace(-0.6, 0.4, 41)
    for T in (0.07, 0.4, 0.9, 1.7, 2.6, 4.0):
        w, wk, wkk, wT = s.derivatives(k, T)
        e = 1e-5
        wp, wm = s.w(k + e, T), s.w(k - e, T)
        np.testing.assert_allclose((wp - wm) / (2 * e), wk, rtol=1e-5, atol=1e-8)
        np.testing.assert_allclose((wp - 2 * w + wm) / e**2, wkk, rtol=5e-3, atol=1e-4)
        np.testing.assert_allclose((s.w(k, T + e) - s.w(k, T - e)) / (2 * e), wT, rtol=1e-5, atol=1e-8)


def test_the_sum_of_node_bumps_is_the_parallel_bump_exactly():
    h = 0.01
    nodes = TentBumpedSurface(SPX.surface, np.full((len(grid.STRIKES), len(grid.TENORS)), h))
    parallel = ShiftedSurface(SPX.surface, h)
    k = np.linspace(-0.8, 0.5, 27)
    for T in (0.02, 0.3, 1.0, 2.2, 6.0):
        for a, b in zip(nodes.derivatives(k, T), parallel.derivatives(k, T)):
            np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-14)


def test_one_point_tent_bumps_keep_the_surface_free_of_butterfly_arbitrage():
    """Every node bumped by +-1 point: no butterfly arbitrage anywhere. Calendar: the SPX surface itself has dw/dT of only
    0.003 in the right wing at two months (127 %), where a point that fades from one tenor to the next, or a strike tent
    that widens with T, takes a few thousandths off: those right-wing short nodes are the only exceptions, bounded, and
    reported by `surfaces.arbitrage` (the local variance is floored there)."""
    base = arbitrage(SPX.surface, 3.0)
    assert base["butterfly_free"] and base["calendar_free"]
    worst, calendar = np.inf, []
    for i in range(len(grid.STRIKES)):
        for j in range(len(grid.TENORS)):
            for h in (0.01, -0.01):
                H = np.zeros((len(grid.STRIKES), len(grid.TENORS)))
                H[i, j] = h
                got = arbitrage(TentBumpedSurface(SPX.surface, H), 3.0)
                assert got["butterfly_free"], (i, j, h, got)
                worst = min(worst, got["min_g"])
                if not got["calendar_free"]:
                    calendar.append((i, j, h, got["min_wT"]))
    assert worst > 0
    assert len(calendar) <= 6 and all(j <= 2 and i >= 7 and w > -6e-3 for i, j, h, w in calendar), calendar


def _kxt_shocks(h=0.01):
    out = {}
    for i in range(len(grid.STRIKES)):
        for j in range(len(grid.TENORS)):
            out[f"n{i},{j}"] = Shock(vol={"*": VolMove(nodes={(i, j): h})})
    out["parallel_up"] = Shock(vol={"*": VolMove(h)})
    out["parallel_down"] = Shock(vol={"*": VolMove(-h)})
    return out


def test_kxt_vega_of_a_call_sits_on_the_nodes_around_its_strike_and_maturity():
    """A 9-month forward-ATM call on the SPX surface, replicated (no noise): its node vegas are on the strikes around 100 % and
    on the 6m and 1y tenors; their sum is the parallel vega up to the nonlinearity."""
    days = 274
    T = days / 365
    K = float(SPX.market.F(T))
    b = book(vanilla(K, days, valuation=SPX.valuation))
    run = revalue(
        [RiskGroup("SPX", b, SPX, 1)], _kxt_shocks(), [Measure("vega", {"parallel_up": 0.5, "parallel_down": -0.5})]
    )
    g = run.groups[0]
    assert g.method == "replication"
    nodes = np.array([[g.pnl[f"n{i},{j}"][2] for j in range(len(grid.TENORS))] for i in range(len(grid.STRIKES))])
    total = nodes.sum()
    i_atm, j6, j1 = grid.STRIKES.index(1.0), grid.TENORS.index(0.5), grid.TENORS.index(1.0)
    near = nodes[i_atm - 1 : i_atm + 2, [j6, j1]].sum()
    assert near / total > 0.85  # the strike tents are smoothed over about 0.1 sqrt(T) of log moneyness (grid.py)
    assert np.all(nodes[:, :j6] == 0) and np.all(
        nodes[:, j1 + 1 :] == 0
    )  # tenors that do not bracket 9 months: nothing
    parallel = g.measures["vega"][2]
    assert abs(total - parallel) / parallel < 2e-3  # one-sided node bumps against a central parallel one


def test_rho_and_epsilon_by_tenor_sit_on_the_bracketing_tenors_and_add_up_to_the_parallel():
    days = 292  # 0.8 year: between the 6m and 1y tenors
    T = days / 365
    ctx = flat_ctx()
    b = book(vanilla(100.0, days))
    shocks = {}
    for j in range(len(grid.TENORS)):
        shocks[f"r{j}"] = Shock(rates={"*": CurveMove(tenors={j: 1.0})})
        shocks[f"q{j}"] = Shock(dividend={"*": CurveMove(tenors={j: 1.0})})
    shocks |= {
        "r": Shock(rates={"*": CurveMove(1.0)}),
        "q": Shock(dividend={"*": CurveMove(1.0)}),
        "D": Shock(discount={"*": CurveMove(1.0)}),
        "F": Shock(forward_rate={"*": CurveMove(1.0)}),
    }
    g = revalue([RiskGroup("SPX", b, ctx, 1)], shocks).groups[0]
    rho = np.array([g.pnl[f"r{j}"][2] for j in range(len(grid.TENORS))])
    eps = np.array([g.pnl[f"q{j}"][2] for j in range(len(grid.TENORS))])
    j6, j1 = grid.TENORS.index(0.5), grid.TENORS.index(1.0)
    for v in (rho, eps):
        assert np.count_nonzero(np.abs(v) > 1e-12) == 2 and v[j6] != 0 and v[j1] != 0
    ref = bs_greeks(S0, 100.0, T, R, Q, VOL)
    d1 = (np.log(S0 / 100.0) + (R - Q + 0.5 * VOL**2) * T) / (VOL * np.sqrt(T))
    ref_eps = -S0 * T * np.exp(-Q * T) * float(norm.cdf(d1))  # dV/dq of a call
    assert abs(rho.sum() - g.pnl["r"][2]) < 1e-3 * abs(g.pnl["r"][2])
    assert abs(eps.sum() - g.pnl["q"][2]) < 1e-3 * abs(g.pnl["q"][2])
    np.testing.assert_allclose(g.pnl["r"][2], ref["rho"] * 1e-4, rtol=2e-3)
    np.testing.assert_allclose(g.pnl["q"][2], ref_eps * 1e-4, rtol=2e-3)
    # discount-only and forward-only legs split the total rho
    np.testing.assert_allclose(g.pnl["D"][2] + g.pnl["F"][2], g.pnl["r"][2], rtol=2e-3)
    np.testing.assert_allclose(g.pnl["D"][2], -T * bs_price(S0, 100.0, T, R, Q, VOL) * 1e-4, rtol=2e-3)


def test_forward_and_spot_moves_on_the_paths_equal_a_new_simulation_on_the_moved_market():
    """Under local vol at sticky moneyness the factor on the paths is exact: same numbers as pricing the moved market."""
    from optlab.structured import preset

    sheet = preset("phoenix", **{"general.maturity_months": 12, "wrapper.solve": "upfront", "coupon.rate": 0.08})
    b = Book([Position("ph", sheet, -1.0)])
    shock = Shock(spot={"*": 0.03}, dividend={"*": CurveMove(25.0, {3: 40.0})}, repo={"*": CurveMove(-10.0)})
    settings = RiskSettings(n_paths=20_000, batch=10_000, engine="numpy")
    g = revalue([RiskGroup("SPX", b, SPX, 5)], {"s": shock}, settings=settings).groups[0]
    assert g.simulations == 1  # spot and forwards ride on the base simulation
    moved = apply(SPX, shock)
    ref = (
        b.price(
            SPX,
            model="lv",
            market=moved.market,
            n_paths=20_000,
            batch=10_000,
            seed=5,
            initial_fixing=SPX.market.spot,
            engine="numpy",
        )
        if "engine" in Book.price.__code__.co_varnames
        else b.price(
            SPX, model="lv", market=moved.market, n_paths=20_000, batch=10_000, seed=5, initial_fixing=SPX.market.spot
        )
    )
    np.testing.assert_allclose(g.values["s"][0], ref.positions[0].value, rtol=1e-10)
    base = b.price(SPX, model="lv", n_paths=20_000, batch=10_000, seed=5)
    np.testing.assert_allclose(g.values["base"][0], base.positions[0].value, rtol=1e-12)


def test_forward_ratio_reproduces_the_bumped_forward():
    m = bumped_market(SPX.market, carry=CurveMove(10.0, {2: 30.0}))
    t = np.linspace(0.01, 4.0, 50)
    np.testing.assert_allclose(forward_ratio(SPX.market, m, t) * SPX.market.F(t), m.F(t), rtol=1e-13)
    np.testing.assert_allclose(m.D(t), SPX.market.D(t), rtol=1e-13)
    base_pillars = np.asarray(SPX.market.to_dict()["pillars"])
    np.testing.assert_allclose(
        bumped_market(SPX.market, CurveMove(0.0, {0: 0.0}), CurveMove(1e-12)).D(base_pillars),
        SPX.market.D(base_pillars),
        rtol=1e-12,
    )


@pytest.mark.slow
def test_vanilla_greeks_under_monte_carlo_on_the_engine_match_black_scholes():
    """Local vol on a flat surface is Black-Scholes: delta, vega and rho of a call within 4 SE of the closed forms."""
    eng = engine_name()
    n = 200_000 if eng != "numpy" else 40_000
    ctx = flat_ctx()
    b = book(vanilla(100.0, 365))
    shocks = {
        "up": Shock(spot={"*": 0.01}),
        "dn": Shock(spot={"*": -0.01}),
        "vu": Shock(vol={"*": VolMove(0.01)}),
        "vd": Shock(vol={"*": VolMove(-0.01)}),
        "ru": Shock(rates={"*": CurveMove(10.0)}),
        "rd": Shock(rates={"*": CurveMove(-10.0)}),
    }
    ms = [
        Measure("delta", {"up": 0.5, "dn": -0.5}),
        Measure("vega", {"vu": 0.5, "vd": -0.5}),
        Measure("rho", {"ru": 0.05, "rd": -0.05}),
    ]
    g = revalue([RiskGroup("SPX", b, ctx, 11)], shocks, ms, RiskSettings(model="lv", n_paths=n, engine=eng)).groups[0]
    ref = bs_greeks(S0, 100.0, 1.0, R, Q, VOL)
    gamma = ref["gamma"] * S0**2 * 1e-4
    within(g.measures["delta"][2], ref["delta"], g.measures["delta"][3], floor=gamma * 0.01)
    within(g.measures["vega"][2], ref["vega"] * 0.01, g.measures["vega"][3], floor=2e-3 * ref["vega"] * 0.01)
    within(g.measures["rho"][2], ref["rho"] * 1e-4, g.measures["rho"][3], floor=1e-3 * ref["rho"] * 1e-4)
    assert g.measures["delta"][3] < 0.002 and g.measures["vega"][3] < 0.004


def test_a_forward_contract_is_linear_in_the_spot_exactly():
    b = book(forward_contract(365))
    ctx = flat_ctx()
    g = revalue(
        [RiskGroup("SPX", b, ctx, 1)],
        {"up": Shock(spot={"*": 0.1}), "dn": Shock(spot={"*": -0.3})},
        settings=RiskSettings(model="lv", n_paths=20_000, engine="numpy"),
    ).groups[0]
    D = float(ctx.market.D(1.0))
    held = g.values["base"][0] + 100 * D  # 100 D E[S_T] / S0 on these paths
    np.testing.assert_allclose(g.pnl["up"][2], 0.1 * held, rtol=1e-12)
    np.testing.assert_allclose(g.pnl["dn"][2], -0.3 * held, rtol=1e-12)


def test_shocks_add_round_trip_through_json_and_group_by_simulation():
    a = Shock(
        spot={"SPX": 0.1},
        vol={"*": VolMove(0.01, nodes={(2, 3): 0.005})},
        rates={"USD": CurveMove(10, {1: 5})},
        corr=0.02,
    )
    b = Shock(spot={"SPX": -0.05}, vol={"*": VolMove(relative=0.01)}, dividend_relative={"*": -0.3}, roll_days=1)
    c = a + b
    assert c.spot["SPX"] == pytest.approx(1.1 * 0.95 - 1)
    assert c.vol["*"].parallel == 0.01 and c.vol["*"].relative == 0.01 and c.roll_days == 1
    back = Shock.from_dict(json.loads(c.to_json()))
    assert back.to_json() == c.to_json()
    env = RiskEnv()
    assert sim_key(SPX, Shock(spot={"*": 0.2}), env) == sim_key(SPX, Shock(rates={"*": CurveMove(50)}), env)
    assert sim_key(SPX, Shock(spot={"*": 0.2}, rule="sticky_strike"), env) != sim_key(SPX, Shock(), env)
    with pytest.raises(ValueError):
        Shock(rule="sticky_delta")
    with pytest.raises(KeyError):
        sim_key(SPX, Shock(spot={"*": 0.1}, rule="realistic"), env)
