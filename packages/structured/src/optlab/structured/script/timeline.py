"""Visitor that turns the dates of a program into simulation times."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from optlab.structured.dates import WeekendCalendar, year_fraction
from optlab.structured.script.nodes import Binary, Call, DateLit, If, Pay, Program, Unary, Window


@dataclass
class Timeline:
    event_times: np.ndarray  # observation time of each event
    pay_times: list  # payment time of each event, None without payment date
    windows: dict  # Window node → fixing times
    sim_times: np.ndarray  # sorted positive times to simulate

    @property
    def horizon(self) -> float:
        return float(max(self.event_times.max(), *(t for t in self.pay_times if t is not None)))


class Clock:
    """Converts script dates to times in years from the valuation date (ACT/365F) and lists the business days
    of a date window. Time literals (t=...) are already times."""

    def __init__(self, valuation: dt.date | str | None = None, calendar=None):
        self.valuation = dt.date.fromisoformat(valuation) if isinstance(valuation, str) else valuation
        self.calendar = calendar or WeekendCalendar()

    def time(self, w) -> float:
        if not isinstance(w, DateLit):
            return float(w.value)
        if self.valuation is None:
            raise ValueError("the script uses calendar dates: give a valuation date")
        return year_fraction(self.valuation, w.value, "ACT/365F")

    def window_times(self, w: Window) -> np.ndarray:
        if not isinstance(w.start, DateLit):
            a, b = float(w.start.value), float(w.end.value)
            return np.linspace(a + (b - a) / w.n, b, w.n)
        if self.valuation is None:
            raise ValueError("the script uses calendar dates: give a valuation date")
        days = self.calendar.business_days(w.start.value, w.end.value)
        return np.array([year_fraction(self.valuation, d, "ACT/365F") for d in days])


def _windows(node, out: list):
    if isinstance(node, Window):
        out.append(node)
    elif isinstance(node, Unary):
        _windows(node.operand, out)
    elif isinstance(node, Binary):
        _windows(node.left, out)
        _windows(node.right, out)
    elif isinstance(node, Call):
        for a in node.args:
            _windows(a, out)
    elif isinstance(node, If):
        _windows(node.cond, out)
        for s in node.body + node.orelse:
            _windows(s, out)
    elif isinstance(node, Pay) or hasattr(node, "expr"):
        _windows(node.expr, out)


def build_timeline(program: Program, clock: Clock | None = None) -> Timeline:
    clock = clock or Clock()
    event_times = np.array([clock.time(ev.when) for ev in program.events])
    pay_times = [clock.time(ev.pay) if ev.pay is not None else None for ev in program.events]
    found: list = []
    for ev in program.events:
        for s in ev.body:
            _windows(s, found)
    windows = {w: clock.window_times(w) for w in found}
    every = np.concatenate([event_times, *windows.values()]) if windows else event_times
    sim_times = np.unique(every[every > 0])
    return Timeline(event_times, pay_times, windows, sim_times)
