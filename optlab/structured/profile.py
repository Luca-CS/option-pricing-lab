"""Visitor that classifies a program by how its payoff depends on the path, for the pricing policy."""

from __future__ import annotations

from dataclasses import dataclass

from optlab.structured.script.nodes import Assign, Binary, Call, If, Name, Pay, Program, Terminate, Unary, Window
from optlab.structured.script.timeline import Timeline


@dataclass(frozen=True)
class ProductProfile:
    product_class: str  # european, path_dependent or callable
    observation_times: tuple  # future times at which the payoff reads the underlying
    has_windows: bool
    early_termination: bool
    horizon: float
    underlyings: tuple = ()

    @property
    def description(self) -> str:
        if len(self.underlyings) > 1:
            return f"basket of {len(self.underlyings)} names ({', '.join(self.underlyings)}), {self.product_class}"
        if self.product_class == "callable":
            return "early termination (autocall): the payoff depends on the path up to the call date"
        if self.product_class == "path_dependent":
            what = "daily barrier window" if self.has_windows else f"{len(self.observation_times)} observation dates"
            return f"path-dependent ({what}), no early termination"
        return "flows depend on the final fixing only (plus deterministic flows)"


def _reads(node, underlyings: set, found: dict):
    if isinstance(node, Name):
        if node.id in underlyings:
            found["spot"] = True
    elif isinstance(node, Window):
        found["window"] = True
    elif isinstance(node, Unary):
        _reads(node.operand, underlyings, found)
    elif isinstance(node, Binary):
        _reads(node.left, underlyings, found)
        _reads(node.right, underlyings, found)
    elif isinstance(node, Call):
        for a in node.args:
            _reads(a, underlyings, found)
    elif isinstance(node, If):
        _reads(node.cond, underlyings, found)
        for s in node.body + node.orelse:
            _reads(s, underlyings, found)
    elif isinstance(node, (Assign, Pay)):
        _reads(node.expr, underlyings, found)
    elif isinstance(node, Terminate):
        found["terminate"] = True


def profile(program: Program, timeline: Timeline) -> ProductProfile:
    underlyings = set(program.underlying_names)
    times, windows, terminate = [], False, False
    for ev, t in zip(program.events, timeline.event_times):
        found: dict = {}
        for s in ev.body:
            _reads(s, underlyings, found)
        windows |= found.get("window", False)
        terminate |= found.get("terminate", False)
        if found.get("spot") and t > 0:
            times.append(float(t))
    if terminate:
        cls = "callable"
    elif windows or len(times) > 1:
        cls = "path_dependent"
    else:
        cls = "european"
    return ProductProfile(cls, tuple(times), windows, terminate, timeline.horizon, program.underlying_names)
