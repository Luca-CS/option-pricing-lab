"""The `optlab` command line (ADR-0010): every v0 script has its command and the commands start in a fresh process;
backups of the database and of the lake, and their restoration; the job queue from the console; the demo data.

The accounts commands are tested with the API (`test_api_admin.py`), the OpenAPI ones in `test_openapi.py`."""

from __future__ import annotations

import io
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from optlab.server import backups
from optlab.server.cli import app, run

REPO = Path(__file__).resolve().parents[3]


def _leaves() -> set[str]:
    """Every command of the tree, as `lake update`, `serve`."""
    import typer.main

    out: set[str] = set()

    def walk(cmd, prefix):
        subs = getattr(cmd, "commands", None)
        if subs:
            for name, c in subs.items():
                walk(c, (*prefix, name))
        else:
            out.add(" ".join(prefix))

    walk(typer.main.get_command(app), ())
    return out


def test_every_script_of_the_inventory_has_its_command():
    text = (REPO / "docs" / "architecture" / "feature-inventory.md").read_text(encoding="utf-8")
    section = text[text.index("## 3. Scripts") : text.index("## 4.")]
    leaves = _leaves()
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    assert len(rows) >= 19
    for row in rows:
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", row)[1:-1]]  # a `\|` inside a cell is not a border
        if len(cells) < 5:  # the table of the commands with no v0 script
            continue
        script, _, command, _, status = cells[:5]
        if "dropped" in status:
            continue
        found = re.findall(r"`optlab ([a-z][a-z \-\\|]*)`", command)
        assert found or "library module" in command, row
        for words in found:
            head, _, alternatives = words.replace("\\|", "|").rpartition(" ")
            for alt in alternatives.split("|"):
                wanted = f"{head} {alt}".strip()
                assert any(wanted == c or wanted.startswith(c + " ") for c in leaves), (script, wanted)
    assert not (REPO / "scripts").exists(), "the scripts are commands now (optlab.server.tools)"


