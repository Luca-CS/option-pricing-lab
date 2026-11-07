"""AST → script text. parse(to_text(program)) == program for every parsed program."""

from __future__ import annotations

from optlab.structured.script.nodes import (
    Assign,
    Binary,
    Call,
    DateLit,
    Event,
    If,
    Name,
    Num,
    Pay,
    Program,
    Terminate,
    Unary,
    Window,
)

PRECEDENCE = {"or": 1, "and": 2, "not": 3, "<": 4, "<=": 4, ">": 4, ">=": 4, "==": 4, "+": 5, "-": 5, "*": 6, "/": 6}
UNARY_MINUS = 7
INDENT = "  "


def number(v: float) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


def when(w) -> str:
    return w.value if isinstance(w, DateLit) else f"t={number(w.value)}"


def _prec(e) -> int:
    if isinstance(e, Binary):
        return PRECEDENCE[e.op]
    if isinstance(e, Unary):
        return PRECEDENCE["not"] if e.op == "not" else UNARY_MINUS
    return 99


def _wrap(e, needs: bool) -> str:
    s = expr(e)
    return f"({s})" if needs else s


def expr(e) -> str:
    if isinstance(e, Num):
        return number(e.value)
    if isinstance(e, Name):
        return e.id
    if isinstance(e, Unary):
        if e.op == "not":
            return "not " + _wrap(e.operand, _prec(e.operand) < PRECEDENCE["not"])
        return "-" + _wrap(e.operand, _prec(e.operand) < UNARY_MINUS)
    if isinstance(e, Binary):
        p = PRECEDENCE[e.op]
        comparison = p == PRECEDENCE["<"]
        left = _wrap(e.left, _prec(e.left) < p or (comparison and _prec(e.left) == p))
        right = _wrap(e.right, _prec(e.right) <= p)
        return f"{left} {e.op} {right}"
    if isinstance(e, Call):
        return f"{e.func}({', '.join(expr(a) for a in e.args)})"
    if isinstance(e, Window):
        n = f", n={e.n}" if e.n is not None else ""
        return f"{e.func}({e.underlying}, {when(e.start)}, {when(e.end)}{n})"
    raise TypeError(f"not an expression: {e!r}")


def statements(body, depth: int) -> list[str]:
    pad, lines = INDENT * depth, []
    for s in body:
        if isinstance(s, Assign):
            lines.append(f"{pad}{s.name} = {expr(s.expr)}")
        elif isinstance(s, Pay):
            lines.append(f"{pad}pay {expr(s.expr)} as {s.tag}")
        elif isinstance(s, Terminate):
            lines.append(f"{pad}terminate")
        elif isinstance(s, If):
            lines.append(f"{pad}if {expr(s.cond)} then")
            lines += statements(s.body, depth + 1)
            if s.orelse:
                lines.append(f"{pad}else")
                lines += statements(s.orelse, depth + 1)
            lines.append(f"{pad}end")
        else:
            raise TypeError(f"not a statement: {s!r}")
    return lines


def event(ev: Event) -> list[str]:
    pay = f" pay {when(ev.pay)}" if ev.pay is not None else ""
    return [f"event {when(ev.when)}{pay}", *statements(ev.body, 1), "end"]


def to_text(program: Program) -> str:
    lines = [f"underlying {u.name}" for u in program.underlyings]
    lines += [f"param {p.name} = {number(p.value)}" for p in program.params]
    for ev in program.events:
        lines += ["", *event(ev)]
    return "\n".join(lines) + "\n"
