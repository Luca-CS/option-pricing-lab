"""The stores the domain packages are given, on a fresh database in a test folder: the server's implementations of
`risk.store.RiskStorage` and `marketdata.store.SnapshotStorage` (ADR-0007)."""

from pathlib import Path


def database(folder):
    from optlab.server.persistence import Database

    return Database(Path(folder) / "optlab.sqlite")


def risk_store(folder):
    from optlab.server.persistence.risk import RiskRepository

    return RiskRepository(database(folder))


def snapshot_store(folder):
    from optlab.server.persistence.snapshots import SnapshotRepository

    return SnapshotRepository(database(folder))
