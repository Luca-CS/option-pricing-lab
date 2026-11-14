"""Lifecycle of a running product: what already happened (coupons paid or missed, call, knock-in, maturity),
read from its own script evaluated on the fixings, so that the events follow exactly the priced payoff."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from optlab.structured.compiler import compile_sheet
from optlab.structured.dates import as_date
from optlab.structured.multiasset import BasketScenario
from optlab.structured.pricer import basket_history, history_times
from optlab.structured.scenario import Scenario
from optlab.structured.schema import TermSheet
from optlab.structured.script.evaluator import Evaluator
from optlab.structured.script.lexer import ScriptError
from optlab.structured.script.nodes import Program
from optlab.structured.script.timeline import Clock, build_timeline

KINDS = ("coupon_paid", "coupon_missed", "called", "knocked_in", "matured")


@dataclass(frozen=True)
class Event:
    trade_id: str
    date: dt.date  # observation date (for a daily knock-in: the first close below the barrier)
    kind: str
    amount: float | None = None  # in currency, for the trade's notional; paid on `payment`
    payment: dt.date | None = None


def events(product, fixings: dict, upto, calendar=None, trade_id: str = "") -> list[Event]:
    """Events of a term sheet (or Trade) with observation dates up to `upto`, from fixings in the model's scale
    ({underlying: {date: level}}). Every observation up to `upto` needs its fixing."""
    if hasattr(product, "term_sheet") and callable(product.term_sheet):
        trade_id = trade_id or product.trade_id
        product = product.term_sheet()
    if not isinstance(product, TermSheet):
        raise TypeError("events need a TermSheet or a term-sheet Trade")
    upto = as_date(upto)
    compiled = compile_sheet(product, upto, calendar)
    program = compiled.program
    kept = [ev for ev in program.events if as_date(ev.when.value) <= upto]
    past = Program(program.underlyings, program.params, tuple(kept))
    clock = Clock(upto, compiled.calendar)
    timeline = build_timeline(past, clock)
    names = program.underlying_names
    if len(names) == 1:
        history = history_times(fixings, names[0], clock)
        scenario = _Lenient(names[0], [], np.empty((2, 0)), np.nan, history)
    else:
        history = basket_history(fixings, names, clock)
        scenario = _LenientBasket([], {n: np.empty((2, 0)) for n in names}, {n: np.nan for n in names}, history)
    try:
        out = Evaluator(past, timeline, check_redemption=False).run(scenario)
    except ScriptError:
        if scenario.missing:
            days = sorted({(upto + dt.timedelta(days=round(t * 365))).isoformat() for _, t in scenario.missing})
            raise KeyError(f"no fixing for the observations of {', '.join(days)}") from None
        raise
    result = []
    final = len(kept) == len(program.events)
    for k, ev in enumerate(kept[1:], start=1):
        day, t_pay = as_date(ev.when.value), timeline.pay_times[k]
        pay_day = as_date(ev.pay.value) if ev.pay is not None else None
        coupon = out.flows.get((t_pay, "coupon"))
        if coupon is not None and np.ndim(coupon) and _alive_before(out, k):
            amount = float(coupon[0])
            result.append(Event(trade_id, day, "coupon_paid" if amount > 0 else "coupon_missed", amount, pay_day))
        if float(np.asarray(out.terminated[k]).reshape(-1)[0]) > 0:
            red = out.flows.get((t_pay, "redemption"))
            result.append(Event(trade_id, day, "called", float(red[0]) if red is not None else None, pay_day))
        if final and k == len(kept) - 1:
            red = out.flows.get((t_pay, "redemption"))
            if red is not None and _alive_before(out, k):
                result.append(Event(trade_id, day, "matured", float(red[0]), pay_day))
    ki = _knock_in(product, fixings, upto, compiled, final)
    if ki is not None:
        result.append(Event(trade_id, ki, "knocked_in"))
    return sorted(result, key=lambda e: (e.date, KINDS.index(e.kind)))


class _MissingAsNaN:
    """Past fixings that are not given read as NaN (recorded): after a call the product observes nothing, and the
    evaluator only objects when a missing fixing matters."""

    missing: list

    def _past(self, name, t):
        try:
            return super()._past(name, t) if isinstance(self, BasketScenario) else super()._past(t)  # pyright: ignore[reportAttributeAccessIssue] (a mixin: Scenario or BasketScenario comes next)
        except KeyError:
            self.missing.append((name, t))
            return np.nan


class _Lenient(_MissingAsNaN, Scenario):
    def __init__(self, *args):
        super().__init__(*args)
        self.missing = []

    def _past(self, t):  # pyright: ignore[reportIncompatibleMethodOverride] (Scenario's signature, which the evaluator calls)
        return _MissingAsNaN._past(self, self.name, t)


class _LenientBasket(_MissingAsNaN, BasketScenario):
    def __init__(self, *args):
        super().__init__(*args)
        self.missing = []


def _alive_before(out, k: int) -> bool:
    lost = sum(float(np.asarray(d).reshape(-1)[0]) for d in out.terminated[:k])
    return lost < 1.0


def _knock_in(sheet: TermSheet, fixings: dict, upto: dt.date, compiled, final: bool) -> dt.date | None:
    """First date the knock-in condition held: any close of the worst name below the barrier for a daily
    barrier, the final fixing for a European one."""
    if sheet.get("downside.type") != "pdi":
        return None
    barrier, strike = sheet.get("downside.barrier"), compiled.strike_date
    names = sheet.underlyings
    initial = {n: fixings[n][_key(fixings[n], strike)] for n in names}
    if sheet.get("downside.observation") == "daily":
        for day in compiled.calendar.business_days(strike, min(upto, compiled.maturity)):
            perf = min(fixings[n][_key(fixings[n], day)] / initial[n] for n in names)
            if perf < barrier:
                return day
        return None
    if final:
        perf = min(fixings[n][_key(fixings[n], compiled.maturity)] / initial[n] for n in names)
        return compiled.maturity if perf < barrier else None
    return None


def _key(levels: dict, day: dt.date):
    for k in (day, day.isoformat()):
        if k in levels:
            return k
    raise KeyError(f"no fixing on {day}")