@pytest.mark.parametrize(
    "argv",
    [
        ["-m", "optlab.server.cli", "lake", "update", "--help"],
        ["-m", "optlab.server.cli", "lake", "derive", "--help"],
        ["-m", "optlab.server.cli", "lake", "surface-test", "--help"],
        ["-m", "optlab.server.cli", "lake", "replay", "--help"],
        ["-m", "optlab.server", "--help"],  # `optlab serve`
        ["-m", "optlab.server.worker", "--help"],
    ],
)
def test_commands_start_in_a_fresh_process(argv):
    """In place of v0's test that the lake scripts start from their path alone."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, *argv], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "Usage:" in done.stdout


def test_a_library_error_is_a_message_and_exit_code_1(capsys):
    assert run(["lake", "derive", "--only", "nothing", "--lake", "unused"]) == 1
    assert "error: --only: nothing not a step" in capsys.readouterr().err
    assert run(["risk", "var", "--engine", "fast"]) == 2  # a usage error, from the parser
    assert run(["no-such-command"]) == 2


# --- the database: backup and restore --------------------------------------------------------------------------------
@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTLAB_DATA", str(tmp_path))
    return tmp_path


def _user(monkeypatch, name):
    monkeypatch.setattr("sys.stdin", io.StringIO(secrets.token_urlsafe(18) + "\n"))
    assert run(["users", "create", name, "--roles", "viewer", "--stdin"]) == 0


def _names(path: Path) -> set[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT name FROM users")}
    finally:
        con.close()


def test_backup_then_restore_the_database(data, monkeypatch, capsys):
    _user(monkeypatch, "alice")
    assert run(["backup"]) == 0
    (copy,) = (data / "backups" / "manual").glob("optlab-*.sqlite")
    assert _names(copy) == {"alice"}
    _user(monkeypatch, "bob")
    database = data / "store" / "optlab.sqlite"
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    assert run(["restore", "latest"]) == 1  # not confirmed: nothing changes
    assert _names(database) == {"alice", "bob"}
    assert run(["restore", "latest", "--yes"]) == 0
    assert _names(database) == {"alice"}
    (saved,) = (data / "backups" / "pre-restore").glob("optlab-*.sqlite")
    assert _names(saved) == {"alice", "bob"}  # what the restoration replaced is kept
    out = capsys.readouterr().out
    assert "restored" in out and "revision 0003" in out


def test_a_restoration_refuses_while_a_worker_serves_the_database(data, monkeypatch, capsys):
    from optlab.server.jobs import Workers
    from optlab.server.persistence import Database

    _user(monkeypatch, "alice")
    assert run(["backup"]) == 0
    db = Database(data / "store" / "optlab.sqlite")
    try:
        leases = Workers(db)
        assert leases.register("w-test", "here", 1, "external", "numpy", 1)
        assert run(["restore", "latest", "--yes"]) == 1
        assert "a worker serves" in capsys.readouterr().err
        leases.stop("w-test")
        assert run(["restore", "latest", "--yes"]) == 0
    finally:
        db.close()


def test_a_copy_is_checked_before_it_is_restored(tmp_path):
    other = tmp_path / "other.sqlite"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="not an optlab database"):
        backups.check_database_copy(other)
    from optlab.server.persistence import Database

    db = Database(tmp_path / "new.sqlite")
    copy = backups.database_copy(db, tmp_path / "copies")
    db.close()
    assert backups.check_database_copy(copy)["revision"] == "0003"
    con = sqlite3.connect(copy)
    con.execute("UPDATE alembic_version SET version_num = '9999'")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="unknown to this code"):
        backups.check_database_copy(copy)
    with pytest.raises(FileNotFoundError):
        backups.check_database_copy(tmp_path / "missing.sqlite")


# --- the lake: weekly copies -----------------------------------------------------------------------------------------
def _lake(root: Path) -> dict[str, Path]:
    files = {
        "curated/prices_eod/year=2020/run1.parquet": b"P" * 4000,
        "curated/prices_eod/year=2021/run2.parquet": b"Q" * 3000,
        "raw/manifest.jsonl": b'{"sha256": "a"}\n',
    }
    out = {}
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        out[rel] = p
    (root / "curated" / "prices_eod" / "year=2021" / "run3.tmp").write_bytes(b"being written")
    return out


def test_lake_copies_link_what_did_not_change_and_keep_the_last_ones(tmp_path):
    lake, folder = tmp_path / "lake", tmp_path / "backups" / "lake"
    files = _lake(lake)
    quiet = lambda *a: None  # noqa: E731
    first = backups.lake_snapshot(lake, folder, keep=2, log=quiet)
    assert (first["files"], first["copied"], first["linked"]) == (3, 3, 0)  # the .tmp file is left out
    with open(files["raw/manifest.jsonl"], "ab") as fh:  # a download appended to the raw manifest
        fh.write(b'{"sha256": "b"}\n')
    second = backups.lake_snapshot(lake, folder, keep=2, log=quiet)
    assert (second["copied"], second["linked"]) == (1, 2)
    one, two = backups.lake_snapshots(folder)
    rel = "curated/prices_eod/year=2020/run1.parquet"
    assert os.path.samefile(one / rel, two / rel)  # one file on disk for both copies
    assert not os.path.samefile(one / "raw/manifest.jsonl", two / "raw/manifest.jsonl")
    (folder / ".20000101-000000-000.partial").mkdir()  # a copy a crash interrupted
    third = backups.lake_snapshot(lake, folder, keep=2, log=quiet)
    assert third["removed"] == [one.name] and len(backups.lake_snapshots(folder)) == 2
    assert not list(folder.glob(".*.partial"))
    assert backups.verify_lake_snapshot(backups.lake_snapshots(folder)[-1]) == []


def test_a_lake_copy_is_checked_then_restored_beside_the_current_lake(tmp_path):
    lake, folder = tmp_path / "lake", tmp_path / "backups" / "lake"
    files = _lake(lake)
    backups.lake_snapshot(lake, folder, log=lambda *a: None)
    (snapshot,) = backups.lake_snapshots(folder)
    bad = tmp_path / "bad"
    shutil.copytree(snapshot, bad)
    (bad / "raw" / "manifest.jsonl").write_bytes(b"changed")
    (bad / "extra.txt").write_bytes(b"?")
    problems = backups.verify_lake_snapshot(bad)
    assert "hash of raw/manifest.jsonl" in problems or "size of raw/manifest.jsonl" in problems
    assert "not in the manifest: extra.txt" in problems
    with pytest.raises(ValueError):
        backups.restore_lake(bad, lake)
    (bad / backups.MANIFEST).unlink()
    assert "an unfinished copy" in backups.verify_lake_snapshot(bad)[0]
    files["curated/prices_eod/year=2020/run1.parquet"].unlink()  # lost
    out = backups.restore_lake(snapshot, lake)
    assert files["curated/prices_eod/year=2020/run1.parquet"].read_bytes() == b"P" * 4000
    aside = Path(out["previous_moved_to"])
    assert aside.is_dir() and not (aside / "curated/prices_eod/year=2020/run1.parquet").exists()
    assert (aside / "curated/prices_eod/year=2021/run3.tmp").exists()  # nothing of the old lake is deleted


def test_backup_with_the_lake_and_restore_it_from_the_console(data, monkeypatch, capsys):
    _lake(data / "lake")
    assert run(["backup", "--lake"]) == 0
    (snapshot,) = backups.lake_snapshots(data / "backups" / "lake")
    assert run(["restore", "latest", "--lake", "--yes"]) == 0
    assert "lake restored from " + snapshot.name in capsys.readouterr().out
    assert (data / "lake" / "raw" / "manifest.jsonl").is_file()


# --- the job queue from the console ----------------------------------------------------------------------------------
def test_a_job_left_to_the_worker_and_the_queue_from_the_console(data, capsys):
    from optlab.server.jobs import JobQueue
    from optlab.server.persistence import Database

    assert run(["lake", "update", "--as-job", "--only", "fred,treasury"]) == 0
    found = re.search(r"job (\S+) queued", capsys.readouterr().out)
    assert found
    job_id = found.group(1)
    db = Database(data / "store" / "optlab.sqlite")
    try:
        kind, params, _ = JobQueue(db).payload(job_id)
        assert kind == "lake" and params == {"only": ["fred", "treasury"]}
        assert JobQueue(db).owner(job_id) is None  # the system's: admins see it
    finally:
        db.close()
    assert run(["jobs", "list"]) == 0
    assert job_id in capsys.readouterr().out
    assert run(["jobs", "show", job_id]) == 0 and '"state": "queued"' in capsys.readouterr().out
    assert run(["jobs", "cancel", job_id]) == 0 and "cancelled" in capsys.readouterr().out
    assert run(["jobs", "wait", job_id]) == 1  # it ended cancelled
    assert run(["lake", "update", "--as-job", "--yahoo-chains"]) == 1  # a manual step stays manual


# --- the demo data ---------------------------------------------------------------------------------------------------
@pytest.mark.slow
def test_the_demo_data_serves_a_book_made_of_made_up_numbers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from optlab.core import engines
    from optlab.server import demo
    from optlab.server.api import Desk
    from optlab.server.app import create_app
    from optlab.server.settings import Settings

    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "file").write_text("someone's")
    with pytest.raises(FileExistsError):
        demo.build(tmp_path / "taken")
    folder = tmp_path / "demo"
    password = secrets.token_urlsafe(18)
    out = demo.build(folder, password=password, n_paths=2_000, log=lambda *a: None)
    assert "password" not in out and out["trades"] == ["DEMO-01", "DEMO-02", "DEMO-03", "DEMO-04"]
    assert demo.build(folder) == demo.info(folder)  # built once
    monkeypatch.setenv("OPTLAB_DATA", str(folder))
    settings = Settings.load()
    assert settings.market == out["market"] and settings.calibration.parent == folder / "archive"
    assert settings.lake == folder / "lake"
    desk = Desk(settings=settings, engine="numpy")
    try:
        desk.use_snapshot({"snapshot_id": settings.market})
        client = TestClient(create_app(settings, desk), base_url="http://127.0.0.1")
        r = client.post("/api/v1/auth/login", json={"name": "demo", "password": password})
        assert r.status_code == 200, r.text
        trades = client.get("/api/v1/trading/trades").json()["items"]
        assert {t["trade_id"] for t in trades} == set(out["trades"])
        assert client.get("/api/v1/market/context").json()["snapshot_id"] == settings.market
    finally:
        desk.close()
        engines.set_default(None)


def test_demo_data_never_takes_the_data_folder_of_the_environment(tmp_path, monkeypatch):
    from optlab.server import cli, demo

    real = tmp_path / "real-data"
    monkeypatch.setenv("OPTLAB_DATA", str(real))  # Luca's data, named by the environment
    monkeypatch.setattr(demo, "default_folder", lambda: tmp_path / "demo")
    monkeypatch.setattr(demo, "info", lambda folder: {"folder": str(folder)})  # built already
    monkeypatch.setitem(cli.GIVEN, "data", None)
    cli._use_demo()
    assert os.environ["OPTLAB_DATA"] == str(tmp_path / "demo")
    monkeypatch.setitem(cli.GIVEN, "data", tmp_path / "chosen")  # `optlab --data DIR serve --demo-data`
    cli._use_demo()
    assert os.environ["OPTLAB_DATA"] == str(tmp_path / "chosen")


def test_a_lake_copy_whose_manifest_points_outside_it_is_never_restored(tmp_path):
    """A restoration writes where the manifest's keys say: a key that leaves the copy (`../`, an absolute path) makes
    the copy unrestorable, even when the file it names exists with the right size and hash."""
    import hashlib
    import json

    lake, folder = tmp_path / "lake", tmp_path / "backups" / "lake"
    _lake(lake)
    backups.lake_snapshot(lake, folder, log=lambda *a: None)
    (snapshot,) = backups.lake_snapshots(folder)
    planted = tmp_path / "backups" / "planted.txt"  # outside the copy, as the key below resolves
    planted.write_bytes(b"payload")
    manifest = json.loads((snapshot / backups.MANIFEST).read_text(encoding="utf-8"))
    entry = {"size": 7, "mtime_ns": 0, "sha256": hashlib.sha256(b"payload").hexdigest()}
    for key in ("../planted.txt", "curated/../../planted.txt", str(planted)):
        bad = json.loads(json.dumps(manifest))
        bad["files"][key] = entry
        (snapshot / backups.MANIFEST).write_text(json.dumps(bad), encoding="utf-8")
        problems = backups.verify_lake_snapshot(snapshot)
        assert any("not a file inside the copy" in p for p in problems), (key, problems)
        with pytest.raises(ValueError):
            backups.restore_lake(snapshot, lake)
        assert not (tmp_path / "planted.txt").exists() and not (lake.parent.parent / "planted.txt").exists()
    assert lake.is_dir() and not list(tmp_path.glob("lake.before-restore-*"))  # nothing was moved either


def test_a_restored_database_keeps_no_session_and_records_its_restoration(data, monkeypatch):
    from optlab.server.persistence import Database
    from optlab.server.security import Accounts
    from optlab.server.settings import Settings

    _user(monkeypatch, "alice")
    db = Database(data / "store" / "optlab.sqlite")
    try:
        accounts = Accounts(db, Settings.load())
        password = secrets.token_urlsafe(18)
        accounts.create("carol", password, ["viewer"])
        accounts.open_session(accounts.authenticate("carol", password, "127.0.0.1"))  # a session in the copy
    finally:
        db.close()
    assert run(["backup"]) == 0
    assert run(["restore", "latest", "--yes"]) == 0
    con = sqlite3.connect(f"file:{data / 'store' / 'optlab.sqlite'}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        actions = [r[0] for r in con.execute("SELECT action FROM audit_log")]
    finally:
        con.close()
    assert "cli_restore_database" in actions


def test_the_demo_settings_never_name_the_real_database(tmp_path, monkeypatch):
    from optlab.server import demo
    from optlab.server.settings import Settings

    folder = tmp_path / "demo"
    demo.check_paths(folder, Settings(_env_file=None, **demo.paths(folder)))  # pyright: ignore[reportCallIssue]
    monkeypatch.setenv("OPTLAB_DATABASE", str(tmp_path / "real" / "optlab.sqlite"))
    with pytest.raises(RuntimeError, match="OPTLAB_DATABASE"):
        demo.check_paths(folder, Settings.load(data=folder))


def test_copies_made_in_the_same_millisecond_get_their_own_names(tmp_path, monkeypatch, data):
    """A fast machine makes two copies in one millisecond (the Linux runner did): each gets a name, in order."""
    from optlab.server.cli import _latest_copy
    from optlab.server.persistence import Database

    monkeypatch.setattr(backups, "stamp", lambda now=None: "20261113-120000-000")
    db = Database(tmp_path / "db.sqlite")
    try:
        first = backups.database_copy(db, data / "backups" / "manual")
        second = backups.database_copy(db, data / "backups" / "manual")
    finally:
        db.close()
    assert (first.name, second.name) == ("optlab-20261113-120000-000.sqlite", "optlab-20261113-120000-000-1.sqlite")
    assert _latest_copy(data / "backups") == second
    lake, folder = tmp_path / "lake", tmp_path / "backups" / "lake"
    _lake(lake)
    names = [backups.lake_snapshot(lake, folder, keep=3, log=lambda *a: None)["folder"] for _ in range(3)]
    assert names == ["20261113-120000-000", "20261113-120000-000-1", "20261113-120000-000-2"]
    assert [p.name for p in backups.lake_snapshots(folder)] == names  # oldest first, as made
