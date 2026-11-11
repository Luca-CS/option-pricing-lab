"""Where market snapshots are kept: identified, immutable calibrated markets that every price points back to.
`optlab.server.persistence.snapshots.SnapshotRepository` implements it on the operational database (ADR-0007); the
local spot scale is kept beside a snapshot, never in its content."""

from __future__ import annotations

import datetime as dt
from typing import Protocol

from optlab.core.snapshot import MarketSnapshot


class SnapshotStorage(Protocol):
    def put(
        self, snap: MarketSnapshot
    ) -> str: ...  # the id; the same content twice is a no-op, a different one an error

    def get(self, snapshot_id: str) -> MarketSnapshot: ...  # KeyError when unknown

    def latest(self, underlying: str, on_or_before: dt.date | str | None = None) -> MarketSnapshot: ...

    def list(self, underlying: str | None = None) -> list[dict]: ...
