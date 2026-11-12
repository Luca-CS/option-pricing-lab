"""The operational database (ADR-0007): the schema of the head migration is the declared one, the repositories behave
as the v0 stores did, concurrent writers do not collide, and a v0 folder imports idempotently, bit for bit, without
being touched."""

import datetime as dt
import hashlib
import json
import sqlite3
import threading

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from optlab.core.snapshot import MarketSnapshot
from optlab.server.persistence import Database
from optlab.server.persistence.desk import DeskRepository
from optlab.server.persistence.migrate_v0 import migrate
from optlab.server.persistence.replays import ReplayRepository
from optlab.server.persistence.risk import RiskRepository
from optlab.server.persistence.schema import metadata
from optlab.server.persistence.snapshots import SnapshotRepository
from optlab.structured import preset
from optlab.structured.trade import Trade


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "optlab.sqlite")
    yield d
    d.close()


def snapshot(underlying="SPX", day="2026-11-04", level=0.04) -> MarketSnapshot:
    market = {"spot": 100.0, "pillars": [0.5, 1.0], "discounts": [0.99, 0.98], "forwards": [100.5, 101.0]}
    return MarketSnapshot(
        underlying,
        f"{day}T16:00:00",
        dt.date.fromisoformat(day),
        "manual",
        market,
        {"theta": level},
        {"v0": level},
        {"mix": 0.5},
    )


def test_the_head_migration_builds_the_declared_schema(db):
    assert db.revision() == db.head() == "0002"
    with db.engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_a_v0_store_is_never_opened_as_a_database(tmp_path):
    v0 = tmp_path / "desk.sqlite"
    con = sqlite3.connect(v0)
    con.execute("CREATE TABLE trades (trade_id TEXT)")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="v0 store"):
        Database(v0)


def test_trades_fixings_settings_and_defaults_behave_as_in_v0(db):
    repo = DeskRepository(db)
    t = Trade.from_term_sheet("T-1", preset("phoenix"), -1.0, dt.date(2026, 11, 2))
    first, second = repo.save_trade(t), repo.save_trade(t)
    assert (first.version, second.version) == (1, 2) and repo.trade("T-1").version == 2
    assert repo.trade("T-1", version=1).version == 1 and [v["version"] for v in repo.versions("T-1")] == [1, 2]
    repo.delete_trade("T-1")
    assert repo.trades() == [] and repo.versions("T-1")[-1]["deleted"]
    with pytest.raises(KeyError):
        repo.trade("T-1")
    repo.restore_trade("T-1")
    assert [x.trade_id for x in repo.trades()] == ["T-1"] and repo.new_trade_id().startswith("T-0002-")
    assert repo.put_fixings("SPX", {dt.date(2026, 1, 2): 100.0, "2026-01-05": 101.0}) == 2
    repo.put_fixings("SPX", {dt.date(2026, 1, 2): 99.0})
    assert repo.fixings() == {"SPX": {dt.date(2026, 1, 2): 99.0, dt.date(2026, 1, 5): 101.0}}
    repo.delete_fixing("SPX", "2026-01-05")
    assert repo.fixings("SPX") == {"SPX": {dt.date(2026, 1, 2): 99.0}}
    assert repo.setting("basket", {"x": 1}) == {"x": 1}
    repo.put_setting("basket", {"names": ["SPX"]})
    assert repo.setting("basket") == {"names": ["SPX"]}
    assert repo.defaults() is None and repo.save_defaults({"g": 1}) == 1 and repo.save_defaults({"g": 2}) == 2
    latest, first_table = repo.defaults(), repo.defaults(1)
    assert latest is not None and first_table is not None
    assert latest["groups"] == {"g": 2} and first_table["groups"] == {"g": 1}
    assert [v["version"] for v in repo.default_versions()] == [2, 1]
    qid = repo.save_quote({"type": "autocall"}, {"headline": "6 %"})
    assert repo.quote(qid)["result"] == {"headline": "6 %"} and repo.quotes()[0]["headline"] == "6 %"


