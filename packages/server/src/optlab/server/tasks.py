"""What a job carries and what the worker runs (ADR-0006).

A job is its kind, its parameters (the validated request, JSON) and `Frozen`: what the desk held when it was
submitted. The market in use is frozen by its snapshot id (snapshots are immutable), the typed basket settings and the
version of the cost table by value, the trades by (id, version) (every version stays readable), the fixings by value.
The worker rebuilds the same objects from them, so a job computes what it would have computed at once, whatever
changed in between (the invariant of handoff §2: switching the market never changes a run in progress).

`TASKS` maps a kind to a function (desk, params, frozen) -> envelope {market, method, result}. The desk, the risk
adapter and the schedules register theirs when imported (`optlab.server.api`, `risk_api`, `schedules`).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from optlab.server.api import Desk


@dataclass(frozen=True)
class Frozen:
    snapshot_id: str | None = None
    basket: dict[str, Any] = field(default_factory=dict[str, Any])
    costs: int | None = None  # version of the cost table; None: none was saved (the starting values)
    fixings: dict[str, Any] | None = None  # {name: {iso date: level}}
    trades: list[Any] | None = None  # [[trade_id, version]]

    def to_json(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "basket": self.basket,
            "costs": self.costs,
            "fixings": self.fixings,
            "trades": self.trades,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any] | None) -> Frozen:
        d = d or {}
        return cls(
            d.get("snapshot_id"),
            d.get("basket") or {},
            d.get("costs"),
            d.get("fixings"),
            d.get("trades"),
        )

    def fixings_by_date(self) -> dict[str, Any] | None:
        """The fixings with their dates as `datetime.date`, as the store gives them."""
        if self.fixings is None:
            return None
        return {
            n: {dt.date.fromisoformat(d): float(v) for d, v in levels.items()} for n, levels in self.fixings.items()
        }


def freeze_fixings(fixings: dict[str, Any] | None) -> dict[str, Any] | None:
    """{name: {date: level}} with ISO dates, for the row."""
    if fixings is None:
        return None
    return {
        n: {(d.isoformat() if isinstance(d, dt.date) else str(d)): float(v) for d, v in levels.items()}
        for n, levels in fixings.items()
    }


def envelope(market: dict[str, Any] | None, method: dict[str, Any] | str | None, result: Any) -> dict[str, Any]:
    """Every job result: the market it ran on, how it was computed, and what it computed (api.md §5)."""
    if isinstance(method, str):
        method = {"name": method}
    return {"market": market, "method": method, "result": result}


def market_of(snapshot_id: str | None, origin: Any, as_of: Any) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "origin": origin,
        "as_of": as_of.isoformat() if isinstance(as_of, dt.date) else as_of,
    }


Task = Callable[[Any, dict[str, Any], Frozen], dict[str, Any]]  # (desk, params, frozen state) -> result
TASKS: dict[str, Task] = {}


def register(kind: str, fn: Task) -> None:
    TASKS[kind] = fn


def run(desk: Desk, kind: str, params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if kind not in TASKS:
        raise KeyError(f"no task of kind {kind!r} in this worker")
    return TASKS[kind](desk, params, Frozen.from_json(context))
