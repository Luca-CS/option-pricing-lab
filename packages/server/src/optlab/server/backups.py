"""Copies of the database and of the lake, and their restoration (handoff §4.12): `optlab backup`, `optlab restore`,
and the worker's `backup` (nightly) and `lake_backup` (weekly) schedules.

- **The database**: `VACUUM INTO` gives a consistent copy while the desk runs (`Database.backup`). A restoration
  checks the copy (SQLite's integrity check, a schema revision this code knows), refuses while a worker serves the
  database, saves the database it replaces in `<backups>/pre-restore/`, writes the copy in place with SQLite's backup
  API, then migrates it to this code's revision.
- **The lake** (Parquet files written once under a temporary name, a raw manifest appended to): a copy in
  `<backups>/lake/<stamp>/`, where a file unchanged since the last copy (same size, same modification time) is a hard
  link to it, so four weekly copies of 340 MB take little more than one. `MANIFEST.json`, written last, lists every
  file with its size and sha256: a folder without it is an unfinished copy, never restored. Restoring checks every
  hash, keeps the current lake beside it (`<lake>.before-restore-<stamp>`) and copies the files back.

Nothing here deletes data without a copy of it: what a restoration replaces is moved aside, not removed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from optlab.core.log import get_logger

LOG = get_logger(__name__)
MANIFEST = "MANIFEST.json"


def stamp(now: dt.datetime | None = None) -> str:
    """A sortable name to the millisecond: two copies in one second do not collide."""
    now = now or dt.datetime.now(dt.UTC)
    return f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"


def fresh(folder: Path, name: str, taken) -> str:
    """`name`, or `name-1`, `name-2`... when `taken(candidate)` says it is used: two copies in one millisecond (a fast
    Linux machine makes them) get two names, which still sort in the order they were made."""
    candidate, k = name, 0
    while taken(candidate):
        k += 1
        candidate = f"{name}-{k}"
    return candidate


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- the database ----------------------------------------------------------------------------------------------------
def database_copy(db, folder: Path, prefix: str = "optlab") -> Path:
    """A consistent copy of `db` (a `Database`) in `folder`."""
    folder = Path(folder)
    name = fresh(folder, f"{prefix}-{stamp()}", lambda n: (folder / f"{n}.sqlite").exists())
    return db.backup(folder / f"{name}.sqlite")


def prune_files(folder: Path, pattern: str, keep_days: int, keep: Path | None = None) -> list[str]:
    """Remove the files of `folder` matching `pattern` older than `keep_days` (never `keep`)."""
    cutoff = time.time() - keep_days * 86400
    removed = []
    for old in sorted(Path(folder).glob(pattern)):
        if old != keep and old.stat().st_mtime < cutoff:
            old.unlink()
            removed.append(old.name)
    return removed


def known_revisions() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from optlab.server.persistence.database import MIGRATIONS

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return {r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()}


def check_database_copy(path: Path) -> dict:
    """What a copy holds, or ValueError when it is not one this code can restore."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise ValueError(f"{path.name} fails SQLite's integrity check: {ok}")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "alembic_version" not in tables:
            raise ValueError(f"{path.name} is not an optlab database (no schema revision)")
        revision = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        counts = {
            t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]  # noqa: S608 (two fixed table names)
            for t in ("trades", "users")
            if t in tables
        }
    finally:
        con.close()
    if revision not in known_revisions():
        raise ValueError(f"{path.name} is at revision {revision!r}, unknown to this code (a newer optlab wrote it)")
    return {"file": str(path), "revision": revision, "bytes": path.stat().st_size, **counts}


def restore_database(source: Path, target: Path, backups: Path) -> dict:
    """Put the copy `source` in place of the database `target` (see the module's docstring)."""
    from optlab.server.jobs import Workers
    from optlab.server.persistence import Database

    info = check_database_copy(source)
    target = Path(target)
    if Path(source).resolve() == target.resolve():
        raise ValueError("the copy is the database itself")
    saved = None
    if target.exists():
        db = Database(target, upgrade=False)
        try:
            alive = []
            try:
                alive = Workers(db).alive()
            except Exception:  # an older revision has no workers table: nothing serves it today
                LOG.debug("no workers table in %s", target, exc_info=True)
            if alive:
                names = ", ".join(w["worker_id"] for w in alive)
                raise RuntimeError(f"a worker serves {target.name} ({names}): stop the api and the worker first")
            saved = database_copy(db, Path(backups) / "pre-restore")
        finally:
            db.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"{Path(source).resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    from sqlalchemy import delete

    from optlab.server.audit import Audit
    from optlab.server.persistence.schema import sessions

    db = Database(target)  # migrated to this code's revision
    try:
        revision = db.revision()
        with db.write() as c:  # sessions of the copy's time come back with it: none survives a restoration
            closed = c.execute(delete(sessions)).rowcount or 0
        detail = {"from": Path(source).name, "revision": info["revision"], "sessions_closed": closed}
        Audit(db).record(None, "cli_restore_database", target.name, detail=detail)
    finally:
        db.close()
    return {
        **info,
        "target": str(target),
        "saved_as": None if saved is None else str(saved),
        "revision_now": revision,
        "sessions_closed": closed,
    }


