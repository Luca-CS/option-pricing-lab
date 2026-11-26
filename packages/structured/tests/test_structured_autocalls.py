"""Asynchronous and lizard autocalls: each name locked once it reached its trigger; an early redemption with a bonus at the
lizard date when no close breached the lizard barrier. Read on fixings (the lifecycle) and priced by Monte Carlo."""

import datetime as dt

import numpy as np
import pytest
from _paths import SPX_CALIBRATION

from optlab.core import Market
from optlab.structured import preset
from optlab.structured.calendars import calendar
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.defaults import resolve
from optlab.structured.lifecycle import events
from optlab.structured.multiasset import BasketContext
from optlab.structured.quote import quote
from optlab.structured.schema import TermSheetError

START = dt.date(2024, 1, 2)
CTX = PricingContext.from_calibration(SPX_CALIBRATION)


def _sheet(name: str, **changes):
    return preset(name, **{"general.strike_date": START, **changes})


def _daily(product, levels_at, upto) -> dict:
    """Every business day's close from strike to `upto`, levels_at(day) -> level."""
    days = product.calendar.business_days(START, upto)
    return {d: float(levels_at(d)) for d in days}


def test_the_schema_places_the_lizard_on_a_call_date_and_the_asynchronous_call_on_a_basket():
    with pytest.raises(TermSheetError, match="must be a call date"):
        _sheet("lizard_phoenix", **{"autocall.lizard_months": 13})
    with pytest.raises(TermSheetError, match="must be a call date"):
        _sheet("lizard_phoenix", **{"autocall.lizard_months": 24})  # the maturity is not an early redemption
    with pytest.raises(TermSheetError, match="not used"):
        preset("phoenix", **{"autocall.mechanism": "asynchronous"})  # one name has nothing to lock apart
    assert _sheet("asynchronous_autocall").get("autocall.mechanism") == "asynchronous"


def test_an_asynchronous_autocall_calls_once_each_name_has_been_at_its_trigger():
    sheet = _sheet("asynchronous_autocall")
    standard = sheet.replace(**{"autocall.mechanism": "standard"})
    product = compile_sheet(sheet, START)
    q1, q2 = (o for o, role in sorted(_observations(product).items())[:2])

    def path(up_first):
        def level(d):
            if d < q1:
                return 100.0
            first = d < q2
            return 105.0 if first == up_first else 95.0

        return level

    fixings = {"SPX": _daily(product, path(True), q2), "NDX": _daily(product, path(False), q2)}
    called = [e for e in events(sheet, fixings, q2) if e.kind == "called"]
    assert [e.date for e in called] == [q2]  # SPX above at q1, NDX at q2: both locked at q2
    assert not [e for e in events(standard, fixings, q2) if e.kind == "called"]  # the worst never was
    (coupon,) = [e for e in events(sheet, fixings, q2) if e.kind == "coupon_paid"]
    assert coupon.amount == pytest.approx(100 * sheet.get("coupon.rate") * 0.5)  # the snowball of two quarters


def _observations(product):
    out = {}
    for ev in product.program.events:
        if ev.pay is not None:
            out[dt.date.fromisoformat(str(ev.when.value))] = True
    return out


def test_a_lizard_redeems_with_its_bonus_when_no_close_breached_its_barrier():
    sheet = _sheet("lizard_phoenix")
    product = compile_sheet(sheet, START)
    lizard = calendar("weekend").adjust(dt.date(2025, 1, 2), "modified_following")
    calm = {
        "SPX": _daily(product, lambda d: 100.0 if d == START else 90.0, lizard)
    }  # below the trigger, above the lizard barrier: redeemed
    kinds = {(e.date, e.kind): e.amount for e in events(sheet, calm, lizard)}
    assert (lizard, "called") in kinds and kinds[(lizard, "called")] == pytest.approx(100.0)
    cpn = 100 * sheet.get("coupon.rate") * 0.25
    assert kinds[(lizard, "coupon_paid")] == pytest.approx(cpn + 100 * sheet.get("autocall.lizard_coupon"))
    dipped = {
        "SPX": _daily(product, lambda d: 100.0 if d == START else 74.0 if d == dt.date(2024, 6, 3) else 90.0, lizard)
    }
    assert not [e for e in events(sheet, dipped, lizard) if e.kind == "called"]  # one close below 75 %: no lizard


def test_the_mechanisms_only_add_early_ends_and_the_lizard_is_overhedged_as_a_daily_barrier():
    lizard = preset("lizard_phoenix", **{"wrapper.solve": "upfront"})
    plain = preset("phoenix", **{"wrapper.solve": "upfront"})
    a = quote(lizard, CTX, control=None, n_paths=8_000)
    b = quote(plain, CTX, control=None, n_paths=8_000)
    assert a.result.expected_life < b.result.expected_life  # the same paths, one more way to end
    costed = quote(lizard, CTX, control=None, n_paths=8_000, costs=resolve(None, lizard).inputs())
    assert costed.desk is not None and any(line.key == "lizard" for line in costed.desk.lines)
    markets = {"SPX": Market.flat(100.0, 0.02, 0.01), "NDX": Market.flat(100.0, 0.02, 0.005)}
    basket = BasketContext(("SPX", "NDX"), markets, {"SPX": 0.2, "NDX": 0.25}, np.array([[1, 0.8], [0.8, 1]]), START)
    asynchronous = preset("asynchronous_autocall", **{"wrapper.solve": "upfront"})
    standard = asynchronous.replace(**{"autocall.mechanism": "standard"})
    qa = quote(asynchronous, basket, model="bs", control=None, n_paths=8_000)
    qs = quote(standard, basket, model="bs", control=None, n_paths=8_000)
    assert qa.result.expected_life < qs.result.expected_life  # a lock never comes later than the worst's call
