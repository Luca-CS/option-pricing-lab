"""Progress of a long computation, for whoever runs it (the desk's worker): silent unless a reporter is set.

The library calls `tick(done, total, unit)` where a loop knows how far it is (the batches of a simulation, the chunks of
a VaR, the days of a backfill); the caller names its own steps with `step(name, index, count)`. Without a reporter it does nothing, so prices, timings and tests are unchanged. A
reporter set with `reporting(reporter)` (a context variable: one per thread, nothing global) receives the ticks, and
may stop the computation between two of them by raising `Cancelled`, which the loops let through after releasing what
they hold. No thread and no I/O here.

A loop that runs other loops (the shocks of a Greeks run, each one a simulation that ticks its own batches) wraps each
of them in `part(index, count)`: the inner ticks are then read inside that part of the outer loop, so the progress goes
through every simulation once instead of reaching its end with the first one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol


class Cancelled(Exception):
    """The computation was asked to stop: raised by a reporter at a tick, never by the library itself."""


class Reporter(Protocol):
    def tick(self, done: int, total: int, unit: str | None) -> None: ...


_REPORTER: ContextVar[Reporter | None] = ContextVar("optlab_progress", default=None)


def tick(done: int, total: int, unit: str | None = None) -> None:
    """`done` of `total` units of the current loop are finished (a reporter may raise `Cancelled` here)."""
    reporter = _REPORTER.get()
    if reporter is not None:
        reporter.tick(int(done), int(total), unit)


def step(name: str, index: int = 0, count: int = 1) -> None:
    """The computation enters its step `index` (from 0) of `count`, named `name`; a reporter may raise `Cancelled`."""
    reporter = _REPORTER.get()
    begin = getattr(reporter, "step", None)
    if begin is not None:
        begin(name, int(index), int(count))


PART_SCALE = 1_000_000  # the resolution of a part's ticks in its outer loop: fine through a few levels of nesting


class _Part:
    """The reporter seen by a loop that is part `index` of `count` of an outer one."""

    def __init__(self, outer: Reporter, index: int, count: int):
        self.outer, self.index, self.count = outer, index, max(count, 1)

    def tick(self, done: int, total: int, unit: str | None) -> None:
        within = min(max(done / total, 0.0), 1.0) if total > 0 else 0.0
        self.outer.tick(round((self.index + within) * PART_SCALE), self.count * PART_SCALE, unit)


@contextmanager
def part(index: int, count: int) -> Iterator[None]:
    """The loops inside are part `index` (from 0) of `count` of the current one: their ticks are read inside that part.
    Nothing without a reporter."""
    outer = _REPORTER.get()
    if outer is None:
        yield
        return
    token = _REPORTER.set(_Part(outer, int(index), int(count)))
    try:
        yield
    finally:
        _REPORTER.reset(token)
    outer.tick((int(index) + 1) * PART_SCALE, max(int(count), 1) * PART_SCALE, None)


def active() -> bool:
    return _REPORTER.get() is not None


@contextmanager
def reporting(reporter: Reporter | None) -> Iterator[None]:
    token = _REPORTER.set(reporter)
    try:
        yield
    finally:
        _REPORTER.reset(token)
