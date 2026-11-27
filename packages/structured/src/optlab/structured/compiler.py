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
    if sheet.mask.name == "callable":
        _issuer_calls(sheet, strike, months, conv, lag, cal, obs)
    maturity = cal.adjust(add_months(strike, months), conv)
    obs.setdefault(maturity, {"pay": cal.add_business_days(maturity, lag)})["final"] = True
    return dict(sorted(obs.items())), maturity


def _issuer_calls(sheet: TermSheet, strike, months: int, conv, lag: int, cal, obs: dict) -> None:
    """The issuer's call dates (every coupon date from the non-call period, the snowball's own frequency), each with
    its period, and its notice date `notice_days` business days before (the date the issuer decides on)."""
    snowball = sheet.get("coupon.type") == "snowball"
    freq = sheet.get("issuer_call.frequency_months" if snowball else "coupon.frequency_months")
    first = sheet.get("issuer_call.first_call_months") // freq
    notice = sheet.get("issuer_call.notice_days")
    for j, (o, p) in enumerate(schedule(strike, months, freq, conv, lag, cal)[first - 1 : -1]):
        obs.setdefault(o, {"pay": p})["issuer_call"] = j
        obs[o]["period"] = first + j
        if notice:
            obs.setdefault(cal.add_business_days(o, -notice), {"pay": None})["notice"] = True


def _params(sheet: TermSheet, coupon: str, upside: str, downside: str, autocall: bool) -> tuple[dict, float]:
    """The script's parameters from the term sheet, and the coupon's scale (the period in years)."""
    g = sheet.get
    params = {"N": g("general.notional")}
    coupon_scale = 0.0
    if coupon in ("fixed", "conditional"):
        coupon_scale = g("coupon.frequency_months") / 12
    elif coupon == "snowball":
        coupon_scale = (
            g("issuer_call.frequency_months" if sheet.mask.name == "callable" else "autocall.frequency_months") / 12
        )
    if coupon != "none":
        params["cpn"] = g("coupon.rate") * coupon_scale
    if coupon == "conditional":
        params["coupon_barrier"] = g("coupon.barrier")
    if autocall:
        params["trigger"] = g("autocall.trigger")
        if g("autocall.step_down"):
            params["step_down"] = g("autocall.step_down")
        if g("autocall.lizard"):
            params["lizard_barrier"] = g("autocall.lizard_barrier")
            params["lizard_coupon"] = g("autocall.lizard_coupon")
    if upside != "none":
        params["call_strike"] = g("upside.strike")
        if upside in ("call", "call_spread"):
            params["participation"] = g("upside.participation")
        if upside == "call_spread":
            params["cap"] = g("upside.cap")
        if upside == "digital":
            params["digital"] = g("upside.digital_amount")
    if sheet.mask.name == "callable":
        params["call_price"] = g("issuer_call.call_price")
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


CORRIDOR_UP = 1e9  # no ceiling: a level of the spot no path reaches


