"""Linear instruments in the risk engine (ADR-0025): valued exactly on each shock's moved market, beside the simulated
positions of their group, with no error of their own; the issuer's funding spread and the dividends of a calendar year as
risk factors. References: the instruments' closed forms on bumped flat markets, and bumping the note's own spread."""

import dataclasses
import datetime as dt

import numpy as np
import pytest
from _risk_refs import VALUATION, Q, R, flat_ctx, vanilla

from optlab.core.market import Market
from optlab.risk import RiskGroup, RiskSettings
from optlab.risk import scenarios as sc
from optlab.risk.curves import CurveMove
from optlab.risk.engine import run_group
from optlab.risk.greeks import GreekOptions, compute
from optlab.risk.shocks import Shock
from optlab.structured.hedges import LinearEnv, SpreadCurve, Valuation, parse
from optlab.structured.hedges.contracts import third_friday
from optlab.structured.portfolio import Book, Position
from optlab.structured.presets import preset

SETTINGS = RiskSettings(model="lv", n_paths=4_000, engine="numpy")
SCALE = 60.0
BANK = SpreadCurve.flat(0.006)
ENV = LinearEnv({"SPX": SCALE}, {"BANQUE-FICTIVE": BANK}, {"USD": 0.001})
EXPIRY = third_friday(2027, 6)
FUT = parse({"type": "future", "name": "SPX", "expiry": EXPIRY, "price": 6000.0})
IRS = parse({"type": "irs", "ccy": "USD", "start": VALUATION, "end": dt.date(2031, 10, 27), "rate": 0.04})
DEP = parse({"type": "deposit", "ccy": "USD", "start": VALUATION, "end": dt.date(2028, 10, 27), "rate": 0.045})
DIV = parse({"type": "dividend_swap", "name": "SPX", "year": 2027, "strike": 60.0})
FAST = GreekOptions(kxt="none", curves=True, sticky_strike=False, realistic=False, cross=False, theta=True)


def _closed(inst, market=None, funding=BANK, valuation=VALUATION, scale=SCALE) -> float:
    m = market or flat_ctx().market
    return inst.value(Valuation(m, valuation, scale, funding, 0.001))


def test_a_group_of_instruments_runs_no_simulation_and_has_no_error():
    book = Book([Position("fut", FUT, 3.0), Position("irs", IRS, 1e6), Position("dep", DEP, -2e5)], ENV)
    run = run_group(RiskGroup("SPX", book, flat_ctx(), 1), {"up": Shock(spot={"*": 0.01})}, None, SETTINGS)
    assert run.simulations == 0 and run.method == "closed_form" and run.base_total_se == 0.0
    assert run.values["base"][0] == pytest.approx(3.0 * _closed(FUT), rel=1e-12)
    assert run.values["base"][1] == pytest.approx(1e6 * _closed(IRS), rel=1e-12)
    up = flat_ctx().market.scaled(101.0)
    assert run.pnl["up"][0][0] == pytest.approx(3.0 * (_closed(FUT, up) - _closed(FUT)), rel=1e-9)
    assert run.pnl["up"][0][1] == pytest.approx(0.0, abs=1e-6)  # a swap does not see the spot


def test_instruments_beside_a_note_leave_its_numbers_bit_for_bit_and_add_exact_greeks():
    note = Position("call", vanilla(100.0, 365), -1.0)
    alone = compute([RiskGroup("SPX", Book([note]), flat_ctx(), 4)], options=FAST, settings=SETTINGS)
    hedged = Book([note, Position("fut", FUT, 2.0), Position("irs", IRS, 1e6)], ENV)
    rep = compute([RiskGroup("SPX", hedged, flat_ctx(), 4)], options=FAST, settings=SETTINGS)
    a, b = alone.positions[0]["greeks"], next(p for p in rep.positions if p["name"] == "call")["greeks"]
    for k, v in a.items():
        assert b[k] == v, k  # the note's Greeks do not move
    fut = next(p for p in rep.positions if p["name"] == "fut")["greeks"]
    F = float(flat_ctx().market.F((EXPIRY - VALUATION).days / 365))
    assert fut["delta|SPX|"][0] == pytest.approx(2.0 * 50.0 * SCALE * F * 0.01, rel=1e-6)
    assert fut["delta|SPX|"][1] < 1e-9 and abs(fut["vega|SPX|"][0]) < 1e-9  # exact, up to rounding
    irs = next(p for p in rep.positions if p["name"] == "irs")["greeks"]
    bumped = Market.flat(100.0, R + 1e-4, Q)  # +1 bp on the rate, discount and forward together
    rho = 1e6 * (_closed(IRS, bumped) - _closed(IRS))
    assert irs["rho|USD|"][0] == pytest.approx(rho, rel=1e-3)  # central at 10 bp against one-sided at 1 bp
    tenors = sum(v[0] for k, v in irs.items() if k.startswith("rho_tenor|USD|"))
    assert tenors == pytest.approx(irs["rho|USD|"][0], rel=0.02)  # the tents add up to the parallel rho
    assert book_total(rep, "delta|SPX|") == pytest.approx(book_total(alone, "delta|SPX|") + fut["delta|SPX|"][0])


def book_total(rep, key):
    return rep.book[key][0]


