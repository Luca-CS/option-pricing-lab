"""Import a v0 desk folder into an optlab database (ADR-0007, handoff §4.4).

A v0 folder holds `desk.sqlite` (trades, fixings, quotes, settings, cost_defaults), `risk.sqlite` (records, runs),
`market/` (`index.sqlite` and one JSON file per snapshot) and `replay/` (one JSON file per replay); each part may be
missing. The import:

- opens every v0 file read-only and never writes to the folder;
- copies rows and JSON payloads as they are (text for text, so every number comes back bit for bit);
- is idempotent: rows are inserted when their key is new, so a second run adds nothing;
- checks at the end that every key of the source is in the target with the same payload, and says what it did.

    python -m optlab.server.persistence.migrate_v0 <v0 folder> <database file> [--backup <folder>]

`--backup` first copies the v0 folder (a dated sub-folder of the one given), as the handoff asks before a migration.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import Table, insert, select

from optlab.server.persistence import schema
from optlab.server.persistence.database import Database

# v0 table -> (v1 table, key columns); the columns keep their names and order
TABLES = {
    ("desk.sqlite", "trades"): (schema.trades, ("trade_id", "version")),
    ("desk.sqlite", "fixings"): (schema.fixings, ("underlying", "date")),
    ("desk.sqlite", "quotes"): (schema.quotes, ("quote_id",)),
    ("desk.sqlite", "settings"): (schema.desk_settings, ("key",)),
    ("desk.sqlite", "cost_defaults"): (schema.cost_defaults, ("version",)),
    ("risk.sqlite", "records"): (schema.risk_records, ("kind", "key", "version")),
    ("risk.sqlite", "runs"): (schema.risk_runs, ("run_id",)),
}


def _read_only(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _insert_new(db: Database, table: Table, keys: tuple, rows: list[dict]) -> int:
    """Insert the rows whose key is not in `table` yet; the number inserted."""
    if not rows:
        return 0
    with db.write() as c:
        have = {tuple(r) for r in c.execute(select(*[table.c[k] for k in keys]))}
        new = [r for r in rows if tuple(r[k] for k in keys) not in have]
        if new:
            c.execute(insert(table), new)
    return len(new)


def _rows_by_key(db: Database, table: Table, keys: tuple) -> dict:
    with db.read() as c:
        return {tuple(r[k] for k in keys): dict(r) for r in c.execute(select(table)).mappings()}


def migrate(folder: str | Path, target: str | Path) -> dict:
    """Import the v0 folder into the database file `target` (created at the head revision when missing)."""
    folder, target = Path(folder), Path(target)
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    if target.resolve().parent == folder.resolve() and target.name in ("desk.sqlite", "risk.sqlite"):
        raise ValueError("the target must be a new file, not one of the v0 stores")
    db = Database(target)
    report: dict = {"source": str(folder), "target": str(target), "tables": {}}
    try:
        expected: dict = {}
        for (file, name), (table, keys) in TABLES.items():
            path = folder / file
            if not path.is_file():
                continue
            con = _read_only(path)
            try:
                if name not in _tables(con):
                    continue
                columns = [c.name for c in table.c]
                rows = [dict(zip(columns, tuple(r))) for r in con.execute(f"SELECT {', '.join(columns)} FROM {name}")]
            finally:
                con.close()
            n = _insert_new(db, table, keys, rows)
            report["tables"][table.name] = {"source": len(rows), "inserted": n}
            expected[table.name] = (table, keys, {tuple(r[k] for k in keys): r for r in rows})

        snaps = _v0_snapshots(folder / "market")
        n = _insert_new(db, schema.snapshots, ("snapshot_id",), snaps)
        report["tables"]["snapshots"] = {"source": len(snaps), "inserted": n}
        expected["snapshots"] = (schema.snapshots, ("snapshot_id",), {(r["snapshot_id"],): r for r in snaps})

        replays = _v0_replays(folder / "replay")
        n = _insert_new(db, schema.replays, ("stem",), replays)
        report["tables"]["replays"] = {"source": len(replays), "inserted": n}
        expected["replays"] = (schema.replays, ("stem",), {(r["stem"],): r for r in replays})

        report["checked"] = _check(db, expected)
    finally:
        db.close()
    return report


def _v0_snapshots(market: Path) -> list[dict]:
    """The rows of `market/index.sqlite` with the content of each snapshot's JSON file, as written."""
    index = market / "index.sqlite"
    if not index.is_file():
        return []
    con = _read_only(index)
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM snapshots ORDER BY snapshot_id")]
    finally:
        con.close()
    out = []
    for r in rows:
        payload = (market / r["path"]).read_text(encoding="utf-8")
        out.append(
            {
                "snapshot_id": r["snapshot_id"],
                "underlying": r["underlying"],
                "valuation": r["valuation"],
                "asof": r["asof"],
                "source": r["source"],
                "payload": payload,
                "spot_scale": r["spot_scale"],
                "recorded_at": r["recorded_at"],
            }
        )
    return out


def _v0_replays(folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("replay*.json")):
        payload = path.read_text(encoding="utf-8")
        json.loads(payload)  # a broken file stops the import before anything is claimed
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
        out.append({"stem": path.stem, "payload": payload, "recorded_at": stamp})
    return out


def _check(db: Database, expected: dict) -> dict:
    """Every source row is in the target, column for column. Raises with the first difference."""
    counts = {}
    for name, (table, keys, rows) in expected.items():
        stored = _rows_by_key(db, table, keys)
        for key, row in rows.items():
            got = stored.get(key)
            if got is None:
                raise AssertionError(f"{name} {key}: missing after the import")
            for col, value in row.items():
                if got[col] != value and not (name == "replays" and col == "recorded_at"):
                    raise AssertionError(f"{name} {key}: {col} differs after the import")
        counts[name] = len(rows)
    return counts


def backup(folder: Path, into: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = into / f"{folder.name}-v0-{stamp}"
    shutil.copytree(folder, target)
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="the v0 folder (desk.sqlite, risk.sqlite, market/, replay/)")
    ap.add_argument("target", type=Path, help="the database file to create or complete")
    ap.add_argument("--backup", type=Path, help="copy the v0 folder under this folder first")
    args = ap.parse_args(argv)
    if args.backup:
        print(f"backup: {backup(args.folder, args.backup)}")
    report = migrate(args.folder, args.target)
    for name, t in report["tables"].items():
        print(f"{name:14s} {t['source']:6d} in the v0 store, {t['inserted']:6d} inserted")
    print(f"checked: every row of the v0 store is in {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
