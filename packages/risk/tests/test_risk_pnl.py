"""R6: EOD runs, backfill, and the explained P&L cascade on the lake's real closes."""

import datetime as dt

import numpy as np
import pytest
from _paths import spx_lake
from _stores import risk_store

from optlab.marketdata.replay import struck_sheet
from optlab.risk import RiskSettings
from optlab.risk import pnl as P
from optlab.structured import preset
from optlab.structured.trade import Trade

SETTINGS = RiskSettings(n_paths=20_000, batch=10_000, engine="numpy")


@pytest.fixture(scope="module")
def lake():
    return spx_lake()


def brc_2020():
    """The replay's BRC (reports/replay.md): 12 months, daily barrier 70 %, struck 15/01/2020, coupon 2.57 % p.a., per 100."""
    sheet = preset(
        "barrier_reverse_convertible",
        **{
            "general.maturity_months": 12,
            "downside.barrier": 0.7,
            "general.underlying": "SPX",
            "general.notional": 100.0,
            "wrapper.solve": "upfront",
            "coupon.rate": 0.0257,
        },
    )
    strike = dt.date(2020, 1, 15)
    return Trade.from_term_sheet("BRC-2020", struck_sheet(sheet, strike), 1.0, strike, strike)


def phoenix_2026():
    """A Phoenix sold in June 2026; called at its first quarterly date after the rally, so worth nothing by November."""
    sheet = preset(
        "phoenix",
        **{
            "general.maturity_months": 24,
            "general.underlying": "SPX",
            "general.notional": 100.0,
            "wrapper.solve": "upfront",
            "coupon.rate": 0.07,
        },
    )
    strike = dt.date(2026, 6, 15)
    return Trade.from_term_sheet("PHX-2026", struck_sheet(sheet, strike), -1.0, strike, strike)


def brc_2026():
    """A BRC sold in June 2026, alive in November (no call, daily barrier at 70 %)."""
    sheet = preset(
        "barrier_reverse_convertible",
        **{
            "general.maturity_months": 12,
            "downside.barrier": 0.7,
            "general.underlying": "SPX",
            "general.notional": 100.0,
            "wrapper.solve": "upfront",
            "coupon.rate": 0.06,
        },
    )
    strike = dt.date(2026, 6, 15)
    return Trade.from_term_sheet("BRC-2026", struck_sheet(sheet, strike), -1.0, strike, strike)


@pytest.mark.lake
def test_the_cascade_adds_up_to_the_total_pnl_to_the_cent_on_the_crash_day(lake, tmp_path):
    store = risk_store(tmp_path)
    t = brc_2020()
    out = P.explain_day(lake, [t], "2020-03-12", "2020-03-13", store, SETTINGS)
    steps = sum(v[0] for v in out["steps"].values())
    assert steps == pytest.approx(out["total"], abs=1e-9)
    tr = out["trades"]["BRC-2020"]
    assert tr["total"] == pytest.approx(tr["value_b_and_cash"] - tr["value_a"], abs=1e-9)
    g = out["groups"][0]
    assert abs(g["reconciliation_gap"]) < 0.01  # the cascade ends on d's EOD value (same seed, same paths)
    assert out["steps"]["spot"][0] > 1.0  # +9.3 % on the index: the note gains
    assert abs(out["steps"]["vol"][0]) > 0.1
    # the Greeks of the day before leave a large part unexplained on a 9 % move: measured, reported, not hidden
    out["unexplained_share"] = abs(sum(out["unexplained"].values())) / abs(out["total"])
    assert out["unexplained_share"] > 0.05