def test_the_funding_greek_of_a_note_is_its_own_spread_bumped():
    """A note discounted on the issuer's spread: +1 bp of the funding shock equals +1 bp of the sheet's spread, path by
    path, so on one seed the two agree to rounding."""
    sheet = preset("phoenix", **{"general.strike_date": VALUATION, "coupon.rate": 0.05})
    s = float(sheet.get("wrapper.issuer_spread") or 0.0)
    wider = sheet.replace(**{"wrapper.issuer_spread": s + 1e-4})
    ctx = flat_ctx()
    base = run_group(
        RiskGroup("SPX", Book([Position("n", sheet, -1.0)]), ctx, 7),
        {"f": Shock(funding={"*": CurveMove(1.0)})},
        None,
        SETTINGS,
    )
    moved = run_group(RiskGroup("SPX", Book([Position("n", wider, -1.0)]), ctx, 7), {}, None, SETTINGS)
    assert base.pnl["f"][2] == pytest.approx(moved.values["base"][0] - base.values["base"][0], rel=1e-9)
    assert base.pnl["f"][2] > 0  # the desk is short the note: a wider spread lowers what it owes


def test_funding_and_dividend_year_greeks_match_the_closed_forms():
    book = Book([Position("dep", DEP, 1e6), Position("div", DIV, 10.0)], ENV)
    rep = compute([RiskGroup("SPX", book, flat_ctx(), 2)], options=FAST, settings=SETTINGS)
    dep = next(p for p in rep.positions if p["name"] == "dep")["greeks"]
    wide = SpreadCurve.flat(0.006 + 1e-4)
    want = 1e6 * (_closed(DEP, funding=wide) - _closed(DEP))
    assert dep["funding|BANQUE-FICTIVE|"][0] == pytest.approx(want, rel=1e-3)
    assert sum(v[0] for k, v in dep.items() if k.startswith("funding_tenor|")) == pytest.approx(want, rel=0.02)
    div = next(p for p in rep.positions if p["name"] == "div")["greeks"]
    # by hand: the flat market whose 2027 dividends are 1 % higher, F' = F exp(-0.01 q (min(T, b) - a)+)
    lo, hi = (dt.date(2027, 1, 1) - VALUATION).days / 365, (dt.date(2028, 1, 1) - VALUATION).days / 365
    T = np.unique(np.r_[np.linspace(1 / 365, 6.0, 2191), lo, hi])  # the year's edges are pillars: a kink at each
    flat = flat_ctx().market
    F = np.asarray(flat.F(T)) * np.exp(-0.01 * Q * np.clip(np.minimum(T, hi) - lo, 0.0, None))
    moved = Market(100.0, T, np.asarray(flat.D(T)), F)
    want = 10.0 * (_closed(DIV, moved) - _closed(DIV))
    assert div["dividend_year|SPX|2027"][0] == pytest.approx(want, rel=1e-4)
    assert abs(div["dividend_year|SPX|2028"][0]) < 1e-9  # later dividends move neither the year nor its forward
    # earlier ones lower the forward the year's dividends are paid on: a small effect of the other sign
    assert -0.01 * want < div["dividend_year|SPX|2026"][0] < 0


def test_every_year_moved_together_is_the_relative_dividend_move():
    """On a simulated position: the dividends of every year moved by 10 % is the move of all the dividends by 10 %."""
    book = Book([Position("call", vanilla(100.0, 900), -1.0)])
    years = {y: -0.1 for y in range(2026, 2030)}
    run = run_group(
        RiskGroup("SPX", book, flat_ctx(), 5),
        {"years": Shock(dividend_years={"SPX": years}), "all": Shock(dividend_relative={"SPX": -0.1})},
        None,
        SETTINGS,
    )
    assert run.pnl["years"][2] == pytest.approx(run.pnl["all"][2], rel=1e-4)
    assert run.simulations == 1  # forward moves ride on the base's simulation


def test_the_scenario_grid_moves_a_future_by_its_forward():
    book = Book([Position("fut", FUT, 1.0)], ENV)
    rep = sc.run([RiskGroup("SPX", book, flat_ctx(), 1)], [], settings=SETTINGS, spots=(-0.2, 0.0), vols=(0.0, 0.1))
    g = rep.grid
    assert g is not None
    F = float(flat_ctx().market.F((EXPIRY - VALUATION).days / 365))
    i, j = g["spots"].index(-0.2), g["vols"].index(0.1)
    assert g["pnl"][i][j][0] == pytest.approx(50.0 * SCALE * F * -0.2, rel=1e-9) and g["pnl"][i][j][1] == 0.0


def test_a_rolled_book_keeps_its_instruments():
    from optlab.risk.engine import rolled_book

    book = Book([Position("call", vanilla(100.0, 365), -1.0), Position("fut", FUT, 1.0)], ENV)
    items = book._prepare(flat_ctx())
    rolled = rolled_book(book, items, VALUATION, VALUATION + dt.timedelta(days=3), 100.0)
    assert [p.name for p in rolled.positions] == ["call", "fut"] and rolled.linear is ENV
    assert dataclasses.replace(ENV) == rolled.linear and np.isfinite(rolled.linear_values(flat_ctx())[0])