def _accumulator_lines(sheet: TermSheet, strike: dt.date, cal) -> tuple[list[str], dt.date]:
    """An accumulator (or decumulator): every business day after the strike the investor buys (sells) one unit at the
    strike, `gearing` units beyond it (below the strike for an accumulator, above for a decumulator), until the first
    close at or beyond the knock-out (not checked during the guaranteed period). The units of a period settle at its end:

        outright (swap)   accumulate: unit x units x (perf - K) / K      decumulate: unit x units x (K - perf)
        funded (note)     accumulate: unit x (units x perf / K + G days - units), the shares bought and the cash not spent
                          decumulate: unit x (units x K + (G days - units) x perf), the cash received and the shares kept

    with unit = N / (G n), n the days of the life, so that N is the most bought (sold) at the strike, every day geared:
    a funded note returns N when it buys nothing. A knock-out settles its period, then the rest at once (a funded note
    returns its budget, or its shares), and ends the product. The last flow is the redemption; a swap pays the notional
    there too, which its wrapper takes back. Interest on the cash a funded note has not spent is not counted."""
    g = sheet.get
    name = sheet.underlyings[0]
    conv, lag = g("general.convention"), g("general.pay_lag")
    acc, funded = g("accumulator.direction") == "accumulate", g("wrapper.format") == "note"
    periods = schedule(strike, g("general.maturity_months"), g("accumulator.settlement_months"), conv, lag, cal)
    maturity = periods[-1][0]
    days = cal.business_days(cal.add_business_days(strike, 1), maturity)
    guarantee = g("accumulator.guarantee_months")
    guard = cal.adjust(add_months(strike, guarantee), conv) if guarantee else None
    n = len(days)
    lines = [
        f"underlying {name}",
        f"param N = {number(g('general.notional'))}",
        f"param K = {number(g('accumulator.strike'))}",
        f"param KO = {number(g('accumulator.knock_out'))}",
        f"param G = {number(g('accumulator.gearing'))}",
        f"param unit = {number(g('general.notional') / (g('accumulator.gearing') * n))}",
        "",
        f"event {strike}",
        f"  S0 = {name}",
        "  units = 0",
        "  out = 0",
        "end",
    ]
    geared, knocked = ("perf < K", "perf >= KO") if acc else ("perf > K", "perf <= KO")
    add = f"units = units + 1 + (G - 1) * ({geared})"
    if acc:
        flow = "unit * (units * perf / K + G * {d} - units)" if funded else "unit * units * (perf - K) / K"
        rest = "unit * G * {r}"
    else:
        flow = "unit * (units * K + (G * {d} - units) * perf)" if funded else "unit * units * (K - perf)"
        rest = "unit * G * {r} * perf"
    i = 0
    for p, (o, pay) in enumerate(periods):
        period_days = [d for d in days[i:] if d <= o]
        i += len(period_days)
        remaining = n - i
        for d in period_days:
            body = ["perf = " + f"{name} / S0", "if out == 0 then"]
            if guard is not None and d <= guard:
                body.append(f"  {add}")
            else:
                body += [f"  if {knocked} then", "    out = 1", "  else", f"    {add}", "  end"]
            body.append("end")
            if d == o:
                amount = flow.format(d=len(period_days))
                if p == len(periods) - 1:
                    body.append(f"pay {amount}{'' if funded else ' + N'} as redemption")
                else:
                    body += [f"pay {amount} as upside", "units = 0", "if out == 1 then"]
                    body.append(f"  pay {rest.format(r=remaining) if funded else 'N'} as redemption")
                    body += ["  terminate", "end"]
                lines += ["", f"event {d} pay {pay}", *("  " + b for b in body), "end"]
            else:
                lines += ["", f"event {d}", *("  " + b for b in body), "end"]
    return lines, maturity


def _variance_lines(sheet: TermSheet, strike: dt.date, maturity: dt.date, pay: dt.date) -> tuple[list[str], str]:
    """A variance or volatility swap, plain or in a corridor: its script lines and the solved parameter. The notional
    N is a vega notional: a var swap pays N (sigma^2 - K_var) / (2 K) per vol point, K the strike entered (so that a
    move of one point at the strike pays about N), capped at (cap K)^2; a vol swap pays N (sigma - K_vol). The notional is
    paid as the redemption and taken back by the swap wrapper, so that a product pays its redemption once."""
    g = sheet.get
    name = sheet.underlyings[0]
    K = g("variance.strike")
    kind, corridor = g("variance.type"), g("variance.corridor")
    lines = [f"underlying {name}", f"param N = {number(g('general.notional'))}", f"param K = {number(K)}"]
    if kind == "variance":
        lines.append(f"param kvar = {number(K * K)}")
        if g("variance.cap"):
            lines.append(f"param cap = {number(g('variance.cap'))}")
    else:
        lines.append(f"param kvol = {number(K)}")
    lo = g("variance.lower") if corridor in ("up", "range") else 0.0
    hi = g("variance.upper") if corridor in ("down", "range") else CORRIDOR_UP
    if corridor != "none":
        lines += [f"param floor = {number(lo)}", f"param ceiling = {number(hi)}"]
    bounds = ", floor * S0, ceiling * S0" if corridor != "none" else ""
    lines += ["", f"event {strike}", f"  S0 = {name}", "end", "", f"event {maturity} pay {pay}"]
    lines.append(f"  v = variance({name}, {strike}, {maturity}{bounds})")
    if kind == "variance":
        realised = "min(v, cap * cap * K * K)" if g("variance.cap") else "v"
        lines.append(f"  pay N * 100 * ({realised} - kvar) / (2 * K) as upside")
    else:
        lines.append("  pay N * 100 * (sqrt(v) - kvol) as upside")
    lines.append("  pay N as redemption")  # the swap wrapper takes the notional back: the swap is worth its payoff
    lines.append("end")
    return lines, "kvar" if kind == "variance" else "kvol"


