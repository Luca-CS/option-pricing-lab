"""Term sheet → script text. Each section contributes fragments to the events; the script is the product."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from optlab.structured import calendars
from optlab.structured.dates import add_months, as_date, schedule, year_fraction
from optlab.structured.schema import TermSheet
from optlab.structured.script.nodes import Program
from optlab.structured.script.parser import parse
from optlab.structured.script.printer import number
from optlab.structured.script.validate import validate


@dataclass
class CompiledProduct:
    sheet: TermSheet
    script: str
    program: Program
    valuation: dt.date
    strike_date: dt.date
    maturity: dt.date
    coupon_param: str | None  # script parameter holding the coupon per period
    coupon_scale: float  # coupon per period = rate per annum * coupon_scale
    calendar: object = None  # business-day calendar of the schedule and of the daily windows

    @property
    def horizon(self) -> float:
        return year_fraction(self.valuation, self.maturity)

    def coupon_rate(self, cpn: float) -> float:
        return cpn / self.coupon_scale


def _observations(sheet: TermSheet, strike: dt.date, cal):
    """Observation dates with their roles: coupon date, call index (0 for the first call date) and payment."""
    months, conv, lag = (
        sheet.get("general.maturity_months"),
        sheet.get("general.convention"),
        sheet.get("general.pay_lag"),
    )
    obs: dict = {}
    if sheet.get("coupon.type") in ("fixed", "conditional"):
        for o, p in schedule(strike, months, sheet.get("coupon.frequency_months"), conv, lag, cal):
            obs.setdefault(o, {"pay": p})["coupon"] = True
    if sheet.mask.name == "autocall":
        calls = schedule(strike, months, sheet.get("autocall.frequency_months"), conv, lag, cal)
        first = sheet.get("autocall.first_call")
        for j, (o, p) in enumerate(calls[first - 1 : -1]):
            obs.setdefault(o, {"pay": p})["call"] = j
            obs[o]["period"] = first + j
    maturity = cal.adjust(add_months(strike, months), conv)
    obs.setdefault(maturity, {"pay": cal.add_business_days(maturity, lag)})["final"] = True
    return dict(sorted(obs.items())), maturity


def _params(sheet: TermSheet, coupon: str, upside: str, downside: str, autocall: bool) -> tuple[dict, float]:
    """The script's parameters from the term sheet, and the coupon's scale (the period in years)."""
    g = sheet.get
    params = {"N": g("general.notional")}
    coupon_scale = 0.0
    if coupon in ("fixed", "conditional"):
        coupon_scale = g("coupon.frequency_months") / 12
    elif coupon == "snowball":
        coupon_scale = g("autocall.frequency_months") / 12
    if coupon != "none":
        params["cpn"] = g("coupon.rate") * coupon_scale
    if coupon == "conditional":
        params["coupon_barrier"] = g("coupon.barrier")
    if autocall:
        params["trigger"] = g("autocall.trigger")
        if g("autocall.step_down"):
            params["step_down"] = g("autocall.step_down")
    if upside != "none":
        params["call_strike"] = g("upside.strike")
        if upside in ("call", "call_spread"):
            params["participation"] = g("upside.participation")
        if upside == "call_spread":
            params["cap"] = g("upside.cap")
        if upside == "digital":
            params["digital"] = g("upside.digital_amount")
    if downside != "none":
        params["put_strike"] = g("downside.strike")
    if downside == "pdi":
        params["ki_barrier"] = g("downside.barrier")
    return params, coupon_scale


def _coupon_lines(coupon: str, memory: bool) -> list[str]:
    """The coupon of an observation date: fixed, conditional, or conditional with memory."""
    if coupon == "fixed":
        return ["pay N * cpn as coupon"]
    if memory:
        return [
            "if perf >= coupon_barrier then",
            "  pay N * cpn * (1 + missed) as coupon",
            "  missed = 0",
            "else",
            "  missed = missed + 1",
            "end",
        ]
    return ["if perf >= coupon_barrier then", "  pay N * cpn as coupon", "end"]


def _upside_lines(upside: str) -> list[str]:
    """The upside paid at maturity: a call, a call spread or a digital (none: nothing)."""
    if upside == "call":
        return ["pay N * participation * max(perf - call_strike, 0) as upside"]
    if upside == "call_spread":
        return ["pay N * participation * max(min(perf, cap) - call_strike, 0) as upside"]
    if upside == "digital":
        return ["if perf >= call_strike then", "  pay N * digital as upside", "end"]
    return []


def _redemption_lines(downside: str, observation, names, strike, o) -> list[str]:
    """The redemption at maturity: at par, a geared put, or a put knocked in at maturity or on a daily window."""
    if downside == "none":
        return ["pay N as redemption"]
    if downside == "geared_put":
        return ["pay N - N * max(put_strike - perf, 0) / put_strike as redemption"]
    if observation == "daily" and len(names) > 1:
        lows = ", ".join(f"lowest({n}, {strike}, {o}) / S0_{n}" for n in names)
        knock = f"knocked_in = worst({lows}) < ki_barrier"
    elif observation == "daily":
        knock = f"knocked_in = lowest({names[0]}, {strike}, {o}) < ki_barrier * S0"
    else:
        knock = "knocked_in = perf < ki_barrier"
    return [
        knock,
        "if knocked_in then",
        "  pay N - N * max(put_strike - perf, 0) as redemption",
        "else",
        "  pay N as redemption",
        "end",
    ]


def compile_sheet(sheet: TermSheet, valuation, calendar=None) -> CompiledProduct:
    """The script of a term sheet. `calendar` overrides the sheet's general.calendar."""
    valuation = as_date(valuation)
    cal = calendars.calendar(calendar or sheet.get("general.calendar"))
    strike = sheet.get("general.strike_date") or valuation
    g = sheet.get
    coupon, upside, downside = g("coupon.type"), g("upside.type", "none"), g("downside.type")
    autocall = sheet.mask.name == "autocall"
    obs, maturity = _observations(sheet, strike, cal)
    n_periods = g("general.maturity_months") // g("autocall.frequency_months") if autocall else None

    params, coupon_scale = _params(sheet, coupon, upside, downside, autocall)
    memory = coupon == "conditional" and g("coupon.memory")

    def trigger(j: int) -> str:
        return f"trigger - {j} * step_down" if j and "step_down" in params else "trigger"

    names = sheet.underlyings
    basket = len(names) > 1
    lines = [f"underlying {n}" for n in names]
    lines += [f"param {k} = {number(v)}" for k, v in params.items()]
    lines += ["", f"event {strike}"]
    lines += [f"  S0_{n} = {n}" for n in names] if basket else [f"  S0 = {names[0]}"]
    if memory:
        lines.append("  missed = 0")
    lines.append("end")
    perf_expr = f"worst({', '.join(f'{n} / S0_{n}' for n in names)})" if basket else f"{names[0]} / S0"
    for o, role in obs.items():
        final, call = role.get("final", False), role.get("call")
        needs_perf = coupon == "conditional" and role.get("coupon") or call is not None or final
        body = [f"perf = {perf_expr}"] if needs_perf else []
        if role.get("coupon"):
            body += _coupon_lines(coupon, memory)
        if call is not None:
            body.append(f"if perf >= {trigger(call)} then")
            if coupon == "snowball":
                body.append(f"  pay N * cpn * {role['period']} as coupon")
            body += ["  pay N as redemption", "  terminate", "end"]
        if final:
            if coupon == "snowball":
                j_final = n_periods - g("autocall.first_call")
                body += [f"if perf >= {trigger(j_final)} then", f"  pay N * cpn * {n_periods} as coupon", "end"]
            body += _upside_lines(upside)
            body += _redemption_lines(downside, g("downside.observation"), names, strike, o)
        lines += ["", f"event {o} pay {role['pay']}", *("  " + b for b in body), "end"]
    script = "\n".join(lines) + "\n"
    program = validate(parse(script))
    return CompiledProduct(
        sheet, script, program, valuation, strike, maturity, "cpn" if coupon != "none" else None, coupon_scale, cal
    )