@pytest.mark.lake
def test_eod_is_stored_replayed_bit_for_bit_and_the_backfill_is_idempotent(lake, tmp_path):
    store = risk_store(tmp_path)
    trades = [brc_2026(), phoenix_2026()]
    first = P.eod(lake, trades, "2026-11-04", store, SETTINGS)
    again = P.eod(lake, trades, "2026-11-04", store, SETTINGS)
    assert again["reused"] and again["run_id"] == first["run_id"]
    fresh = P.eod(lake, trades, "2026-11-04", None, SETTINGS)
    assert fresh["value"] == first["value"] and fresh["positions"][0]["greeks"] == first["positions"][0]["greeks"]
    assert {p["trade_id"] for p in first["positions"]} == {"BRC-2026", "PHX-2026"}
    back = P.backfill(lake, trades, "2026-11-02", "2026-11-04", store, SETTINGS)
    assert [b["reused"] for b in back] == [False, False, True]
    again = P.backfill(lake, trades, "2026-11-02", "2026-11-04", store, SETTINGS)
    assert all(b["reused"] for b in again)
    assert len(store.runs("eod")) == 3 and len(store.runs("pnl")) == 2
    cum = P.cumulative(store, "2026-11-02", "2026-11-04")
    assert cum["total"] == pytest.approx(sum(d["total"] for d in cum["daily"]))
    assert len(cum["curve"]) == 2


@pytest.mark.lake
def test_on_a_quiet_day_the_greeks_explain_most_of_the_pnl(lake, tmp_path):
    store = risk_store(tmp_path)
    out = P.explain_day(lake, [brc_2026()], "2026-11-03", "2026-11-04", store, SETTINGS)
    total, unexplained = out["total"], sum(out["unexplained"].values())
    share = 1 - abs(unexplained) / abs(total)
    print("quiet day: total", total, "predicted", out["predicted_total"], "explained share", share)
    assert np.isfinite(share)
    assert share > 0.9  # measured: 99 % on 04/11/2026 (reports/risk.md)


@pytest.mark.lake
def test_hedges_ride_in_the_eod_and_the_cascade_and_a_what_if_never_does(lake, tmp_path):
    """A sold BRC hedged by a future, a deposit and a dividend swap: the EOD values them, the cascade still adds up to
    the cent and ends on the next EOD, and a simulated trade stays out of both."""
    from optlab.structured.hedges import parse
    from optlab.structured.hedges.contracts import third_friday

    store = risk_store(tmp_path)
    a, b = dt.date(2026, 11, 3), dt.date(2026, 11, 4)
    fut = parse({"type": "future", "name": "SPX", "expiry": third_friday(2026, 12), "price": 6800.0})
    dep = parse({"type": "deposit", "ccy": "USD", "start": "2026-10-01", "end": "2027-10-01", "rate": 0.045})
    div = parse({"type": "dividend_swap", "name": "SPX", "year": 2027, "strike": 75.0})
    trades = [
        brc_2026(),
        Trade.from_instrument("H-FUT", fut, -1.0, a, tags=("hedge",)),
        Trade.from_instrument("H-DEP", dep, 1e5, a, fill=1.0),
        Trade.from_instrument("H-DIV", div, 10.0, a),
        Trade.from_instrument("H-WHATIF", fut, 5.0, a, status="simulated"),
    ]
    first = P.eod(lake, trades, a, store, SETTINGS, greeks=False)
    ids = {p["trade_id"] for p in first["positions"]}
    assert ids == {"BRC-2026", "H-FUT", "H-DEP", "H-DIV"}  # the what-if is not in the official EOD
    hedge = next(p for p in first["positions"] if p["trade_id"] == "H-FUT")
    assert hedge["value_se"] == 0.0
    out = P.explain_day(lake, trades, a, b, store, SETTINGS)
    assert sum(v[0] for v in out["steps"].values()) == pytest.approx(out["total"], abs=1e-9)
    assert abs(out["groups"][0]["reconciliation_gap"]) < 0.01
    fut_steps = out["trades"]["H-FUT"]["steps"]
    assert fut_steps["vol"] == 0.0 and fut_steps["skew"] == 0.0  # a future has no vega
    assert abs(fut_steps["spot"]) > 0  # a short future moves with the index