def _call_lines(call: str, coupon: str, period: int, asynchronous: bool, names) -> list[str]:
    """An autocall date: the worst name at or above its trigger, or (asynchronous) every name locked once it was."""
    if asynchronous:
        lines = [f"locked_{n} = max(locked_{n}, {n} / S0_{n} >= {call})" for n in names]
        lines.append(f"if worst({', '.join(f'locked_{n}' for n in names)}) >= 1 then")
    else:
        lines = [f"if perf >= {call} then"]
    if coupon == "snowball":
        lines.append(f"  pay N * cpn * {period} as coupon")
    return [*lines, "  pay N as redemption", "  terminate", "end"]


def _decision_lines(prefix: str, perf_expr: str, memory: bool, daily_ki: bool, names, strike, o) -> list[str]:
    """What the issuer decides on, at its notice date or the call date itself: the performance, the missed coupons'
    count (memory) and whether a daily knock-in barrier was breached so far."""
    lines = [f"{prefix}perf = {perf_expr}"] if prefix else []
    if daily_ki:
        if len(names) > 1:
            lows = ", ".join(f"lowest({n}, {strike}, {o}) / S0_{n}" for n in names)
            lines.append(f"{prefix}breached = worst({lows}) < ki_barrier")
        else:
            lines.append(f"{prefix}breached = lowest({names[0]}, {strike}, {o}) < ki_barrier * S0")
    return lines


def _issuer_parts(role, o, prefix, perf_expr, memory, daily_ki, names, strike, coupon) -> tuple[list, list]:
    """An event's lines for the issuer's call: what is decided on at a notice date (before the coupon), and at a call
    date the call itself after the coupon (with no notice, the state is read there first)."""
    before = _decision_lines(prefix, perf_expr, memory, daily_ki, names, strike, o) if role.get("notice") else []
    after = []
    if role.get("issuer_call") is not None:
        if not prefix:
            after += _decision_lines("", perf_expr, memory, daily_ki, names, strike, o)
        amount = f"N * (call_price + cpn * {role['period']})" if coupon == "snowball" else "N * call_price"
        after.append(f"call pay {amount}")
    return before, after


def _snowball_final(sheet: TermSheet, issuer: bool, n_periods, trigger) -> list[str]:
    """A snowball's coupon at maturity: every period's, on an issuer callable; above the last trigger on an autocall."""
    g = sheet.get
    if issuer:
        return [f"pay N * cpn * {g('general.maturity_months') // g('issuer_call.frequency_months')} as coupon"]
    j_final = n_periods - g("autocall.first_call")
    return [f"if perf >= {trigger(j_final)} then", f"  pay N * cpn * {n_periods} as coupon", "end"]


def _issuer_call_states(prefix: str, memory: bool, daily_ki: bool) -> list[str]:
    return [f"{prefix or ''}perf"] + (["missed"] if memory else []) + ([f"{prefix}breached"] if daily_ki else [])


def _lizard_lines(names, strike, o) -> list[str]:
    """The lizard date: redeemed at par with the bonus if no close of any name breached the lizard barrier since the
    strike (a path called on this date already pays nothing more)."""
    if len(names) > 1:
        lows = ", ".join(f"lowest({n}, {strike}, {o}) / S0_{n}" for n in names)
        cond = f"worst({lows}) >= lizard_barrier"
    else:
        cond = f"lowest({names[0]}, {strike}, {o}) >= lizard_barrier * S0"
    return [f"if {cond} then", "  pay N * lizard_coupon as coupon", "  pay N as redemption", "  terminate", "end"]


