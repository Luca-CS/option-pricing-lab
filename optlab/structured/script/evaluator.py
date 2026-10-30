"""Vectorised evaluation of a program on one batch of paths.

Every variable is an array over paths. Conditions are float masks (0/1, or a ramp in smoothed mode): inside an
`if`, `x = e` becomes `x = m*e + (1-m)*x`, `pay e` adds `m*alive*e` to the flows and `terminate` multiplies
`alive` by `1-m`.
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


def _compare(op, a, b, eps):
    hard = HARD[op](a, b).astype(float) if isinstance(a, np.ndarray) or isinstance(b, np.ndarray) \
        else float(HARD[op](a, b))
    if eps == 0 or op == "==":
        return hard
    scale = eps * np.abs(b)
    d = np.subtract(a, b) if op in (">", ">=") else np.subtract(b, a)
    with np.errstate(divide="ignore", invalid="ignore"):
        ramp = np.clip(d / scale + 0.5, 0.0, 1.0)
    return np.where(scale > 0, ramp, hard)


class Evaluator:
    def __init__(self, program: Program, timeline: Timeline, params: dict | None = None, smoothing: float = 0.0,
                 check_redemption: bool = True):
        self.program = program
        self.timeline = timeline
        self.params = {**program.param_values, **(params or {})}
        unknown = set(params or {}) - set(program.param_values)
        if unknown:
            raise KeyError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        self.eps = float(smoothing)
        self.check_redemption = check_redemption

    def run(self, scenario) -> BatchFlows:
        self.scenario = scenario
        self.vars: dict = {}
        out = BatchFlows(scenario.n_paths, alive=np.ones(scenario.n_paths))
        self.out = out
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            for k, ev in enumerate(self.program.events):
                self.t_obs = float(self.timeline.event_times[k])
                self.t_pay = self.timeline.pay_times[k]
                before = out.alive
                self.block(ev.body, 1.0)
                out.terminated.append(before - out.alive)
        if self.check_redemption and self.eps == 0:
            total = sum(out.redemption_weights.values(), np.zeros(scenario.n_paths))
            bad = np.abs(total - 1.0) > 1e-12
            if bad.any():
                raise ScriptError(f"the redemption is not paid exactly once on {bad.mean():.2%} of the paths "
                                  "(a valid product pays one 'redemption' per path)",
                                  self.program.events[-1].line)
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
                    self.vars[s.name] = np.where(m == 1.0, e, old) if self.eps == 0 else m * e + (1 - m) * old
            elif isinstance(s, Pay):
                w = m * self.out.alive
                e = self.expr(s.expr)
                key = (self.t_pay, s.tag)
                self.out.flows[key] = self.out.flows.get(key, 0.0) + np.where(w != 0, w * e, 0.0)
                if s.tag == "redemption":
                    self.out.redemption_weights[self.t_pay] = self.out.redemption_weights.get(self.t_pay, 0.0) + w
            elif isinstance(s, Terminate):
                self.out.alive = self.out.alive * (1.0 - m)
            elif isinstance(s, If):
                c = self.expr(s.cond)
                self.block(s.body, m * c)
                if s.orelse:
                    self.block(s.orelse, m * (1.0 - c))

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
            if e.op == "and":
                return a * b
            if e.op == "or":
                return a + b - a * b
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
            fix = self.scenario.fixings(e.underlying, self.timeline.windows[e])
            return fix.min(axis=1) if e.func == "lowest" else fix.max(axis=1)
        raise TypeError(f"cannot evaluate {e!r}")
