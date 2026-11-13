"""Progress of a long computation, for whoever runs it (the desk's worker): silent unless a reporter is set.

The library calls `tick(done, total, unit)` where a loop knows how far it is (the batches of a simulation, the chunks of
a VaR, the days of a backfill); the caller names its own steps with `step(name, index, count)`. Without a reporter it does nothing, so prices, timings and tests are unchanged. A
reporter set with `reporting(reporter)` (a context variable: one per thread, nothing global) receives the ticks, and
may stop the computation between two of them by raising `Cancelled`, which the loops let through after releasing what
they hold. No thread and no I/O here.
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


def active() -> bool:
    return _REPORTER.get() is not None


@contextmanager
def reporting(reporter: Reporter | None) -> Iterator[None]:
    token = _REPORTER.set(reporter)
    try:
        yield
    finally:
        _REPORTER.reset(token)