def test_snapshots_are_immutable_and_keep_their_scale_beside_them(db):
    store = SnapshotRepository(db)
    snap = snapshot()
    sid = store.put(snap)
    assert store.put(snap) == sid and store.get(sid) == snap and store.get(sid).spot_scale is None
    scaled = snap.with_spot_scale(6800.0)
    store.put(scaled)
    assert store.get(sid).spot_scale == pytest.approx(68.0) and "spot_scale" not in store.get(sid).to_json()
    later = snapshot(day="2026-11-05")
    store.put(later)
    assert store.latest("SPX").snapshot_id == later.snapshot_id
    assert store.latest("SPX", "2026-11-04").snapshot_id == sid
    assert [r["snapshot_id"] for r in store.list("SPX")] == [sid, later.snapshot_id]
    with pytest.raises(KeyError):
        store.latest("SX5E")


def test_risk_records_and_runs_and_replays(db):
    risk = RiskRepository(db)
    assert risk.put("scenarios", "default", [1]) == 1 and risk.put("scenarios", "default", [2]) == 2
    last, first = risk.get("scenarios", "default"), risk.get("scenarios", "default", 1)
    assert last is not None and first is not None and last["payload"] == [2] and first["payload"] == [1]
    assert risk.keys("scenarios") == ["default"] and len(risk.versions("scenarios", "default")) == 2
    a = risk.save_run("eod", dt.date(2026, 11, 4), "k1", {"n": 1}, {"pnl": 1.0}, 1.5)
    b = risk.save_run("eod", "2026-11-04", "k2", {"n": 2}, {"pnl": 2.0}, 1.5)
    found = risk.find_run("k1")
    assert (
        (a, b) == ("eod-2026-11-04-v1", "eod-2026-11-04-v2") and found is not None and found["payload"] == {"pnl": 1.0}
    )
    assert [r["run_id"] for r in risk.runs("eod")] == [b] and len(risk.runs("eod", latest=False)) == 2
    assert "payload" not in risk.runs("eod")[0] and risk.runs("eod")[0]["params"] == {"n": 2}
    replays = ReplayRepository(db)
    replays.save("replay-T-lv", {"events": [1]})
    replays.save("replay-T-lv", {"events": [1, 2]})
    assert replays.get("replay-T-lv") == {"events": [1, 2]} and [s for s, _ in replays.all()] == ["replay-T-lv"]


def test_concurrent_writers_take_turns(db):
    risk = RiskRepository(db)
    errors = []

    def write(i):
        try:
            for _ in range(10):
                risk.put("load", "same", i)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and [v["version"] for v in risk.versions("load", "same")] == list(range(1, 41))


def test_the_backup_is_a_whole_consistent_copy(db, tmp_path):
    DeskRepository(db).put_setting("k", 1)
    copy = Database(db.backup(tmp_path / "backups" / "copy.sqlite"))
    try:
        assert DeskRepository(copy).setting("k") == 1
    finally:
        copy.close()
    with pytest.raises(FileExistsError):
        db.backup(tmp_path / "backups" / "copy.sqlite")


# --- the import of a v0 folder --------------------------------------------------------------------------------------
V0_DESK = (
    "CREATE TABLE trades (trade_id TEXT, version INTEGER, book TEXT, status TEXT, payload TEXT, deleted INTEGER "
    "DEFAULT 0, recorded_at TEXT, PRIMARY KEY (trade_id, version))",
    "CREATE TABLE fixings (underlying TEXT, date TEXT, level REAL, source TEXT, recorded_at TEXT, PRIMARY KEY "
    "(underlying, date))",
    "CREATE TABLE quotes (quote_id TEXT PRIMARY KEY, created_at TEXT, sheet TEXT, result TEXT)",
    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, recorded_at TEXT)",
    "CREATE TABLE cost_defaults (version INTEGER PRIMARY KEY, payload TEXT, recorded_at TEXT)",
)
V0_RISK = (
    "CREATE TABLE records (kind TEXT, key TEXT, version INTEGER, payload TEXT, meta TEXT, created_at TEXT, PRIMARY KEY "
    "(kind, key, version))",
    "CREATE TABLE runs (run_id TEXT PRIMARY KEY, kind TEXT, day TEXT, run_key TEXT, version INTEGER, code TEXT, params "
    "TEXT, payload TEXT, created_at TEXT, seconds REAL)",
)


