"""Hand-written recursive-descent parser: script text → Program.

Grammar (one statement per line, blocks closed by `end`):
    program   := { underlying NAME | param NAME = [-]NUMBER } { event }
    event     := event WHEN [pay WHEN] NEWLINE { stmt } end
    WHEN      := DATE | t = NUMBER
    stmt      := NAME = expr | pay expr as TAG | terminate
               | if expr then NEWLINE { stmt } [else NEWLINE { stmt }] end
    expr      := and { or and } ;  and := not { and not } ;  not := not not | cmp
    cmp       := arith [ (< <= > >= ==) arith ] ;  arith := term { (+ -) term } ;  term := unary { (* /) unary }
    unary     := - unary | primary
    primary   := NUMBER | NAME | ( expr ) | FUNC ( expr {, expr} ) | lowest|highest ( NAME , WHEN , WHEN [, n = INT] )
    FUNC      := min | max (2 args) | abs | exp | log (1 arg) | worst | best (2 or more, e.g. worst-of baskets)
"""

from __future__ import annotations

from .lexer import ScriptError, Token, tokenize
from .nodes import (
    FUNCTIONS,
    WINDOWS,
    Assign,
    Binary,
    Call,
    DateLit,
    Event,
    If,
    Name,
    Num,
    Param,
    Pay,
    Program,
    Terminate,
    TimeLit,
    Unary,
    Underlying,
    Window,
)

COMPARISONS = ("<", "<=", ">", ">=", "==")


