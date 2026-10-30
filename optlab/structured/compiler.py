"""Term sheet → script text. Each section contributes fragments to the events; the script is the product."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .dates import add_business_days, add_months, adjust, as_date, schedule, year_fraction
from .schema import TermSheet, TermSheetError
from .script.nodes import Program
from .script.parser import parse
from .script.printer import number
from .script.validate import validate


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

    @property
    def horizon(self) -> float:
        return year_fraction(self.valuation, self.maturity)

    def coupon_rate(self, cpn: float) -> float:
        return cpn / self.coupon_scale


def _observations(sheet: TermSheet, strike: dt.date):
    """Observation dates with their roles: coupon date, call index (0 for the first call date) and payment."""
    months, conv, lag = sheet.get("general.maturity_months"), sheet.get("general.convention"), sheet.get(
        "general.pay_lag")
    obs: dict = {}
    if sheet.get("coupon.type") in ("fixed", "conditional"):
        for o, p in schedule(strike, months, sheet.get("coupon.frequency_months"), conv, lag):
            obs.setdefault(o, {"pay": p})["coupon"] = True
    if sheet.mask.name == "autocall":
        calls = schedule(strike, months, sheet.get("autocall.frequency_months"), conv, lag)
        first = sheet.get("autocall.first_call")
        for j, (o, p) in enumerate(calls[first - 1:-1]):
            obs.setdefault(o, {"pay": p})["call"] = j
            obs[o]["period"] = first + j
    maturity = adjust(add_months(strike, months), conv)
    obs.setdefault(maturity, {"pay": add_business_days(maturity, lag)})["final"] = True
    return dict(sorted(obs.items())), maturity


def compile_sheet(sheet: TermSheet, valuation) -> CompiledProduct:
    valuation = as_date(valuation)
    strike = sheet.get("general.strike_date") or valuation
    if strike < valuation:
        raise TermSheetError("the strike date is before the valuation date: past fixings are not supported yet")
    g = sheet.get
    coupon, upside, downside = g("coupon.type"), g("upside.type", "none"), g("downside.type")
    autocall = sheet.mask.name == "autocall"
    obs, maturity = _observations(sheet, strike)
    n_periods = g("general.maturity_months") // g("autocall.frequency_months") if autocall else None

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
    memory = coupon == "conditional" and g("coupon.memory")

    def trigger(j: int) -> str:
        return f"trigger - {j} * step_down" if j and "step_down" in params else "trigger"

    lines = [f"underlying {g('general.underlying')}"]
    lines += [f"param {k} = {number(v)}" for k, v in params.items()]
    lines += ["", f"event {strike}", f"  S0 = {g('general.underlying')}"]
    if memory:
        lines.append("  missed = 0")
    lines.append("end")
    und = g("general.underlying")
    for o, role in obs.items():
        final, call = role.get("final", False), role.get("call")
        needs_perf = coupon == "conditional" and role.get("coupon") or call is not None or final
        body = [f"perf = {und} / S0"] if needs_perf else []
        if role.get("coupon"):
            if coupon == "fixed":
                body.append("pay N * cpn as coupon")
            elif memory:
                body += ["if perf >= coupon_barrier then", "  pay N * cpn * (1 + missed) as coupon",
                         "  missed = 0", "else", "  missed = missed + 1", "end"]
            else:
                body += ["if perf >= coupon_barrier then", "  pay N * cpn as coupon", "end"]
        if call is not None:
            body.append(f"if perf >= {trigger(call)} then")
            if coupon == "snowball":
                body.append(f"  pay N * cpn * {role['period']} as coupon")
            body += ["  pay N as redemption", "  terminate", "end"]
        if final:
            if coupon == "snowball":
                j_final = n_periods - g("autocall.first_call")
                body += [f"if perf >= {trigger(j_final)} then", f"  pay N * cpn * {n_periods} as coupon", "end"]
            if upside == "call":
                body.append("pay N * participation * max(perf - call_strike, 0) as upside")
            elif upside == "call_spread":
                body.append("pay N * participation * max(min(perf, cap) - call_strike, 0) as upside")
            elif upside == "digital":
                body += ["if perf >= call_strike then", "  pay N * digital as upside", "end"]
            if downside == "none":
                body.append("pay N as redemption")
            elif downside == "geared_put":
                body.append("pay N - N * max(put_strike - perf, 0) / put_strike as redemption")
            else:
                if g("downside.observation") == "daily":
                    body.append(f"knocked_in = lowest({und}, {strike}, {o}) < ki_barrier * S0")
                else:
                    body.append("knocked_in = perf < ki_barrier")
                body += ["if knocked_in then", "  pay N - N * max(put_strike - perf, 0) as redemption", "else",
                         "  pay N as redemption", "end"]
        lines += ["", f"event {o} pay {role['pay']}", *("  " + b for b in body), "end"]
    script = "\n".join(lines) + "\n"
    program = validate(parse(script))
    return CompiledProduct(sheet, script, program, valuation, strike, maturity,
                           "cpn" if coupon != "none" else None, coupon_scale)