def v0_folder(root):
    """A v0 desk folder as v0 wrote it: the same schemas, one row of each kind, a snapshot file and a replay file."""
    root.mkdir()
    con = sqlite3.connect(root / "desk.sqlite")
    for stmt in V0_DESK:
        con.execute(stmt)
    t = Trade.from_term_sheet("T-0001-abcd", preset("phoenix"), -2.0, dt.date(2026, 11, 2))
    con.execute(
        "INSERT INTO trades VALUES (?, 1, ?, ?, ?, 0, '2026-11-01T10:00:00+00:00')",
        (t.trade_id, t.book, t.status, t.to_json()),
    )
    con.execute("INSERT INTO fixings VALUES ('SPX', '2026-11-03', 99.12345678901234, 'yahoo', 'now')")
    con.execute("INSERT INTO quotes VALUES ('q1', 'now', '{}', '{\"headline\": \"x\"}')")
    con.execute("INSERT INTO settings VALUES ('basket', '{\"names\": [\"SPX\", \"SX5E\"]}', 'now')")
    con.execute("INSERT INTO cost_defaults VALUES (1, '{\"groups\": {}}', 'now')")
    con.commit()
    con.close()
    con = sqlite3.connect(root / "risk.sqlite")
    for stmt in V0_RISK:
        con.execute(stmt)
    con.execute("INSERT INTO records VALUES ('scenarios', 'default', 1, '[1, 2]', '{}', 'now')")
    con.execute(
        "INSERT INTO runs VALUES ('eod-2026-11-04-v1', 'eod', '2026-11-04', 'k', 1, 'abc', '{}', "
        "'{\"pnl\": -1234.5678901234567}', 'now', 3.25)"
    )
    con.commit()
    con.close()
    snap = snapshot()
    folder = root / "market" / "SPX" / "2026-11-04"
    folder.mkdir(parents=True)
    (folder / f"{snap.snapshot_id}.json").write_text(snap.to_json(), encoding="utf-8")
    con = sqlite3.connect(root / "market" / "index.sqlite")
    con.execute(
        "CREATE TABLE snapshots (snapshot_id TEXT PRIMARY KEY, underlying TEXT, valuation TEXT, asof TEXT, "
        "source TEXT, path TEXT, spot_scale REAL, recorded_at TEXT)"
    )
    con.execute(
        "INSERT INTO snapshots VALUES (?, 'SPX', '2026-11-04', ?, 'manual', ?, 68.0, 'now')",
        (snap.snapshot_id, snap.asof, f"SPX/2026-11-04/{snap.snapshot_id}.json"),
    )
    con.commit()
    con.close()
    (root / "replay").mkdir()
    (root / "replay" / "replay-T-lv.json").write_text(json.dumps({"events": [{"pnl": 0.1}]}), encoding="utf-8")
    return root, snap


def fingerprint(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_a_v0_folder_imports_idempotently_bit_for_bit_and_untouched(tmp_path):
    root, snap = v0_folder(tmp_path / "showcase")
    before = fingerprint(root)
    target = tmp_path / "new" / "optlab.sqlite"
    first = migrate(root, target)
    assert {k: v["inserted"] for k, v in first["tables"].items()} == {
        "trades": 1,
        "fixings": 1,
        "quotes": 1,
        "desk_settings": 1,
        "cost_defaults": 1,
        "risk_records": 1,
        "risk_runs": 1,
        "snapshots": 1,
        "replays": 1,
    }
    again = migrate(root, target)
    assert all(v["inserted"] == 0 for v in again["tables"].values()) and fingerprint(root) == before
    db = Database(target)
    try:
        desk = DeskRepository(db)
        assert desk.trade("T-0001-abcd").quantity == -2.0 and desk.setting("basket") == {"names": ["SPX", "SX5E"]}
        assert desk.fixings("SPX")["SPX"][dt.date(2026, 11, 3)] == 99.12345678901234
        assert RiskRepository(db).run("eod-2026-11-04-v1")["payload"] == {"pnl": -1234.5678901234567}
        stored = SnapshotRepository(db).get(snap.snapshot_id)
        assert stored == snap and stored.spot_scale == 68.0
        assert ReplayRepository(db).get("replay-T-lv") == {"events": [{"pnl": 0.1}]}
    finally:
        db.close()
