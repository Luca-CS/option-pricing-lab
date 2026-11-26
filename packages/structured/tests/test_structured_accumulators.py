"""Accumulators and decumulators, outright (a swap settled period by period) and fully funded (a note): their script
against closed forms when neither the knock-out nor the gearing can act, their lifecycle on fixings, their quotes."""

import datetime as dt

import numpy as np
import pytest
from _paths import SPX_CALIBRATION

from optlab.core import BlackScholesModel, Market
from optlab.structured import preset
from optlab.structured.calendars import calendar
from optlab.structured.cashflows import Note, Swap
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.lifecycle import events
from optlab.structured.pricer import price
from optlab.structured.schema import TermSheetError
from optlab.structured.script.timeline import Clock, build_timeline

START = dt.date(2024, 1, 2)
MARKET = Market.flat(100.0, 0.03, 0.01)
CTX = PricingContext.from_calibration(SPX_CALIBRATION)


def _sheet(name: str, **changes):
    return preset(name, **{"general.strike_date": START, "general.maturity_months": 3, **changes})


def _reference(product, funded: bool, acc: bool) -> float:
    """The value per 100 of notional when no knock-out and no gearing can act (G = 1, the knock-out out of reach):
    each day's unit settles at its period's end at the forward, discounted."""
    tl = build_timeline(product.program, Clock(START, product.calendar))
    p = product.program.param_values
    N, K, unit = p["N"], p["K"], p["unit"]
    total = 0.0

    days_in = 0
    for t_obs, t_pay in zip(tl.event_times[1:], tl.pay_times[1:]):
        days_in += 1
        if t_pay is None:
            continue
        fwd = float(MARKET.F(t_obs)) / MARKET.spot
        D = float(MARKET.D(t_pay))
        if funded:
            per = unit * days_in * (fwd / K if acc else K)  # every unit bought (sold): G days - units = 0 at G = 1
        else:
            per = unit * days_in * ((fwd - K) / K if acc else (K - fwd))
        total += D * per
        days_in = 0
    return 100 * total / N


@pytest.mark.parametrize("name", ["accumulator", "decumulator", "funded_accumulator", "funded_decumulator"])
def test_without_knock_out_or_gearing_the_price_is_the_forward_of_every_unit(name):
    acc = "decumulator" not in name
    funded = name.startswith("funded")
    sheet = _sheet(name, **{"accumulator.gearing": 1.0, "accumulator.knock_out": 3.0 if acc else 0.01})
    product = compile_sheet(sheet, START)
    fmt = Note() if funded else Swap()
    res = price(
        product.script, BlackScholesModel(0.2), MARKET, fmt, 20_000, 4, valuation=START, calendar=product.calendar
    ).value
    ref = _reference(product, funded, acc)
    assert abs(res.price - ref) <= 4 * res.stderr + 1e-9, f"{res.price} vs {ref}"


def test_the_sheet_keeps_its_knock_out_on_the_right_side_of_the_strike():
    with pytest.raises(TermSheetError, match="above its strike"):
        _sheet("accumulator", **{"accumulator.knock_out": 0.85})
    with pytest.raises(TermSheetError, match="below its strike"):
        _sheet("decumulator", **{"accumulator.knock_out": 1.2})
    with pytest.raises(TermSheetError, match="guaranteed period"):
        _sheet("accumulator", **{"accumulator.guarantee_months": 3})


def test_an_accumulator_settles_each_period_and_a_knock_out_ends_it():
    sheet = _sheet("accumulator", **{"accumulator.guarantee_months": 0})
    product = compile_sheet(sheet, START)
    days = calendar(sheet.get("general.calendar")).business_days(START, product.maturity)
    first_settle = next(dt.date.fromisoformat(str(e.when.value)) for e in product.program.events if e.pay is not None)
    p = product.program.param_values
    # every close at 95 % (between the strike 90 % and the knock-out 105 %): one unit a day, settled at 95 %
    calm = {"SPX": {d: 100.0 if d == START else 95.0 for d in days if d <= first_settle}}
    (settled,) = [e for e in events(sheet, calm, first_settle) if e.kind == "settled"]
    n_days = sum(1 for d in days if START < d <= first_settle)
    assert settled.amount == pytest.approx(p["unit"] * n_days * (0.95 - 0.9) / 0.9)
    # below the strike every day counts G units; a close at 106 % knocks out: the period settles, then it ends
    ko_day = days[5]
    path = {d: 100.0 if d == START else (106.0 if d == ko_day else 85.0) for d in days if d <= first_settle}
    out = events(sheet, {"SPX": path}, first_settle)
    (settled,) = [e for e in out if e.kind == "settled"]
    assert settled.amount == pytest.approx(p["unit"] * 2 * 4 * (0.85 - 0.9) / 0.9)  # four days geared before it
    assert [e.date for e in out if e.kind == "called"] == [first_settle]


def test_the_presets_quote_their_upfront_under_local_vol():
    from optlab.structured.quote import quote

    for name in ("accumulator", "funded_decumulator"):
        q = quote(preset(name, **{"general.maturity_months": 3}), CTX, control=None, n_paths=4_000)
        assert (
            q.coupon_rate is None
            and q.upfront is not None
            and np.isfinite(q.upfront)
            and q.decision.product_class == "callable"
        )
