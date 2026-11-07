"""Public API hygiene: units, bounds, messages and Greeks of every kind of quote."""

import datetime as dt

import numpy as np
import pytest
from _structured_refs import flat_context
from optlab.structured import TermSheetError, preset, quote
from optlab.structured.dates import business_days
from optlab.structured.multiasset import BasketContext
from optlab.structured.portfolio import Book, Position
from optlab.structured.script.lexer import ScriptError
from optlab.structured.script.parser import parse
from optlab.structured.script.validate import validate
from optlab.structured.units import UNITS

CTX = flat_context()


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"coupon.rate": 8}, "fractions: 0.6 = 60 %, 0.08 = 8 % p.a."),
        ({"downside.barrier": 60}, "above the maximum 3.0"),
        ({"upside.participation": 50}, "above the maximum 10.0"),
    ],
)
def test_levels_and_rates_are_fractions(changes, message):
    with pytest.raises(TermSheetError, match=message.replace(".", r"\.").replace("(", r"\(")):
        preset(
            "phoenix" if "coupon.rate" in changes or "downside.barrier" in changes else "capital_protected", **changes
        )


def test_a_field_path_needs_a_section():
    with pytest.raises(TermSheetError, match="use 'section.field', e.g. 'coupon.rate'"):
        preset("phoenix").get("rate")


def test_model_names_are_case_insensitive():
    q = quote(
        preset("phoenix", **{"general.maturity_months": 12}), CTX, model="BS", control=None, n_paths=4_000, batch=None
    )
    assert q.decision.model == "bs" and q.decision.forced


def test_tables_name_every_underlying_and_the_implied_margin():
    basket = BasketContext(
        ("SPX", "SX5E"),
        {"SPX": CTX.market, "SX5E": CTX.market},
        {"SPX": 0.2, "SX5E": 0.25},
        np.array([[1, 0.5], [0.5, 1]]),
        CTX.valuation,
    )
    q = quote(
        preset("phoenix", **{"general.basket": "worst_of", "general.maturity_months": 12}),
        basket,
        control=None,
        n_paths=4_000,
        batch=None,
    )
    assert q.table().splitlines()[0].startswith("Autocallable on SPX, SX5E")
    cpn = quote(preset("capital_protected"), CTX, control=None)
    assert f"implied margin at par {100 - cpn.upfront:.2f} %" in cpn.table()


def test_validation_errors_carry_the_column():
    with pytest.raises(ScriptError) as err:
        validate(parse("underlying SPX\nevent t=1 pay t=1\n  pay x as redemption\nend\n"))
    assert (err.value.line, err.value.col) == (3, 7)


def test_quote_greeks_of_a_running_product_go_through_a_book():
    strike = dt.date(2026, 4, 27)
    sheet = preset("barrier_reverse_convertible", **{"general.strike_date": strike, "wrapper.solve": "upfront"})
    fixings = {"SPX": {d: 100.0 for d in business_days(strike, CTX.valuation)}}
    q = quote(sheet, CTX, model="bs", control=None, n_paths=20_000, batch=10_000, fixings=fixings, with_greeks=True)
    g = Book([Position("b", sheet, 1.0, fixings=fixings)]).greeks(CTX, model="bs", n_paths=20_000, batch=10_000)
    assert q.greeks.delta == pytest.approx(g["delta"]["positions"][0], rel=1e-12)  # notional 100: % = currency
    assert q.greeks.delta > 0 and q.greeks.units == {"delta": UNITS["delta"], "vega": UNITS["vega"]}


def test_quote_greeks_of_a_basket_are_summed_over_the_names():
    basket = BasketContext(
        ("SPX", "SX5E"),
        {"SPX": CTX.market, "SX5E": CTX.market},
        {"SPX": 0.2, "SX5E": 0.25},
        np.array([[1, 0.5], [0.5, 1]]),
        CTX.valuation,
    )
    q = quote(
        preset("reverse_convertible", **{"general.basket": "worst_of", "wrapper.solve": "upfront"}),
        basket,
        control=None,
        n_paths=20_000,
        batch=10_000,
        with_greeks=True,
    )
    per = q.greeks.per_name
    assert set(per) == {"SPX", "SX5E"} and all(v["delta"] > 0 for v in per.values())
    assert q.greeks.delta == pytest.approx(sum(v["delta"] for v in per.values()), rel=1e-12)


def test_book_greeks_carry_their_units():
    g = Book([Position("rc", preset("reverse_convertible", **{"wrapper.solve": "upfront"}), 1.0)]).greeks(CTX)
    assert g["units"]["delta"] == UNITS["book_delta"]
