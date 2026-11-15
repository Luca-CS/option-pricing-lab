"""The digitals of a compiled term sheet, found in its script, and the call spread that replaces each one in the price.

A digital pays a jump when the performance crosses a level, so its delta has no bound near that level. The desk hedges a call or put spread
instead, on the side against it, of width

    width = max(min spread, jump / delta max)

in points of performance, so that the delta of the spread stays under `delta max` (% of notional per 1 % of spot). The jump of each digital:

    coupon barrier     the coupon of the period (a memory's arrears are left out)
    autocall trigger   what the call pays on top of the redemption: the accumulated snowball, else one period's coupon. The continuation value
                       is taken at par: the real gap depends on the path, and this keeps a width known before any simulation
    final barrier      the capital lost at the barrier: put strike - barrier. No jump at a barrier on the strike
    upside digital     its amount

A barrier observed every day cannot be read as a call spread on one date: its level is moved by the `min spread` of the group instead, on the side
against the desk. The side of each digital is measured on the product, since on an autocall it depends on the product.
"""

from __future__ import annotations

from dataclasses import dataclass

from optlab.structured.script.evaluator import Ramp
from optlab.structured.script.nodes import Assign, Binary, Call, If, Name, Num, Window
from optlab.structured.script.timeline import Clock, build_timeline

KINDS = {
    "coupon": "Digital: coupon barrier",
    "autocall": "Digital: autocall trigger",
    "barrier": "Digital: final barrier",
    "upside": "Digital: upside",
    "daily": "Barrier observed daily",
}
DELTA_FIELD = {
    "coupon": "delta_max_coupon",
    "autocall": "delta_max_autocall",
    "barrier": "delta_max_barrier",
    "upside": "delta_max_upside",
}


@dataclass(frozen=True)
class Digital:
    kind: str
    line: int  # of the comparison in the script
    date: str
    level: float  # in performance
    jump: float  # fraction of notional, 0 for a daily barrier

    def width(self, min_spread: float, delta_max: dict) -> float:
        """In points of performance (fractions). A `delta_max` is a ratio: 1.0 is 1 % of notional per 1 % of spot."""
        if self.kind == "daily":
            return float(min_spread)
        return max(float(min_spread), abs(self.jump) / float(delta_max[self.kind]))

    def ramp(self, side: str, min_spread: float, delta_max: dict) -> Ramp:
        return Ramp(side, self.width(min_spread, delta_max) / self.level, self.kind == "daily")


def _value(node, params: dict) -> float | None:
    """A constant expression of the parameters, or None."""
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Name):
        return params.get(node.id)
    if isinstance(node, Binary) and node.op in ("+", "-", "*", "/"):
        a, b = _value(node.left, params), _value(node.right, params)
        if a is None or b is None:
            return None
        return {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else None}[node.op]
    return None


def _has_window(node) -> bool:
    if isinstance(node, Window):
        return True
    if isinstance(node, Call):
        return any(_has_window(a) for a in node.args)
    if isinstance(node, Binary):
        return _has_window(node.left) or _has_window(node.right)
    return False


def _names(node) -> set:
    if isinstance(node, Name):
        return {node.id}
    if isinstance(node, Binary):
        return _names(node.left) | _names(node.right)
    return set()


def _call_amount(body, params: dict) -> float:
    """What an autocall pays on top of its redemption, in fractions of the notional."""
    N = params["N"]
    return sum(_value(s.expr, params) or 0.0 for s in body if type(s).__name__ == "Pay" and s.tag == "coupon") / N


def _classify(cond, body, params: dict, sheet) -> tuple | None:
    """(kind, level, jump) of a comparison in an `if`, or None."""
    if not isinstance(cond, Binary) or cond.op not in (">=", ">", "<", "<="):
        return None
    names = _names(cond.right)
    cpn = params.get("cpn", 0.0)
    if "coupon_barrier" in names:
        return "coupon", _value(cond.right, params), cpn
    if "trigger" in names:
        return "autocall", _value(cond.right, params), _call_amount(body, params) or cpn
    if "call_strike" in names:
        return "upside", _value(cond.right, params), params.get("digital", 0.0)
    return None


def find_digitals(product, params: dict | None = None) -> list[Digital]:
    """The digitals still to be observed, from the script of a compiled term sheet, at the given parameters (the coupon when solved)."""
    program = product.program
    values = {**program.param_values, **(params or {})}
    timeline = build_timeline(program, Clock(product.valuation, product.calendar))
    out = []
    for ev, t in zip(program.events, timeline.event_times):
        if t <= 0:
            continue
        date = getattr(ev.when, "value", "")

        def visit(body):
            for s in body:
                if isinstance(s, If):
                    got = _classify(s.cond, s.body, values, product.sheet)
                    if got is not None and got[1]:
                        out.append(Digital(got[0], s.cond.line, str(date), float(got[1]), float(got[2])))  # noqa: B023 (called within this iteration only)
                    visit(s.body)
                    visit(s.orelse)
                elif isinstance(s, Assign) and s.name == "knocked_in" and isinstance(s.expr, Binary):
                    ki = values["ki_barrier"]
                    if _has_window(s.expr.left):
                        out.append(Digital("daily", s.expr.line, str(date), float(ki), 0.0))  # noqa: B023 (called within this iteration only)
                    else:
                        jump = values["put_strike"] - ki
                        if jump > 1e-12:
                            out.append(Digital("barrier", s.expr.line, str(date), float(ki), float(jump)))  # noqa: B023 (called within this iteration only)

        visit(ev.body)
    return out
