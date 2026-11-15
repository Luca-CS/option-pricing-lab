"""Book of structured products on a shared simulation."""

import numpy as np
import pytest
from _paths import SPX_CALIBRATION
from _structured_refs import flat_context

from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.portfolio import Book, Position
from optlab.structured.presets import preset
from optlab.structured.pricer import price
from optlab.structured.quote import quote

BS_CTX = flat_context()
SPX = PricingContext.from_calibration(SPX_CALIBRATION)
N = 40_000

PUT = """underlying SPX
param N = 100
event 2026-10-27
  S0 = SPX
end
event 2027-10-27 pay 2027-11-03
  pay N * max(1 - SPX / S0, 0) as redemption
end
"""


def rc(**changes):
    return preset("reverse_convertible", **{"wrapper.solve": "upfront", "coupon.rate": 0.06, **changes})


def test_a_one_position_book_is_the_product_price_bit_for_bit():
    sheet = preset("phoenix", **{"wrapper.solve": "upfront", "general.maturity_months": 12})
    book = Book([Position("phoenix", sheet, 1.0)]).price(BS_CTX, model="bs", n_paths=N, seed=5, batch=10_000)
    p = compile_sheet(sheet, BS_CTX.valuation)
    alone = price(
        p.program, BS_CTX.model("bs"), BS_CTX.market, n_paths=N, seed=5, batch=10_000, valuation=BS_CTX.valuation
    )
    assert book.value == alone.value.price and book.stderr == alone.value.stderr
    assert book.positions[0].unit_price == pytest.approx(alone.price, rel=1e-14)


def test_long_and_short_the_same_product_is_exactly_flat():
    sheet = preset("phoenix", **{"wrapper.solve": "upfront", "general.maturity_months": 12})
    book = Book([Position("long", sheet, 3.0), Position("short", sheet, -3.0)]).price(
        BS_CTX, model="bs", n_paths=N, seed=5
    )
    assert book.value == 0.0 and book.stderr == 0.0
    assert book.positions[0].value == -book.positions[1].value


def test_shared_paths_give_the_hedged_book_a_small_error():
    # RC with a strike-100 European PDI (B = K) = bond + coupons - put: adding the put back leaves a bond
    book = Book([Position("rc", rc(**{"downside.barrier": 1.0}), 10.0), Position("put", PUT, 10.0)])
    res = book.price(BS_CTX, model="bs", n_paths=N, seed=7)
    legs = np.hypot(*(p.stderr for p in res.positions))
    assert res.stderr < 1e-10 * max(1.0, abs(res.value))  # deterministic book: coupons + notional
    assert legs > 0.1  # each leg alone carries the noise of the put
    assert res.value == pytest.approx(sum(p.value for p in res.positions), rel=1e-12)


def test_positions_on_different_dates_agree_with_standalone_prices():
    phoenix = preset("phoenix", **{"wrapper.solve": "upfront", "general.maturity_months": 12})
    brc = preset("barrier_reverse_convertible", **{"wrapper.solve": "upfront", "coupon.rate": 0.06})
    res = Book([Position("phoenix", phoenix, 2.0), Position("brc", brc, -1.0)]).price(
        BS_CTX, model="bs", n_paths=N, seed=9, batch=10_000
    )
    for pos, sheet in zip(res.positions, (phoenix, brc)):
        p = compile_sheet(sheet, BS_CTX.valuation)
        alone = price(
            p.program, BS_CTX.model("bs"), BS_CTX.market, n_paths=N, seed=19, batch=10_000, valuation=BS_CTX.valuation
        )
        assert abs(pos.unit_price - alone.price) <= 4 * np.hypot(pos.unit_stderr, alone.stderr)


def test_book_policy_follows_the_most_demanding_position():
    euro = Book([Position("rc", rc(), 1.0), Position("put", PUT, -1.0)])
    r = euro.price(BS_CTX)
    assert r.method == "replication" and r.stderr == 0.0
    mixed = Book([Position("rc", rc(), 1.0), Position("phoenix", preset("phoenix"), 1.0)])
    r = mixed.price(SPX, n_paths=20_000, batch=10_000)
    assert (r.method, r.model) == ("monte_carlo", "lv")
    assert "book: 2 positions" in r.table()


def test_replicated_book_equals_the_sum_of_quotes():
    sheets = {"rc": rc(**{"downside.barrier": 0.7}), "airbag": preset("airbag")}
    book = Book([Position(k, v, 2.0) for k, v in sheets.items()]).price(SPX)
    for pos, (_k, v) in zip(book.positions, sheets.items()):
        q = quote(v, SPX, control=None)
        assert pos.unit_price == pytest.approx(q.upfront, abs=1e-10)


def test_book_from_quotes_uses_the_solved_coupons():
    q = quote(preset("reverse_convertible"), BS_CTX, control=None)
    book = Book([Position("rc", q, 1.0)]).price(BS_CTX)
    assert book.positions[0].unit_price == pytest.approx(98.0, abs=1e-8)


def test_book_greeks_are_the_sum_of_position_greeks_and_match_replication():
    sheets = [("rc", rc(**{"downside.barrier": 0.7}), 1.0), ("put", PUT, -2.0)]
    book = Book([Position(*s) for s in sheets])
    rep = book.greeks(BS_CTX)  # all European: replication
    mc = book.greeks(BS_CTX, model="bs", n_paths=100_000, seed=3)
    for key in ("delta", "vega"):
        assert rep[key]["total"] == pytest.approx(sum(rep[key]["positions"]), rel=1e-12)
        assert mc[key]["total"] == pytest.approx(sum(mc[key]["positions"]), rel=1e-9)
        assert abs(mc[key]["total"] - rep[key]["total"]) <= 4 * mc[key]["total_se"] + 0.002
    assert rep["delta"]["positions"][1] > 0  # short puts gain when the spot rises
    assert rep["vega"]["positions"][1] < 0


def test_book_greeks_under_slv_have_no_vega():
    g = Book([Position("phoenix", preset("phoenix", **{"general.maturity_months": 12}), 1.0)]).greeks(
        SPX, model="slv", n_paths=20_000, batch=10_000
    )
    assert g["model"] == "slv" and g["vega"] is None and g["delta"]["total_se"] > 0


def test_book_slv_vega_by_recalibration_is_the_sum_of_positions():
    sheet = preset("phoenix", **{"general.maturity_months": 12})
    book = Book([Position("a", sheet, 1.0), Position("b", rc(), 2.0)])
    g = book.greeks(SPX, model="slv", n_paths=20_000, batch=10_000, slv_vega=True, n_particles=50_000)
    assert g["model"] == "slv" and g["vega"] is not None
    assert g["vega"]["total"] == pytest.approx(sum(g["vega"]["positions"]), rel=1e-9)
    assert g["vega"]["positions"][1] < 0  # the reverse convertible is short vol
