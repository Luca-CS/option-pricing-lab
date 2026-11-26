"""The vol part of the explained P&L opened: node by node, by delta bucket, and under each spot-vol rule."""

import datetime as dt

import numpy as np
import pytest
from _stores import risk_store

from optlab.marketdata.replay import struck_sheet
from optlab.marketdata.synthetic import synthetic_lake
from optlab.risk import RiskSettings, grid
from optlab.risk import explain as X
from optlab.risk import pnl as P
from optlab.risk.greeks import shifted_move
from optlab.risk.surfaces import bumped_surface
from optlab.structured import preset
from optlab.structured.trade import Trade

SETTINGS = RiskSettings(n_paths=4_000, batch=4_000, engine="numpy")
A, B = "2020-03-12", "2020-03-13"
ATM = grid.STRIKES.index(1.0)


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    return synthetic_lake(tmp_path_factory.mktemp("explain") / "lake", "2020-01-01", "2020-03-31")[0]


def brc():
    """A six-month BRC on SPX struck on 15/01/2020, daily barrier at 70 %: alive four months after the day explained."""
    sheet = preset(
        "barrier_reverse_convertible",
        **{
            "general.maturity_months": 6,
            "downside.barrier": 0.7,
            "general.underlying": "SPX",
            "general.notional": 100.0,
            "wrapper.solve": "upfront",
            "coupon.rate": 0.05,
        },
    )
    strike = dt.date(2020, 1, 15)
    return Trade.from_term_sheet("BRC", struck_sheet(sheet, strike), -1.0, strike, strike)


def test_the_buckets_split_each_node_between_the_two_deltas_around_it():
    values, deltas = np.zeros((10, 8)), np.full((10, 8), 0.5)
    values[3, 2], deltas[3, 2] = 2.0, 0.8  # between 90 and 75 delta: a third to 90, two thirds to 75
    values[0, 0], deltas[0, 0] = 1.0, 0.97  # beyond the ends: the end buckets
    values[9, 7], deltas[9, 7] = -1.0, 0.02
    values[5, 4] = 0.5  # on a bucket: all of it there
    out = X.delta_buckets(values, deltas)
    assert out.sum(axis=0) == pytest.approx(values.sum(axis=0))
    assert out[0, 2] == pytest.approx(2 / 3) and out[1, 2] == pytest.approx(4 / 3)
    assert out[0, 0] == 1.0 and out[-1, 7] == -1.0 and out[2, 4] == 0.5


def test_a_tenor_moves_its_level_then_each_node_beyond_it():
    moves = np.zeros((10, 8))
    moves[:, 1] = 0.01
    moves[2, 1] = 0.03
    steps = X.node_steps(moves, [0, 1])
    assert [(k, j, i) for k, j, i, _ in steps] == [("level", 1, None), ("node", 1, 2)]
    assert steps[0][3] == {"term": {1: 0.01}} and steps[1][3]["nodes"] == {(2, 1): pytest.approx(0.02)}
    assert [k for k, *_ in X.node_steps(moves, [0, 1], "tenor")] == ["level", "shape"]
    assert X.node_steps(moves, [0]) == []  # a tenor the book does not reach is not moved
    assert X._live_tenors(0.34) == [0, 1, 2]  # the 1y tent starts at 6 months


def test_the_nodes_read_in_delta_and_the_shifted_move_is_absolute_at_the_money_relative_in_the_wings():
    flat = 0.2
    d = grid.node_deltas(flat)
    assert d[ATM, 3] == pytest.approx(0.5398, abs=1e-4)  # N(0.1): 54 delta at one year and 20 % vol
    assert np.all(np.diff(d, axis=0) < 0)  # the higher the strike, the lower the call delta
    from _synthetic_surface import TRUE_SSVI

    move = shifted_move(TRUE_SSVI, 0.01)
    moved = grid.node_vols(bumped_surface(TRUE_SSVI, nodes=move.matrices()[0], relative_nodes=move.matrices()[1]))
    base = grid.node_vols(TRUE_SSVI)
    assert moved[ATM] - base[ATM] == pytest.approx(np.full(len(grid.TENORS), 0.01), abs=1e-12)
    assert moved - base == pytest.approx(0.01 * base / base[ATM], abs=1e-12)
    wide = shifted_move(TRUE_SSVI, 0.01, shift=100.0)  # a large shift is the parallel move
    moved = grid.node_vols(bumped_surface(TRUE_SSVI, nodes=wide.matrices()[0], relative_nodes=wide.matrices()[1]))
    assert moved - base == pytest.approx(np.full(base.shape, 0.01), abs=2e-4)
    assert shifted_move(flat, 0.01).parallel == 0.01


