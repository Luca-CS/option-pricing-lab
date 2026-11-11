"""Market data: the lake, its sources and derivations, the point-in-time markets, and the storage protocol of
snapshots (the type is `optlab.core.snapshot`, the store the server's)."""

from optlab.marketdata.store import SnapshotStorage

__all__ = ["SnapshotStorage"]
