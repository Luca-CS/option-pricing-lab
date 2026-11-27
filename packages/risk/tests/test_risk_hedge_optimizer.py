"""The hedge optimizer (H3): a book of one sold vanilla gets the same vanilla back (residual 0, its half-spread as the
cost), a delta-only target hedged by futures leaves about no delta, integer lots stay integers, the solve reaches what the
tolerances allow and says what it misses, and the control grid moves by the proposal's own P&L."""

import datetime as dt

import numpy as np
import pytest
from _risk_refs import VALUATION, flat_ctx

from optlab.risk import RiskGroup, RiskSettings
from optlab.risk.hedge import Candidate, Target, optimise, solve
from optlab.structured.hedges import LinearEnv, parse
from optlab.structured.hedges.contracts import third_friday
from optlab.structured.portfolio import Book, Position
from optlab.structured.presets import preset

SETTINGS = RiskSettings(model="lv", n_paths=6_000, engine="numpy", smoothing=0.005)
ENV = LinearEnv({"SPX": 60.0})
PUT = {"type": "vanilla", "name": "SPX", "expiry": dt.date(2027, 10, 27), "strike": 5700.0, "kind": "put"}
EXPIRY = third_friday(2027, 3)
FUT = parse({"type": "future", "name": "SPX", "expiry": EXPIRY, "price": 6100.0})


def test_a_sold_vanilla_gets_itself_back_at_its_half_spread():
    book = Book([Position("sold", parse(PUT), -1.0)], ENV)
    cands = [
        Candidate("the same put", parse(PUT), 2.5, 10.0, False, "SPX"),
        Candidate("a future", FUT, 12.5, 100.0, False, "SPX"),
    ]
    run = optimise(
        [RiskGroup("SPX", book, flat_ctx(), 3)], cands, settings=SETTINGS, grid=False, relative=0.0, floor=1e-6
    )
    q = {p["label"]: p["quantity"] for p in run.proposal}
    assert q.get("the same put") == pytest.approx(1.0, abs=1e-6) and abs(q.get("a future", 0.0)) < 1e-6
    assert run.cost == pytest.approx(2.5, rel=1e-6) and not run.missed
    assert all(abs(t["after"]) < 1e-6 * max(1.0, abs(t["before"])) for t in run.targets)


def test_a_delta_target_is_met_by_futures_in_integer_lots_and_the_grid_moves_by_them():
    note = preset("phoenix", **{"general.strike_date": VALUATION, "coupon.rate": 0.04, "general.notional": 1e7})
    book = Book([Position("phx", note, -1.0)], ENV)
    cands = [Candidate("ES", FUT, 12.5, 50.0, True, "SPX")]
    run = optimise(
        [RiskGroup("SPX", book, flat_ctx(), 5)],
        cands,
        targets=[Target("delta|SPX|", 0.0, 1000.0)],
        settings=SETTINGS,
        grid=True,
    )
    (p,) = run.proposal
    assert p["quantity"] == round(p["quantity"]) and p["quantity"] > 0  # the desk is short delta: it buys futures
    (t,) = run.targets
    assert abs(t["after"]) <= 1000.0 and abs(t["before"]) > 3000.0
    F = float(flat_ctx().market.F((EXPIRY - VALUATION).days / 365))
    assert run.grid is not None
    i, j = run.grid["spots"].index(-0.2), run.grid["vols"].index(0.0)
    future_pnl = p["quantity"] * 50.0 * 60.0 * F * -0.2
    assert run.grid["after"][i][j] == pytest.approx(run.grid["before"][i][j] + future_pnl, rel=1e-9)


def test_the_solve_says_what_the_limits_cannot_reach():
    G = np.array([100.0, 40.0])
    A = np.array([[1.0], [0.0]])  # one instrument, no exposure to the second Greek
    cands = [Candidate("x", FUT, 1.0, 1000.0)]
    sol = solve(G, A, [Target("a", 0.0, 1.0), Target("b", 0.0, 1.0)], cands)
    assert sol.quantities[0] == pytest.approx(-99.0, abs=1e-6)  # just inside the tolerance: the least cost
    assert sol.missed == ["b"] and sol.cost == pytest.approx(99.0, abs=1e-6)
    tight = solve(G[:1], A[:1], [Target("a", 0.0, 1.0)], [Candidate("x", FUT, 1.0, 10.0)])
    assert tight.quantities[0] == pytest.approx(-10.0) and tight.missed == ["a"]  # the limit binds