def test_the_vol_move_node_by_node_adds_up_and_each_rule_splits_the_same_amount(lake, tmp_path):
    store = risk_store(tmp_path)
    trades = [brc()]
    day = P.explain_day(lake, trades, A, B, store, SETTINGS)
    betas = {"SPX": [-0.6] * len(grid.TENORS)}
    out = X.explain_detail(lake, trades, A, B, store, SETTINGS, rule="sticky_moneyness", detail="node", betas=betas)
    sm = out["rules"]["sticky_moneyness"]["steps"]
    for s in ("spot", "vol", "skew"):  # the stored cascade's steps, bit for bit: same seeds, same contexts
        assert sm[s] == pytest.approx(day["steps"][s][0], abs=1e-9)
    assert out["vol_total"] == pytest.approx(sm["vol"] + sm["skew"])
    assert abs(out["check"]) < 1e-9  # nodes and residual add up to the vol and skew steps
    spx = out["names"]["SPX"]
    nodes = np.asarray(spx["nodes"])
    assert nodes.sum(axis=0) == pytest.approx(spx["by_tenor"], abs=1e-12)
    assert np.asarray(spx["buckets"]).sum(axis=0) == pytest.approx(nodes.sum(axis=0), abs=1e-12)
    assert spx["skipped"] == [3, 4, 5, 6, 7] and not np.any(nodes[:, 3:])
    assert np.count_nonzero(nodes[:, :3]) > 20  # every live node moved and was revalued
    deltas = np.asarray(spx["deltas"])
    assert 0.45 < deltas[ATM, 0] < 0.6 and np.all(np.diff(deltas, axis=0) < 0)
    assert out["revaluations"] > 30
    # the three rules end on d's surface: spot + vol + skew is the same amount, split differently
    assert set(out["rules"]) == {"sticky_moneyness", "sticky_strike", "realistic"}
    totals = {r: sum(v["steps"][s] for s in X.RULE_STEPS) for r, v in out["rules"].items()}
    assert all(t == pytest.approx(totals["sticky_moneyness"], abs=1e-9) for t in totals.values())
    ss = out["rules"]["sticky_strike"]["steps"]
    assert abs(ss["spot"] - sm["spot"]) > 1e-6
    assert all(np.isfinite(v) for r in out["rules"].values() for v in r["predicted"].values())
    again = X.explain_detail(lake, trades, A, B, store, SETTINGS, rule="sticky_moneyness", detail="node", betas=betas)
    assert again["reused"] and again["run_id"] == out["run_id"]


def test_under_sticky_strike_the_tenors_add_up_to_what_that_rule_leaves_and_realistic_needs_betas(lake, tmp_path):
    store = risk_store(tmp_path)
    trades = [brc()]
    out = X.explain_detail(lake, trades, A, B, store, SETTINGS, rule="sticky_strike", detail="tenor")
    ss = out["rules"]["sticky_strike"]["steps"]
    assert out["vol_total"] == pytest.approx(ss["vol"] + ss["skew"])
    assert abs(out["check"]) < 1e-9
    spx = out["names"]["SPX"]
    assert "nodes" not in spx and len(spx["shape_se"]) == len(grid.TENORS) == len(spx["level_se"])
    assert out["unavailable"] == {"realistic": "no spot-vol betas for these names"}
    with pytest.raises(ValueError, match="betas"):
        X.explain_detail(lake, trades, A, B, store, SETTINGS, rule="realistic")
    with pytest.raises(ValueError, match="detail"):
        X.explain_detail(lake, trades, A, B, store, SETTINGS, detail="strike")