def compile_sheet(sheet: TermSheet, valuation, calendar=None) -> CompiledProduct:
    """The script of a term sheet. `calendar` overrides the sheet's general.calendar."""
    valuation = as_date(valuation)
    cal = calendars.calendar(calendar or sheet.get("general.calendar"))
    strike = sheet.get("general.strike_date") or valuation
    g = sheet.get
    if sheet.mask.name == "accumulator":
        lines, maturity = _accumulator_lines(sheet, strike, cal)
        script = "\n".join(lines) + "\n"
        program = validate(parse(script))
        return CompiledProduct(sheet, script, program, valuation, strike, maturity, None, 0.0, cal)
    if sheet.mask.name == "variance":
        maturity = cal.adjust(add_months(strike, g("general.maturity_months")), g("general.convention"))
        lines, param = _variance_lines(sheet, strike, maturity, cal.add_business_days(maturity, g("general.pay_lag")))
        script = "\n".join(lines) + "\n"
        program = validate(parse(script))
        return CompiledProduct(sheet, script, program, valuation, strike, maturity, param, 1.0, cal)
    coupon, upside, downside = g("coupon.type"), g("upside.type", "none"), g("downside.type")
    autocall = sheet.mask.name == "autocall"
    obs, maturity = _observations(sheet, strike, cal)
    n_periods = g("general.maturity_months") // g("autocall.frequency_months") if autocall else None

    params, coupon_scale = _params(sheet, coupon, upside, downside, autocall)
    memory = coupon == "conditional" and g("coupon.memory")

    def trigger(j: int) -> str:
        return f"trigger - {j} * step_down" if j and "step_down" in params else "trigger"

    names = sheet.underlyings
    first = names[0]
    basket = len(names) > 1
    lines = [f"underlying {n}" for n in names]
    lines += [f"param {k} = {number(v)}" for k, v in params.items()]
    lines += ["", f"event {strike}"]
    lines += [f"  S0_{n} = {n}" for n in names] if basket else [f"  S0 = {first}"]
    asynchronous = autocall and basket and g("autocall.mechanism") == "asynchronous"
    if asynchronous:
        lines += [f"  locked_{n} = 0" for n in names]
    if memory:
        lines.append("  missed = 0")
    lizard_date = add_months(strike, g("autocall.lizard_months")) if autocall and g("autocall.lizard") else None
    lines.append("end")
    perf_expr = f"worst({', '.join(f'{n} / S0_{n}' for n in names)})" if basket else f"{first} / S0"
    issuer = sheet.mask.name == "callable"
    daily_ki = issuer and downside == "pdi" and g("downside.observation") == "daily"
    prefix = "notice_" if issuer and g("issuer_call.notice_days") else ""
    if issuer:  # the declaration goes with the parameters, before the events
        lines.insert(len(names) + len(params), f"state {', '.join(_issuer_call_states(prefix, memory, daily_ki))}")
    for o, role in obs.items():
        final, call, icall = role.get("final", False), role.get("call"), role.get("issuer_call")
        needs_perf = coupon == "conditional" and role.get("coupon") or call is not None or final or icall is not None
        body = [f"perf = {perf_expr}"] if needs_perf else []
        before, after = _issuer_parts(role, o, prefix, perf_expr, memory, daily_ki, names, strike, coupon)
        body += before
        if role.get("coupon"):
            body += _coupon_lines(coupon, memory)
        body += after
        if call is not None:
            body += _call_lines(trigger(call), coupon, role["period"], asynchronous, names)
            if lizard_date is not None and o == cal.adjust(lizard_date, g("general.convention")):
                body += _lizard_lines(names, strike, o)
        if final:
            if coupon == "snowball":
                body += _snowball_final(sheet, issuer, n_periods, trigger)
            body += _upside_lines(upside)
            body += _redemption_lines(downside, g("downside.observation"), names, strike, o)
        pays = f" pay {role['pay']}" if role["pay"] is not None else ""
        lines += ["", f"event {o}{pays}", *("  " + b for b in body), "end"]
    script = "\n".join(lines) + "\n"
    program = validate(parse(script))
    return CompiledProduct(
        sheet, script, program, valuation, strike, maturity, "cpn" if coupon != "none" else None, coupon_scale, cal
    )