# --- the lake --------------------------------------------------------------------------------------------------------
def lake_snapshots(folder: Path) -> list[Path]:
    """The finished copies of `folder` (those with a manifest), oldest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir() and (p / MANIFEST).is_file())


def _manifest(snapshot: Path) -> dict:
    return json.loads((Path(snapshot) / MANIFEST).read_text(encoding="utf-8"))


def lake_snapshot(lake_root: Path, folder: Path, keep: int = 4, log: Callable[[str], None] = LOG.info) -> dict:
    """Copy the lake into `folder/<stamp>/`, linking what the last copy already holds; keep the last `keep` copies."""
    from optlab.core import progress

    root = Path(lake_root)
    if not root.is_dir():
        raise FileNotFoundError(f"no lake at {root}")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    copies = lake_snapshots(folder)
    previous = copies[-1] if copies else None
    before = _manifest(previous)["files"] if previous else {}
    name = fresh(folder, stamp(), lambda n: (folder / n).exists() or (folder / f".{n}.partial").exists())
    work = folder / f".{name}.partial"
    files: dict[str, dict] = {}
    linked = copied = copied_bytes = 0
    sources = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix != ".tmp")
    for i, path in enumerate(sources):
        rel = path.relative_to(root).as_posix()
        st = path.stat()
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        old = before.get(rel)
        if previous is not None and old and old["size"] == st.st_size and old["mtime_ns"] == st.st_mtime_ns:
            try:
                os.link(previous / rel, dest)
                files[rel] = old
                linked += 1
                continue
            except OSError:  # another file system, or no hard links: copy it
                pass
        shutil.copy2(path, dest)
        files[rel] = {"size": dest.stat().st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256(dest)}
        copied += 1
        copied_bytes += files[rel]["size"]
        if i % 200 == 0:
            progress.tick(i, len(sources), "files")
    manifest = {
        "lake": str(root),
        "made_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "linked_to": previous.name if previous else None,
        "files": files,
    }
    (work / MANIFEST).write_text(json.dumps(manifest, indent=0, sort_keys=True), encoding="utf-8")
    target = folder / name
    work.rename(target)
    removed = []
    for old_copy in lake_snapshots(folder)[:-keep] if keep > 0 else []:
        shutil.rmtree(old_copy)
        removed.append(old_copy.name)
    for stale in folder.glob(".*.partial"):  # a copy a crash interrupted
        if stale != work:
            shutil.rmtree(stale, ignore_errors=True)
    total = sum(f["size"] for f in files.values())
    log(f"lake copy {name}: {len(files)} files, {total / 1e6:.0f} MB, {copied} copied, {linked} linked")
    return {
        "folder": name,
        "files": len(files),
        "bytes": total,
        "copied": copied,
        "copied_bytes": copied_bytes,
        "linked": linked,
        "removed": removed,
        "keep": keep,
    }


def _inside(rel: str) -> bool:
    """A manifest key names a file inside its folder: relative, in `/` form, without `..`, `.`, a drive or a `\\`."""
    parts = PurePosixPath(rel).parts
    return (
        bool(rel)
        and "\\" not in rel
        and ":" not in rel
        and not PurePosixPath(rel).is_absolute()
        and all(p not in ("", ".", "..") for p in parts)
    )


def _contained(base: Path, rel: str) -> bool:
    return (Path(base) / rel).resolve().is_relative_to(Path(base).resolve())


def verify_lake_snapshot(snapshot: Path) -> list[str]:
    """What is wrong with a copy (empty when the files found in it are exactly those of its manifest, each with its
    size and hash). A key that would point outside the copy, or a link inside it, makes it unrestorable: a restoration
    writes where the keys say, so they are checked as untrusted input."""
    snapshot = Path(snapshot)
    if not (snapshot / MANIFEST).is_file():
        return [f"{snapshot.name}: no {MANIFEST} (an unfinished copy)"]
    files = _manifest(snapshot)["files"]
    problems = [
        f"not a file inside the copy: {rel!r}" for rel in files if not _inside(rel) or not _contained(snapshot, rel)
    ]
    found = set()
    for p in snapshot.rglob("*"):
        rel = p.relative_to(snapshot).as_posix()
        if p.is_symlink():
            problems.append(f"a link: {rel}")
        elif p.is_file() and rel != MANIFEST:
            found.add(rel)
    for rel, f in files.items():
        if not _inside(rel):
            continue
        p = snapshot / rel
        if rel not in found:
            problems.append(f"missing {rel}")
        elif p.stat().st_size != f["size"]:
            problems.append(f"size of {rel}")
        elif sha256(p) != f["sha256"]:
            problems.append(f"hash of {rel}")
    problems += [f"not in the manifest: {rel}" for rel in sorted(found - set(files))]
    return problems


def restore_lake(snapshot: Path, lake_root: Path) -> dict:
    """Put a checked copy in place of the lake; the current lake is moved beside it, not deleted."""
    snapshot, root = Path(snapshot), Path(lake_root)
    problems = verify_lake_snapshot(snapshot)
    if problems:
        raise ValueError(f"{snapshot.name} cannot be restored: " + "; ".join(problems[:5]))
    files = _manifest(snapshot)["files"]
    if not all(_contained(root, rel) for rel in files):  # checked above; a second guard on the side that writes
        raise ValueError(f"{snapshot.name} names files outside the lake")
    aside = None
    if root.exists():
        aside = root.with_name(f"{root.name}.before-restore-{stamp()}")
        root.rename(aside)
    for rel in files:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot / rel, dest)
    return {
        "snapshot": str(snapshot),
        "lake": str(root),
        "files": len(files),
        "previous_moved_to": aside and str(aside),
    }
