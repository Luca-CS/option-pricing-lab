"""Vectorised evaluation of a program on one batch of paths.

Every variable is an array over paths. Conditions are float masks (0/1, or a ramp in smoothed mode): inside an
`if`, `x = e` becomes `x = m*e + (1-m)*x`, `pay e` adds `m*alive*e` to the flows and `terminate` multiplies
`alive` by `1-m`.

Undefined values are errors, not silent branches: a comparison with NaN (log of a negative number, 0/0) stays NaN,
`and`/`or` short-circuit on a certain operand, and a NaN condition or a non-finite payment on a path that the
current mask reaches (and that has not terminated) raises a ScriptError with the line. Values that are masked out
may be NaN.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

import numpy as np

from .lexer import ScriptError
from .nodes import Assign, Binary, Call, If, Name, Num, Pay, Program, Terminate, Unary, Window
from .timeline import Timeline

FUNCS = {"min": np.minimum, "max": np.maximum, "abs": np.abs, "exp": np.exp, "log": np.log}
ARITH = {"+": np.add, "-": np.subtract, "*": np.multiply, "/": np.divide}
HARD = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal, "==": np.equal}


@dataclass
class BatchFlows:
    """Undiscounted flows of one batch, keyed by (payment time, tag), plus redemption weights per payment time
    (the probability-like weight with which the redemption is paid there) and the alive mass lost per event."""

    n_paths: int
    flows: dict = field(default_factory=dict)
    redemption_weights: dict = field(default_factory=dict)
    terminated: list = field(default_factory=list)
    alive: np.ndarray | float = 1.0
    warnings: list = field(default_factory=list)


@dataclass(frozen=True)
class Ramp:
    """A comparison a >= b or a < b read as a call or put spread on one side of b: over [b, b (1 + width)] on side "up", over [b (1 - width), b] on
    side "down". With `shift`, the level moves to the far end of that interval and the comparison stays hard."""

    side: str  # up or down
    width: float  # relative to |b|
    shift: bool = False


def _ramped(op, a, b, ramp: Ramp):
    w = ramp.width * np.abs(b)
    lo = np.where(np.asarray(ramp.side == "up"), b, np.subtract(b, w))
    if ramp.shift:
        level = np.add(b, w) if ramp.side == "up" else np.subtract(b, w)
        out = (
            HARD[op](a, level).astype(float)
            if isinstance(a, np.ndarray) or isinstance(level, np.ndarray)
            else float(HARD[op](a, level))
        )
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            position = np.clip((np.subtract(a, lo)) / w, 0.0, 1.0)
        out = position if op in (">", ">=") else 1.0 - position
        out = np.where(w > 0, out, HARD[op](a, b).astype(float))
    if _any_nan(a, b):
        out = np.where(np.isnan(a) | np.isnan(b), np.nan, out)
    return out


def _compare(op, a, b, eps):
    hard = (
        HARD[op](a, b).astype(float)
        if isinstance(a, np.ndarray) or isinstance(b, np.ndarray)
        else float(HARD[op](a, b))
    )
    if _any_nan(a, b):  # NaN compares as False: keep it undefined instead of sending the path to `else`
        hard = np.where(np.isnan(a) | np.isnan(b), np.nan, hard)
    if eps == 0 or op == "==":
        return hard
    scale = eps * np.abs(b)
    d = np.subtract(a, b) if op in (">", ">=") else np.subtract(b, a)
    with np.errstate(divide="ignore", invalid="ignore"):
        ramp = np.clip(d / scale + 0.5, 0.0, 1.0)
    return np.where(scale > 0, ramp, hard)


def _any_nan(a, b) -> bool:
    return bool(np.any(np.isnan(a)) or np.any(np.isnan(b)))


class Evaluator:
    def __init__(
        self,
        program: Program,
        timeline: Timeline,
        params: dict | None = None,
        smoothing: float = 0.0,
        check_redemption: bool = True,
        ramps: dict | None = None,
    ):
        self.program = program
        self.timeline = timeline
        self.params = {**program.param_values, **(params or {})}
        unknown = set(params or {}) - set(program.param_values)
        if unknown:
            raise KeyError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        self.eps = float(smoothing)
        self.check_redemption = check_redemption
        self.ramps = ramps or {}  # (event index, line of the comparison) -> Ramp
        self.smooth = self.eps != 0 or any(not r.shift for r in self.ramps.values())

    def run(self, scenario) -> BatchFlows:
        self.scenario = scenario
        self.vars: dict = {}
        out = BatchFlows(scenario.n_paths, alive=np.ones(scenario.n_paths))
        self.out = out
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            for k, ev in enumerate(self.program.events):
                self.t_obs = float(self.timeline.event_times[k])
                self.t_pay = self.timeline.pay_times[k]
                self.k = k
                before = out.alive
                self.block(ev.body, 1.0)
                out.terminated.append(before - out.alive)
        if self.check_redemption:
            total = sum(out.redemption_weights.values(), np.zeros(scenario.n_paths))
            tol = 1e-12 if not self.smooth else 1e-9
            bad = ~(np.abs(total - 1.0) <= tol)  # NaN weights fail too
            if bad.any():
                message = (
                    f"the redemption is not paid exactly once on {bad.mean():.2%} of the paths "
                    "(a valid product pays one 'redemption' per path)"
                )
                if not self.smooth or not np.all(np.isfinite(total)):
                    raise ScriptError(message, self.program.events[-1].line)
                out.warnings.append(message + " in the smoothing ramps")
        return out

    # --- statements ---
    def block(self, body, m):
        for s in body:
            if isinstance(s, Assign):
                e = self.expr(s.expr)
                if isinstance(m, float) and m == 1.0:
                    self.vars[s.name] = e
                else:
                    old = self.vars.get(s.name, 0.0)
                    if not self.smooth:
                        self.vars[s.name] = np.where(m == 1.0, e, old)
                    else:  # values outside the mask may be NaN: never multiply them by a zero weight
                        self.vars[s.name] = np.where(m == 1.0, e, np.where(m == 0.0, old, m * e + (1 - m) * old))
            elif isinstance(s, Pay):
                w = m * self.out.alive
                e = self.expr(s.expr)
                self._check_defined(~np.isfinite(e) & (w != 0), s.line, f"the amount paid as {s.tag} is not finite")
                key = (self.t_pay, s.tag)
                self.out.flows[key] = self.out.flows.get(key, 0.0) + np.where(w != 0, w * e, 0.0)
                if s.tag == "redemption":
                    self.out.redemption_weights[self.t_pay] = self.out.redemption_weights.get(self.t_pay, 0.0) + w
            elif isinstance(s, Terminate):
                self.out.alive = self.out.alive * (1.0 - m)
            elif isinstance(s, If):
                c = self.expr(s.cond)
                undefined = np.isnan(c)
                if np.any(undefined):
                    # a path that already terminated pays nothing more: its conditions do not matter
                    self._check_defined(
                        undefined & (np.asarray(m * self.out.alive) != 0),
                        s.line,
                        "the condition is undefined (NaN, e.g. the log of a negative number)",
                    )
                    c = np.where(undefined, 0.0, c)  # reached by no path
                self.block(s.body, m * c)
                if s.orelse:
                    self.block(s.orelse, m * (1.0 - c))

    def _check_defined(self, bad, line: int, what: str):
        bad = np.broadcast_to(bad, (self.scenario.n_paths,))
        if bad.any():
            raise ScriptError(f"{what} on {bad.mean():.2%} of the paths it reaches", line)

    # --- expressions ---
    def expr(self, e):
        if isinstance(e, Num):
            return e.value
        if isinstance(e, Name):
            if e.id in self.vars:
                return self.vars[e.id]
            if e.id in self.params:
                return self.params[e.id]
            return self.scenario.spot(e.id, self.t_obs)
        if isinstance(e, Binary):
            a, b = self.expr(e.left), self.expr(e.right)
            if e.op in ARITH:
                return ARITH[e.op](a, b)
            if e.op == "and":  # a certain false operand decides, even if the other one is undefined
                return np.where((a == 0) | (b == 0), 0.0, a * b) if _any_nan(a, b) else a * b
            if e.op == "or":
                return np.where((a == 1) | (b == 1), 1.0, a + b - a * b) if _any_nan(a, b) else a + b - a * b
            ramp = self.ramps.get((self.k, e.line)) if self.ramps else None
            if ramp is not None:
                return _ramped(e.op, a, b, ramp)
            return _compare(e.op, a, b, self.eps)
        if isinstance(e, Unary):
            v = self.expr(e.operand)
            return -v if e.op == "-" else 1.0 - v
        if isinstance(e, Call):
            args = [self.expr(a) for a in e.args]
            if e.func in ("worst", "best"):
                return functools.reduce(np.minimum if e.func == "worst" else np.maximum, args)
            return FUNCS[e.func](*args)
        if isinstance(e, Window):
            times = self.timeline.windows[e]
            if hasattr(self.scenario, "extreme"):  # a path source without it still works through fixings
                return self.scenario.extreme(e.underlying, times, "min" if e.func == "lowest" else "max")
            fix = self.scenario.fixings(e.underlying, times)
            return fix.min(axis=1) if e.func == "lowest" else fix.max(axis=1)
        raise TypeError(f"cannot evaluate {e!r}")
