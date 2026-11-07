"""Swap with a funding leg (floating rate plus spread until redemption)."""

import numpy as np
import pytest
from _structured_refs import FLAT, flat_context
from optlab.structured.cashflows import FundedSwap, Swap
from optlab.structured.compiler import compile_sheet
from optlab.structured.portfolio import Book, Position
from optlab.structured.presets import preset
from optlab.structured.pricer import price
from optlab.structured.quote import quote, wrapper
from optlab.structured.schema import TermSheet, TermSheetError
from optlab.structured.script.timeline import Clock, build_timeline

CTX = flat_context()
FUNDED = {"wrapper.format": "swap", "wrapper.funding": "floating"}


def mc(sheet, n_paths=40_000, seed=4):
    p = compile_sheet(sheet, CTX.valuation)
    return price(
        p.program,
        CTX.model("bs"),
        FLAT,
        fmt=wrapper(sheet, p),
        n_paths=n_paths,
        seed=seed,
        batch=10_000,
        valuation=CTX.valuation,
    ), p


def test_funded_swap_is_the_note_minus_par_path_by_path():
    # single curve, no spread, no payment lag, funding on the call dates: the floating leg telescopes to
    # N (1 - D(call date)), which is exactly what the note holder earns on the notional
    base = {"general.pay_lag": 0, "wrapper.solve": "upfront"}
    note, _ = mc(preset("phoenix", **base))
    swap, _ = mc(preset("phoenix", **base, **FUNDED))
    assert swap.price == pytest.approx(note.price - 100, abs=1e-9)
    assert swap.stderr == pytest.approx(note.stderr, rel=1e-9)


def test_solved_coupon_is_the_same_in_note_and_in_funded_swap_without_lag():
    base = {"general.pay_lag": 0}
    note = quote(preset("phoenix", **base), CTX, model="bs", control=None, n_paths=40_000, batch=10_000)
    swap = quote(preset("phoenix", **base, **FUNDED), CTX, model="bs", control=None, n_paths=40_000, batch=10_000)
    assert swap.result.price == pytest.approx(-2.0, abs=1e-10)
    assert swap.coupon_rate == pytest.approx(note.coupon_rate, abs=1e-10)


def test_bullet_funding_leg_is_deterministic():
    sheet = preset("reverse_convertible", **{"wrapper.solve": "upfront", **FUNDED, "wrapper.funding_spread": 0.01})
    funded, p = mc(sheet)
    plain = price(
        p.program, CTX.model("bs"), FLAT, fmt=Swap(), n_paths=40_000, seed=4, batch=10_000, valuation=CTX.valuation
    )
    fs = wrapper(sheet, p)
    assert isinstance(fs, FundedSwap) and len(fs.periods) == 4
    leg = 100 * fs.funding_amounts(FLAT, 100.0).sum() / 100
    assert funded.price == pytest.approx(plain.price - leg, abs=1e-10)
    assert funded.stderr == pytest.approx(plain.stderr, rel=1e-9)
    # one year of funding at r + 1 % on 100, ACT/360
    assert leg == pytest.approx(100 * (1 - float(FLAT.D(1.0))) + 1.0 * 365 / 360, rel=2e-2)


def test_funding_spread_sensitivity_is_the_expected_funded_annuity():
    s0, p = mc(preset("phoenix", **{"wrapper.solve": "upfront", **FUNDED}))
    s1, _ = mc(preset("phoenix", **{"wrapper.solve": "upfront", **FUNDED, "wrapper.funding_spread": 0.01}))
    fs = wrapper(preset("phoenix", **FUNDED), p)
    tl = build_timeline(p.program, Clock(CTX.valuation))
    alive_after = 1 - np.cumsum(s0.termination)
    annuity = 0.0
    for s, _, pay, tau in fs.periods:
        k = int(np.searchsorted(tl.event_times, s + 1e-12, side="right"))
        alive = 1.0 if k == 0 else alive_after[k - 1]
        annuity += tau * float(FLAT.D(pay)) * alive
    assert (s1.price - s0.price) / 0.01 == pytest.approx(-100 * annuity, rel=1e-9)  # price in % of N
    assert 0.5 < annuity < 2.0


def test_funding_fields_belong_to_the_swap():
    with pytest.raises(TermSheetError, match="not used here"):
        TermSheet("autocall", {"wrapper": {"format": "note", "funding": "floating"}})
    with pytest.raises(TermSheetError, match="multiple of"):
        preset("phoenix", **{**FUNDED, "wrapper.funding_frequency_months": 5})
    assert preset("phoenix", **FUNDED).get("wrapper.funding_spread") == 0.0


def test_book_positions_use_the_funded_wrapper():
    sheet = preset("phoenix", **{"wrapper.solve": "upfront", "general.maturity_months": 12, **FUNDED})
    book = Book([Position("funded", sheet, 1.0)]).price(CTX, model="bs", n_paths=40_000, seed=4, batch=10_000)
    alone, _ = mc(sheet)
    assert book.positions[0].unit_price == pytest.approx(alone.price, abs=1e-10)
