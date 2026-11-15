"""Static checks of a parsed program, with the line of the first error."""

from __future__ import annotations

from typing import Any

from optlab.structured.script.lexer import KEYWORDS, ScriptError
from optlab.structured.script.nodes import (
    FUNCTIONS,
    TAGS,
    WINDOWS,
    Assign,
    Binary,
    Call,
    DateLit,
    If,
    Name,
    Num,
    Pay,
    Program,
    Terminate,
    Unary,
    Window,
)

MAX_UNDERLYINGS = 5  # baskets (worst-of) of up to five names


def _key(w) -> Any:
    return w.value  # ISO strings sort like dates, times are floats


def _check_name(name: str, line: int, what: str):
    if name in KEYWORDS or name in FUNCTIONS or name in WINDOWS:
        raise ScriptError(f"{what} name {name!r} is reserved", line)


class _Checker:
    def __init__(self, program: Program):
        self.program = program
        self.params = set()
        self.underlyings = set()

    def run(self):
        p = self.program
        if not p.underlyings:
            raise ScriptError("no underlying declared (add 'underlying NAME')", 1)
        for u in p.underlyings:
            _check_name(u.name, u.line, "underlying")
            if u.name in self.underlyings:
                raise ScriptError(f"underlying {u.name!r} declared twice", u.line, u.col)
            self.underlyings.add(u.name)
        if len(p.underlyings) > MAX_UNDERLYINGS:
            raise ScriptError(
                f"at most {MAX_UNDERLYINGS} underlyings are supported, found {len(p.underlyings)}",
                p.underlyings[MAX_UNDERLYINGS].line,
            )
        for prm in p.params:
            _check_name(prm.name, prm.line, "parameter")
            if prm.name in self.params or prm.name in self.underlyings:
                raise ScriptError(f"name {prm.name!r} declared twice", prm.line, prm.col)
            self.params.add(prm.name)
        if not p.events:
            raise ScriptError("the script has no event", 1)
        mode = type(p.events[0].when)
        defined: set[str] = set()
        previous = None
        for ev in p.events:
            for w in (ev.when, ev.pay):
                if w is not None and type(w) is not mode:
                    raise ScriptError("dates and times (t=...) cannot be mixed in one script", ev.line, ev.col)
            if previous is not None and _key(ev.when) <= _key(previous.when):
                raise ScriptError(
                    f"event dates must be strictly increasing ({ev.when.value} after {previous.when.value})",
                    ev.line,
                    ev.col,
                )
            if ev.pay is not None and _key(ev.pay) < _key(ev.when):
                raise ScriptError("the payment date is before the observation date", ev.line, ev.col)
            self.event = ev
            self.mode = mode
            defined = self.block(ev.body, defined)
            previous = ev

    def block(self, body, defined: set) -> set:
        defined = set(defined)
        for s in body:
            if isinstance(s, Assign):
                _check_name(s.name, s.line, "variable")
                if s.name in self.params or s.name in self.underlyings:
                    raise ScriptError(f"cannot assign to {s.name!r}, it is a parameter or an underlying", s.line, s.col)
                self.expr(s.expr, defined)
                defined.add(s.name)
            elif isinstance(s, Pay):
                if self.event.pay is None:
                    raise ScriptError(
                        "'pay' in an event without a payment date (write 'event DATE pay DATE')", s.line, s.col
                    )
                if s.tag not in TAGS:
                    raise ScriptError(f"unknown flow tag {s.tag!r}, expected one of {', '.join(TAGS)}", s.line, s.col)
                self.expr(s.expr, defined)
            elif isinstance(s, If):
                self.expr(s.cond, defined)
                # a variable is defined after the if only if both branches define it
                defined = self.block(s.body, defined) & self.block(s.orelse, defined)
            elif not isinstance(s, Terminate):
                raise ScriptError(f"unexpected statement {s!r}", getattr(s, "line", 0))
        return defined

    def expr(self, e, defined: set):
        if isinstance(e, Num):
            return
        if isinstance(e, Name):
            if e.id not in defined and e.id not in self.params and e.id not in self.underlyings:
                raise ScriptError(f"{e.id!r} is used before it is assigned", e.line, e.col)
        elif isinstance(e, Unary):
            self.expr(e.operand, defined)
        elif isinstance(e, Binary):
            self.expr(e.left, defined)
            self.expr(e.right, defined)
        elif isinstance(e, Call):
            for a in e.args:
                self.expr(a, defined)
        elif isinstance(e, Window):
            self.window(e)
        else:
            raise ScriptError(f"unexpected expression {e!r}", getattr(e, "line", 0))

    def window(self, w: Window):
        if w.underlying not in self.underlyings:
            raise ScriptError(f"{w.underlying!r} is not a declared underlying", w.line, w.col)
        for d in (w.start, w.end):
            if type(d) is not self.mode:
                raise ScriptError("dates and times (t=...) cannot be mixed in one script", w.line, w.col)
        if _key(w.end) <= _key(w.start):
            raise ScriptError(f"{w.func} window ends before it starts", w.line, w.col)
        if _key(w.end) > _key(self.event.when):
            raise ScriptError(
                f"{w.func} window ends after the event date (it would read future fixings)", w.line, w.col
            )
        if self.mode is DateLit and w.n is not None:
            raise ScriptError("n= is only for time windows; date windows use every business day", w.line, w.col)
        if self.mode is not DateLit and w.n is None:
            raise ScriptError("a time window needs the number of fixings, e.g. n=252", w.line, w.col)


def validate(program: Program) -> Program:
    """Raise ScriptError on the first problem; return the program unchanged otherwise."""
    _Checker(program).run()
    return program