class Parser:
    def __init__(self, text: str):
        self.tokens = tokenize(text)
        self.i = 0

    # --- token helpers ---
    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def error(self, message: str, tok: Token | None = None):
        tok = tok or self.tok
        return ScriptError(message, tok.line, tok.col)

    def check(self, kind: str, text: str | None = None) -> bool:
        return self.tok.kind == kind and (text is None or self.tok.text == text)

    def accept(self, kind: str, text: str | None = None) -> Token | None:
        if self.check(kind, text):
            tok = self.tok
            self.i += 1
            return tok
        return None

    def expect(self, kind: str, text: str | None = None, what: str | None = None) -> Token:
        tok = self.accept(kind, text)
        if tok is None:
            found = {"NEWLINE": "end of line", "EOF": "end of script"}.get(self.tok.kind, repr(self.tok.text))
            raise self.error(f"expected {what or text or kind.lower()}, found {found}")
        return tok

    def skip_newlines(self):
        while self.accept("NEWLINE"):
            pass

    def end_of_line(self):
        if not (self.accept("NEWLINE") or self.check("EOF")):
            raise self.error(f"unexpected {self.tok.text!r} at end of statement")
        self.skip_newlines()

    # --- program ---
    def program(self) -> Program:
        underlyings, params, events = [], [], []
        self.skip_newlines()
        while not self.check("EOF"):
            if tok := self.accept("KEYWORD", "underlying"):
                underlyings.append(Underlying(self.expect("NAME", what="underlying name").text, tok.line, tok.col))
            elif tok := self.accept("KEYWORD", "param"):
                name = self.expect("NAME", what="parameter name").text
                self.expect("OP", "=")
                sign = -1.0 if self.accept("OP", "-") else 1.0
                params.append(Param(name, sign * float(self.expect("NUMBER", what="number").text), tok.line, tok.col))
            elif self.check("KEYWORD", "event"):
                events.append(self.event())
                continue
            else:
                raise self.error(f"expected underlying, param or event, found {self.tok.text!r}")
            self.end_of_line()
        return Program(tuple(underlyings), tuple(params), tuple(events))

    def when(self):
        tok = self.tok
        if self.accept("DATE"):
            return DateLit(tok.text, tok.line, tok.col)
        if self.check("NAME", "t"):
            self.i += 1
            self.expect("OP", "=")
            return TimeLit(float(self.expect("NUMBER", what="time in years").text), tok.line, tok.col)
        raise self.error(f"expected a date (YYYY-MM-DD) or t=<years>, found {tok.text!r}")

    def event(self) -> Event:
        tok = self.expect("KEYWORD", "event")
        when = self.when()
        pay = self.when() if self.accept("KEYWORD", "pay") else None
        self.end_of_line()
        body = self.block(("end",))
        self.expect("KEYWORD", "end", "'end' closing the event")
        self.end_of_line()
        return Event(when, pay, body, tok.line, tok.col)

    def block(self, closers) -> tuple:
        stmts = []
        while not any(self.check("KEYWORD", c) for c in closers):
            if self.check("EOF"):
                raise self.error("unexpected end of script, missing 'end'")
            stmts.append(self.statement())
        return tuple(stmts)

    # --- statements ---
    def statement(self):
        tok = self.tok
        if self.accept("KEYWORD", "if"):
            cond = self.expr()
            self.expect("KEYWORD", "then")
            self.end_of_line()
            body = self.block(("else", "end"))
            orelse = ()
            if self.accept("KEYWORD", "else"):
                self.end_of_line()
                orelse = self.block(("end",))
            self.expect("KEYWORD", "end", "'end' closing the if")
            self.end_of_line()
            return If(cond, body, orelse, tok.line, tok.col)
        if self.accept("KEYWORD", "pay"):
            expr = self.expr()
            self.expect("KEYWORD", "as")
            tag = self.expect("NAME", what="a flow tag").text
            self.end_of_line()
            return Pay(expr, tag, tok.line, tok.col)
        if self.accept("KEYWORD", "terminate"):
            self.end_of_line()
            return Terminate(tok.line, tok.col)
        if self.accept("NAME"):
            self.expect("OP", "=", "'=' (assignment)")
            expr = self.expr()
            self.end_of_line()
            return Assign(tok.text, expr, tok.line, tok.col)
        raise self.error(f"expected a statement, found {tok.text!r}")

    # --- expressions ---
    def expr(self):
        left = self.and_expr()
        while tok := self.accept("KEYWORD", "or"):
            left = Binary("or", left, self.and_expr(), tok.line, tok.col)
        return left

    def and_expr(self):
        left = self.not_expr()
        while tok := self.accept("KEYWORD", "and"):
            left = Binary("and", left, self.not_expr(), tok.line, tok.col)
        return left

    def not_expr(self):
        if tok := self.accept("KEYWORD", "not"):
            return Unary("not", self.not_expr(), tok.line, tok.col)
        return self.comparison()

    def comparison(self):
        left = self.arith()
        if self.tok.kind == "OP" and self.tok.text in COMPARISONS:
            tok = self.tok
            self.i += 1
            left = Binary(tok.text, left, self.arith(), tok.line, tok.col)
            if self.tok.kind == "OP" and self.tok.text in COMPARISONS:
                raise self.error("chained comparisons are not allowed, use 'and'")
        return left

    def arith(self):
        left = self.term()
        while self.tok.kind == "OP" and self.tok.text in "+-":
            tok = self.tok
            self.i += 1
            left = Binary(tok.text, left, self.term(), tok.line, tok.col)
        return left

    def term(self):
        left = self.unary()
        while self.tok.kind == "OP" and self.tok.text in "*/":
            tok = self.tok
            self.i += 1
            left = Binary(tok.text, left, self.unary(), tok.line, tok.col)
        return left

    def unary(self):
        if tok := self.accept("OP", "-"):
            return Unary("-", self.unary(), tok.line, tok.col)
        return self.primary()

    def primary(self):
        tok = self.tok
        if self.accept("NUMBER"):
            return Num(float(tok.text), tok.line, tok.col)
        if self.accept("OP", "("):
            e = self.expr()
            self.expect("OP", ")")
            return e
        if self.accept("NAME"):
            if not self.accept("OP", "("):
                return Name(tok.text, tok.line, tok.col)
            if tok.text in WINDOWS:
                return self.window(tok)
            if tok.text not in FUNCTIONS:
                raise self.error(f"unknown function {tok.text!r}", tok)
            args = [self.expr()]
            while self.accept("OP", ","):
                args.append(self.expr())
            self.expect("OP", ")")
            arity = FUNCTIONS[tok.text]
            if (arity is None and len(args) < 2) or (arity is not None and len(args) != arity):
                expected = "at least 2" if arity is None else arity
                raise self.error(f"{tok.text} takes {expected} argument(s), got {len(args)}", tok)
            return Call(tok.text, tuple(args), tok.line, tok.col)
        raise self.error(f"expected an expression, found {tok.text!r}" if tok.text else "expected an expression")

    def window(self, tok: Token):
        und = self.expect("NAME", what="underlying name").text
        self.expect("OP", ",")
        start = self.when()
        self.expect("OP", ",")
        end = self.when()
        n = None
        if self.accept("OP", ","):
            self.expect("NAME", "n")
            self.expect("OP", "=")
            num = self.expect("NUMBER", what="number of fixings")
            if not num.text.isdigit() or int(num.text) < 1:
                raise self.error("n must be a positive integer", num)
            n = int(num.text)
        self.expect("OP", ")")
        return Window(tok.text, und, start, end, n, tok.line, tok.col)


def parse(text: str) -> Program:
    """Parse a script. Raises ScriptError with the line and column of the first error."""
    return Parser(text).program()
