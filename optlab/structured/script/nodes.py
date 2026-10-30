"""AST of the payoff language. Line numbers are kept for messages but ignored by equality."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    pass


# --- observation dates: a calendar date or a time in years ---

@dataclass(frozen=True)
class DateLit(Node):
    value: str  # ISO date, YYYY-MM-DD
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class TimeLit(Node):
    value: float
    line: int = field(default=0, compare=False)


When = DateLit | TimeLit


# --- expressions ---

@dataclass(frozen=True)
class Num(Node):
    value: float
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Name(Node):
    id: str
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Unary(Node):
    op: str  # "-" or "not"
    operand: Node
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Binary(Node):
    op: str  # + - * / < <= > >= == and or
    left: Node
    right: Node
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Call(Node):
    func: str  # min, max, abs, exp, log
    args: tuple
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Window(Node):
    """Lowest or highest fixing of an underlying between two dates (both business days included), or on n
    equally spaced times in (start, end] in time mode."""
    func: str  # lowest or highest
    underlying: str
    start: When
    end: When
    n: int | None = None
    line: int = field(default=0, compare=False)


# --- statements ---

@dataclass(frozen=True)
class Assign(Node):
    name: str
    expr: Node
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class If(Node):
    cond: Node
    body: tuple
    orelse: tuple = ()
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Pay(Node):
    expr: Node
    tag: str  # coupon, upside or redemption
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Terminate(Node):
    line: int = field(default=0, compare=False)


# --- program ---

@dataclass(frozen=True)
class Event(Node):
    when: When
    pay: When | None
    body: tuple
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Param(Node):
    name: str
    value: float
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Underlying(Node):
    name: str
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Program(Node):
    underlyings: tuple
    params: tuple
    events: tuple

    @property
    def param_values(self) -> dict:
        return {p.name: p.value for p in self.params}

    @property
    def underlying_names(self) -> tuple:
        return tuple(u.name for u in self.underlyings)


TAGS = ("coupon", "upside", "redemption")
FUNCTIONS = {"min": 2, "max": 2, "abs": 1, "exp": 1, "log": 1}
WINDOWS = ("lowest", "highest")
