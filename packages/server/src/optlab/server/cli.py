"""The `optlab` command line (ADR-0010): one Typer application over the desk, its data and v0's scripts.

    optlab serve [--open] [--demo-data]      the API (127.0.0.1), with its worker unless OPTLAB_WORKER=external
    optlab worker                            the job queue's worker, alone (the `worker` container)
    optlab users create|list|roles|password|disable|enable
    optlab backup [--lake] | restore PATH [--lake] | migrate [--v0 FOLDER] | jobs list|show|wait|cancel
    optlab api export|check                  the versioned OpenAPI document
    optlab check fast|full|gpu|golden        the test suites (from a checkout)
    optlab lake update|derive|fx-check|surface-test|regime-test|replay
    optlab risk eod|backfill|pnl|var|ladder
    optlab calib run|report|download-spx     optlab structured examples|pricing-report
    optlab demo data|seed-showcase|widget-data      optlab bench accel|numpy     optlab version

`--data DIR` before the command (or `OPTLAB_DATA`) picks the data folder; every other path comes from its settings
(`<data>/config/optlab.env`). `python -m optlab.server`, `python -m optlab.server.worker`, `.users` and `.openapi`
stay as aliases of `serve`, `worker`, `users` and `api`. Each inventory row of a v0 script names its command here
(`docs/architecture/feature-inventory.md` §3).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from optlab.server.tools import say

app = typer.Typer(
    name="optlab",
    help="optlab: option pricing, structured products and risk; the desk's server and its tools.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
ENGINES = ("auto", "gpu", "cpu", "numpy")
FAILURES = (ValueError, KeyError, RuntimeError, FileNotFoundError, FileExistsError, PermissionError)


def _group(name: str, help: str) -> typer.Typer:
    sub = typer.Typer(name=name, help=help, no_args_is_help=True, rich_markup_mode=None)
    app.add_typer(sub)
    return sub


users_app = _group("users", "Accounts (ADR-0020): every change is in the audit log, as the system.")
api_app = _group("api", "The versioned OpenAPI document, api/openapi.json.")
check_app = _group("check", "The test suites and the golden numbers (from a checkout).")
jobs_app = _group("jobs", "The job queue of the database: list, show, wait for, cancel.")
lake_app = _group("lake", "The historical lake: updates, derived tables, checks and replays.")
risk_app = _group("risk", "Risk runs on a desk database: EOD, backfill, P&L, VaR, ladder.")
calib_app = _group("calib", "The calibration chain SSVI -> SVI -> Heston -> SLV and its report.")
structured_app = _group("structured", "Structured products: example quotes and the desk-price report.")
demo_app = _group("demo", "Demo data: the synthetic demo folder, the showcase book, the demo page's data.")
bench_app = _group("bench", "Benchmarks of the Monte Carlo engines.")


def _choice(name: str, value: str | None, allowed) -> None:
    if value is not None and value not in allowed:
        raise typer.BadParameter(f"{value!r} is not one of {', '.join(allowed)}", param_hint=name)


def _split(values: list[str] | None) -> list[str] | None:
    """A list option given as repeats or comma-separated (v0's `--only a b` reads `--only a,b` now)."""
    if values is None:
        return None
    return [x for v in values for x in v.split(",") if x]


def _settings(**overrides):
    from optlab.server.settings import Settings

    return Settings.load(**overrides)


@app.callback()
def _root(
    data: Annotated[
        Path | None,
        typer.Option("--data", help="the data folder (OPTLAB_DATA); default: optlab-data beside the checkout"),
    ] = None,
) -> None:
    GIVEN["data"] = None
    if data is not None:
        GIVEN["data"] = data.expanduser().resolve()
        os.environ["OPTLAB_DATA"] = str(GIVEN["data"])  # this process and the ones it starts


GIVEN: dict[str, Path | None] = {"data": None}  # what the command line itself named (not the environment)


# --- the server --------------------------------------------------------------------------------------------------------
@app.command()
def serve(
    host: Annotated[
        str | None, typer.Option(help="default 127.0.0.1; beyond this machine only with OPTLAB_LAN=true")
    ] = None,
    port: Annotated[int | None, typer.Option(help="default 8765")] = None,
    workers: Annotated[int | None, typer.Option(help="process pool for Monte Carlo batches on NumPy (e.g. 6)")] = None,
    engine: Annotated[
        str | None, typer.Option(help="auto (the GPU, else every CPU thread, else NumPy), gpu, cpu, numpy")
    ] = None,
    store: Annotated[Path | None, typer.Option(help="the database file (default: the settings')")] = None,
    calibration: Annotated[Path | None, typer.Option(help="the starting market's calibration file")] = None,
    market: Annotated[
        str | None, typer.Option(help="start on this stored snapshot (default: OPTLAB_MARKET, else the calibration)")
    ] = None,
    log: Annotated[str, typer.Option(help="log level")] = "INFO",
    open_: Annotated[bool, typer.Option("--open", help="open the front (or the API docs page) in the browser")] = False,
    demo_data: Annotated[
        bool, typer.Option("--demo-data", help="serve the demo folder (made-up numbers; built when missing)")
    ] = False,
) -> None:
    """The API on http://127.0.0.1:8765/api/v1, with the job worker in its process unless OPTLAB_WORKER=external."""
    import uvicorn

    from optlab.core.log import configure
    from optlab.server.api import Desk
    from optlab.server.app import create_app
    from optlab.server.logs import to_files

    _choice("--engine", engine, ENGINES)
    configure(log)
    folder = _use_demo() if demo_data else None
    settings = _settings(host=host, port=port, workers=workers, engine=engine, calibration=calibration, database=store)
    if folder is not None:
        from optlab.server import demo

        demo.check_paths(folder, settings)  # the demo never serves the real database or lake
    to_files(settings, "api", log)
    desk = Desk(
        settings=settings, engine=settings.engine, workers=settings.workers, run_jobs=settings.worker == "embedded"
    )
    start_on = market or settings.market
    if start_on:
        desk.use_snapshot({"snapshot_id": start_on})
    eng = desk.health()["engine"]
    gpus = ", ".join(d["name"] for d in eng["devices"]) or "no CUDA device"
    say(f"data {settings.data}; database {desk.db.path}; market {desk.ctx.snapshot_id}")
    say(f"Monte Carlo engine: {eng['resolved']} ({gpus}; {eng['cpu_threads']} CPU threads)")
    if desk.worker is not None:
        say(f"jobs: the worker of this process, {desk.max_jobs} at a time")
    else:
        say("jobs: left to `optlab worker` (OPTLAB_WORKER=external, or another worker serves this database)")
    base = f"http://{settings.host}:{settings.port}"
    say(f"API on {base}/api/v1 (OpenAPI: /api/v1/openapi.json)")
    if open_:
        page = f"{base}/" if settings.frontend else (f"{base}/api/v1/docs" if settings.docs else None)
        if page is None:
            say("--open: no page to open yet (no front, OPTLAB_DOCS off): the API answers on " + base + "/api/v1")
        else:
            threading.Timer(1.5, webbrowser.open, args=(page,)).start()
    uvicorn.run(
        create_app(settings, desk),
        host=settings.host,
        port=settings.port,
        log_level=log.lower(),
        proxy_headers=settings.lan,
        server_header=False,
    )


def _use_demo() -> Path:
    """Point this process at the demo folder (`--data` when given on the command line, else the default one; never an
    OPTLAB_DATA of the environment, which names the real data), built first when missing."""
    from optlab.server import demo

    folder = Path(GIVEN["data"] or demo.default_folder()).resolve()
    demo.trust(folder)
    os.environ["OPTLAB_DATA"] = str(folder)
    if demo.info(folder) is None:
        say(f"building the demo data in {folder} (made-up numbers; a few minutes, once)")
        out = demo.build(folder, log=say)
        say(f"account demo, password {out['password']} (shown once; `optlab users password demo` changes it)")
    return folder


@app.command()
def worker(
    concurrency: Annotated[int | None, typer.Option(help="jobs at a time (default: the settings', 2)")] = None,
    engine: Annotated[str | None, typer.Option(help="auto, gpu, cpu, numpy")] = None,
    workers: Annotated[int | None, typer.Option(help="process pool for Monte Carlo batches on NumPy")] = None,
    store: Annotated[Path | None, typer.Option(help="the database file (default: the settings')")] = None,
    wait_lease: Annotated[
        float, typer.Option(help="seconds to wait for the lease of a worker that stopped without releasing it")
    ] = 30.0,
    log: Annotated[str, typer.Option(help="log level")] = "INFO",
) -> None:
    """The queue's worker alone: claims the jobs, runs them, fires the schedules (one worker per database)."""
    from optlab.core.log import configure
    from optlab.server.api import Desk
    from optlab.server.logs import to_files
    from optlab.server.worker import Worker

    _choice("--engine", engine, ENGINES)
    configure(log)
    settings = _settings(engine=engine, workers=workers, database=store, job_concurrency=concurrency)
    to_files(settings, "worker", log)
    desk = Desk(settings=settings, engine=settings.engine, workers=settings.workers, run_jobs=False)
    desk.shared = settings.lan  # the same rule as the api's for raw quotes and levels
    w = Worker(desk, role="external", concurrency=settings.job_concurrency)
    deadline = time.monotonic() + wait_lease
    while not w.start():
        if time.monotonic() > deadline:
            desk.close()
            raise RuntimeError("another worker serves this database: stop it first")
        say("another worker holds the lease: waiting for it to expire")
        time.sleep(2)
    say(f"worker {w.worker_id} on {desk.db.path}: {w.concurrency} job(s) at a time, Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        say("stopping: running jobs are asked to stop")
    finally:
        w.stop()
        desk.close()


# --- accounts ----------------------------------------------------------------------------------------------------------
Database_ = Annotated[Path | None, typer.Option("--database", help="the database file (default: the settings')")]
Stdin = Annotated[bool, typer.Option("--stdin", help="read the password from standard input (scripts and tests)")]


def _accounts(database: Path | None):
    from optlab.server.audit import Audit
    from optlab.server.persistence import Database
    from optlab.server.security import Accounts

    settings = _settings()
    db = Database(database or settings.database)
    return db, Accounts(db, settings), Audit(db)


def _password(stdin: bool) -> str:
    import getpass

    if stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("password (12 characters at least): ")
    if getpass.getpass("again: ") != first:
        raise ValueError("the two passwords differ")
    return first


def _roles(text: str | None) -> list[str]:
    return [r.strip() for r in (text or "").split(",") if r.strip()]


@users_app.command("create")
def users_create(
    name: str,
    roles: Annotated[str, typer.Option(help="comma-separated: admin, trader, structurer, risk, quant, viewer")] = "",
    database: Database_ = None,
    stdin: Stdin = False,
) -> None:
    """Create an account; the password is asked twice and never echoed (never an argument)."""
    db, accounts, audit = _accounts(database)
    try:
        user = accounts.create(name, _password(stdin), _roles(roles))
        audit.record(None, "cli_create_user", name, detail={"roles": sorted(user.roles)})
        say(f"created {name} ({', '.join(sorted(user.roles))})")
    finally:
        db.close()


@users_app.command("list")
def users_list(database: Database_ = None) -> None:
    """The accounts, their roles and their state."""
    db, accounts, _ = _accounts(database)
    try:
        for u in accounts.all():
            state = "disabled" if u["disabled"] else ("locked" if u["locked_until"] else "active")
            say(f"{u['name']:20s} {','.join(u['roles']):40s} {state}")
    finally:
        db.close()


@users_app.command("roles")
def users_roles(name: str, roles: str, database: Database_ = None) -> None:
    """Replace an account's roles (comma-separated); its sessions are closed."""
    db, accounts, audit = _accounts(database)
    try:
        wanted = _roles(roles)
        accounts.set_roles(name, wanted)
        audit.record(None, "cli_set_roles", name, detail={"roles": wanted})
        say(f"{name}: {', '.join(sorted(wanted))}; its sessions are closed")
    finally:
        db.close()


@users_app.command("password")
def users_password(name: str, database: Database_ = None, stdin: Stdin = False) -> None:
    """Set a new password; the account's sessions are closed."""
    db, accounts, audit = _accounts(database)
    try:
        accounts.set_password(name, _password(stdin))
        audit.record(None, "cli_set_password", name)
        say(f"{name}: new password; its sessions are closed")
    finally:
        db.close()


def _set_disabled(name: str, database: Path | None, disabled: bool) -> None:
    db, accounts, audit = _accounts(database)
    try:
        accounts.set_disabled(name, disabled)
        action = "disable" if disabled else "enable"
        audit.record(None, f"cli_{action}_user", name)
        say(f"{name}: {action}d")
    finally:
        db.close()


@users_app.command("disable")
def users_disable(name: str, database: Database_ = None) -> None:
    """Disable an account (its sessions are closed)."""
    _set_disabled(name, database, True)


@users_app.command("enable")
def users_enable(name: str, database: Database_ = None) -> None:
    """Enable a disabled account."""
    _set_disabled(name, database, False)


# --- backups, restoration, migration, jobs ----------------------------------------------------------------------------
@app.command()
def backup(
    lake: Annotated[bool, typer.Option("--lake", help="also a copy of the lake in <backups>/lake")] = False,
    store: Annotated[Path | None, typer.Option(help="the database file (default: the settings')")] = None,
) -> None:
    """A consistent copy of the database now (in <backups>/manual), and of the lake with --lake."""
    from optlab.server import backups
    from optlab.server.persistence import Database

    settings = _settings(database=store)
    db = Database(settings.database)
    try:
        target = backups.database_copy(db, Path(settings.backups) / "manual")
    finally:
        db.close()
    info = backups.check_database_copy(target)
    say(f"database: {target} ({info['bytes'] / 1e6:.1f} MB, revision {info['revision']})")
    if lake:
        out = backups.lake_snapshot(
            settings.lake, Path(settings.backups) / "lake", keep=settings.lake_backup_keep, log=say
        )
        say(f"lake: {Path(settings.backups) / 'lake' / out['folder']}")


def _latest_copy(root: Path) -> Path:
    """The newest copy of the database, nightly or manual (their names sort as their times)."""
    found = [*(root / "nightly").glob("optlab-*.sqlite"), *(root / "manual").glob("optlab-*.sqlite")]
    if not found:
        raise FileNotFoundError(f"no copy of the database in {root / 'nightly'} or {root / 'manual'}")
    return max(found, key=lambda p: p.name)


@app.command()
def restore(
    path: Annotated[
        str, typer.Argument(help="a copy of the database, or of the lake with --lake; `latest` for the last one")
    ],
    lake: Annotated[bool, typer.Option("--lake", help="restore the lake from one of its copies")] = False,
    store: Annotated[Path | None, typer.Option(help="the database to replace (default: the settings')")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="do not ask for confirmation")] = False,
) -> None:
    """Put a copy back in place of the database (or of the lake): checked first; what it replaces is kept aside."""
    from optlab.server import backups

    settings = _settings(database=store)
    root = Path(settings.backups)
    if lake:
        copies = backups.lake_snapshots(root / "lake")
        snapshot = (copies[-1] if copies else None) if path == "latest" else Path(path)
        if snapshot is None:
            raise FileNotFoundError(f"no copy of the lake in {root / 'lake'}")
        problems = backups.verify_lake_snapshot(snapshot)
        if problems:
            raise ValueError(f"{snapshot} cannot be restored: " + "; ".join(problems[:5]))
        if not yes:
            typer.confirm(
                f"replace the lake {settings.lake} by {snapshot.name} (the current one is moved aside)?", abort=True
            )
        out = backups.restore_lake(snapshot, settings.lake)
        say(f"lake restored from {snapshot.name}: {out['files']} files; the previous one is {out['previous_moved_to']}")
        return
    source = _latest_copy(root) if path == "latest" else Path(path)
    info = backups.check_database_copy(source)
    say(f"{source.name}: revision {info['revision']}, {info.get('trades', 0)} trades, {info.get('users', 0)} accounts")
    if not yes:
        typer.confirm(f"replace {settings.database} by this copy (the current one is saved first)?", abort=True)
    out = backups.restore_database(source, settings.database, root)
    say(f"restored {settings.database} (revision {out['revision_now']}); the previous one is {out['saved_as']}")


@app.command()
def migrate(
    v0: Annotated[
        Path | None, typer.Option(help="import a v0 folder (desk.sqlite, risk.sqlite, market/, replay/)")
    ] = None,
    store: Annotated[Path | None, typer.Option(help="the database file (default: the settings')")] = None,
) -> None:
    """Bring the database to this code's schema revision (Alembic); with --v0, import a v0 store first (idempotent)."""
    from optlab.server.persistence import Database

    settings = _settings(database=store)
    if v0 is not None:
        from optlab.server.persistence.migrate_v0 import migrate as import_v0

        out = import_v0(v0, settings.database)
        say(f"v0 folder {v0} imported: {out}")
    db = Database(settings.database)
    try:
        say(f"{db.path}: revision {db.revision()} (head {db.head()})")
    finally:
        db.close()


def _queue(store: Path | None):
    from optlab.server.jobs import JobQueue
    from optlab.server.persistence import Database

    db = Database(store or _settings().database)
    return db, JobQueue(db)


@jobs_app.command("list")
def jobs_list(
    store: Annotated[Path | None, typer.Option(help="the database file")] = None,
    limit: Annotated[int, typer.Option(help="newest first")] = 20,
) -> None:
    """The newest jobs of the database (every user's), without their results."""
    db, queue = _queue(store)
    try:
        for j in queue.history(everyone=True, limit=limit):
            progress = "" if j.get("progress") is None else f" {100 * j['progress']:.0f} %"
            say(f"{j['id']}  {j['kind']:14s} {j['state']:10s}{progress}  {j['submitted_at']}  {j.get('step') or ''}")
    finally:
        db.close()


@jobs_app.command("show")
def jobs_show(job_id: str, store: Annotated[Path | None, typer.Option(help="the database file")] = None) -> None:
    """One job, its result or its error (JSON)."""
    import json

    db, queue = _queue(store)
    try:
        say(json.dumps(queue.get(job_id), indent=1, default=str))
    finally:
        db.close()


@jobs_app.command("wait")
def jobs_wait(job_id: str, store: Annotated[Path | None, typer.Option(help="the database file")] = None) -> None:
    """Follow a job's progress until it ends (the worker that serves the database runs it)."""
    db, queue = _queue(store)
    try:
        last = None
        while True:
            j = queue.get(job_id)
            line = f"{j['state']} {100 * (j.get('progress') or 0):.0f} % {j.get('step') or ''}"
            if line != last:
                say(line)
                last = line
            if j["state"] not in ("queued", "running"):
                break
            time.sleep(1)
        if j["state"] != "done":
            raise RuntimeError(f"job {job_id} {j['state']}: {(j.get('error') or {}).get('message')}")
    finally:
        db.close()


@jobs_app.command("cancel")
def jobs_cancel(job_id: str, store: Annotated[Path | None, typer.Option(help="the database file")] = None) -> None:
    """Cancel a queued job, or ask a running one to stop at its next batch."""
    db, queue = _queue(store)
    try:
        say(f"{job_id}: {queue.cancel(job_id)}")
    finally:
        db.close()


# --- the API document and the checks ------------------------------------------------------------------------------------
@api_app.command("export")
def api_export(
    out: Annotated[Path | None, typer.Option(help="default: api/openapi.json of the checkout")] = None,
) -> None:
    """Write the OpenAPI document of the routes (after changing a route)."""
    from optlab.server import openapi

    target = out or openapi.DEFAULT
    doc = openapi.document()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(openapi.render(doc), encoding="utf-8", newline="\n")
    say(f"wrote {target} ({len(doc['paths'])} paths)")


@api_app.command("check")
def api_check(
    out: Annotated[Path | None, typer.Option(help="default: api/openapi.json of the checkout")] = None,
) -> None:
    """Exit 1 when the versioned document is not the code's."""
    import json

    from optlab.server import openapi

    target = out or openapi.DEFAULT
    current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    if current != json.loads(openapi.render(openapi.document())):
        print(f"{target} is not the code's document: run optlab api export", file=sys.stderr)
        raise typer.Exit(1)
    say(f"{target} is up to date")


PASS = {"allow_extra_args": True, "ignore_unknown_options": True}  # what follows goes to pytest or golden.py


def _repo() -> Path:
    from optlab.server.settings import repository_root

    repo = repository_root()
    if repo is None:
        raise RuntimeError("the checks run from a checkout of the repository (an installed wheel has no tests)")
    return repo


def _run(argv: list[str]) -> None:
    say("$ " + " ".join(argv[1:]))
    code = subprocess.run(argv, cwd=_repo(), check=False).returncode  # noqa: S603 (this interpreter, fixed arguments)
    if code:
        raise typer.Exit(code)


@check_app.command("fast", context_settings=PASS)
def check_fast(ctx: typer.Context) -> None:
    """What the CI runs: no slow, GPU, lake or engine test, on every core (a few minutes)."""
    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not slow and not gpu and not lake and not native",
            "-n",
            "auto",
            "--dist",
            "loadfile",
            *ctx.args,
        ]
    )


@check_app.command("full", context_settings=PASS)
def check_full(ctx: typer.Context) -> None:
    """The whole suite, with the real lake, the engine, the GPU and node when present (about 30 minutes)."""
    _run([sys.executable, "-m", "pytest", "-n", "4", "--dist", "loadfile", *ctx.args])


@check_app.command("gpu", context_settings=PASS)
def check_gpu(ctx: typer.Context) -> None:
    """The engine's tests (CPU and GPU), then the golden numbers of the engine."""
    _run([sys.executable, "-m", "pytest", "-m", "gpu or native", *ctx.args])
    _run(
        [
            sys.executable,
            "tests/golden/golden.py",
            "check",
            "--local",
            "--group",
            "native_cpu",
            "native_gpu",
            "presets_spx_gpu",
        ]
    )


@check_app.command("golden", context_settings=PASS)
def check_golden(
    ctx: typer.Context,
    local: Annotated[bool, typer.Option("--local", help="with the engine, lake and showcase groups")] = False,
) -> None:
    """The golden numbers, bit for bit (tests/golden/golden.py check; more options after the command)."""
    _run([sys.executable, "tests/golden/golden.py", "check", *(["--local"] if local else []), *ctx.args])


# --- the lake ----------------------------------------------------------------------------------------------------------
Lake_ = Annotated[Path | None, typer.Option("--lake", help="the lake folder (default: the settings')")]
Only = Annotated[list[str] | None, typer.Option("--only", help="steps, comma-separated or repeated")]


def _as_job(kind: str, params: dict, log=say) -> str:
    """Submit a job of `kind` to the database's queue for the worker that serves it; returns its id."""
    from optlab.server.api import Desk

    desk = Desk(settings=_settings(), run_jobs=False)
    try:
        out = desk._submit(kind, params)
    finally:
        desk.close()
    log(
        f"job {out['job']} queued{' (the same one, still running)' if out.get('reused') else ''}: optlab jobs wait {out['job']}"
    )
    return out["job"]


@lake_app.command("update")
def lake_update(
    only: Only = None,
    lake: Lake_ = None,
    yahoo_chains: Annotated[bool, typer.Option(help="one snapshot of the day's Yahoo chains (US hours only)")] = False,
    import_sample: Annotated[str | None, typer.Option(help="zip of the historicaldata.net sample")] = None,
    dolt: Annotated[str | None, typer.Option(help="folder of the cloned DoltHub options database")] = None,
    as_job: Annotated[bool, typer.Option("--as-job", help="leave the public sources' update to the worker")] = False,
) -> None:
    """Update the lake from the public sources and print the quality report."""
    if as_job:
        if lake or yahoo_chains or import_sample or dolt:
            raise ValueError("--as-job updates the public sources of the settings' lake only")
        _as_job("lake", {"only": _split(only)})
        return
    from optlab.server.tools import lake_update as tool

    tool.main(_split(only), lake, yahoo_chains, import_sample, dolt)


@lake_app.command("derive")
def lake_derive(only: Only = None, lake: Lake_ = None) -> None:
    """The derived tables: curves, carry, bank, FX, surfaces, correlation, pairs, observed fits, parity."""
    from optlab.server.tools import lake_derive as tool

    tool.main(_split(only), lake)


@lake_app.command("fx-check")
def lake_fx_check(lake: Lake_ = None, n_paths: Annotated[int, typer.Option(help="paths per seed")] = 100_000) -> None:
    """FX against H.10 and CME futures, and the size of the quanto adjustment: <reports>/fx_check.md."""
    from optlab.server.tools import fx_check

    fx_check.main(lake, n_paths)


@lake_app.command("surface-test")
def lake_surface_test(
    step: Annotated[int, typer.Option(help="every n-th day of the sample")] = 5, lake: Lake_ = None
) -> None:
    """Reconstruction of SPX from volatility indices against real chains: <results>/surface_reconstruction.json."""
    from optlab.server.tools import surface_test

    surface_test.main(step, lake)


@lake_app.command("regime-test")
def lake_regime_test(step: Annotated[int, typer.Option(help="every n-th day of the sample")] = 8) -> None:
    """The same by level of the VIX, the crash of 2020 held out: <results>/surface_regimes.json."""
    from optlab.server.tools import regime_test

    regime_test.main(step)


@lake_app.command("replay")
def lake_replay(
    action: Annotated[str, typer.Argument(help="cost (what a valuation costs) or run (the products over their life)")],
    every: Annotated[int, typer.Option(help="revalue every n-th day")] = 1,
    paths: Annotated[int, typer.Option(help="Monte Carlo paths")] = 20_000,
    model: Annotated[str, typer.Option(help="lv, slv, heston or bs")] = "lv",
    only: Annotated[str | None, typer.Option(help="one product: BRC-2020 or PHX-2022")] = None,
    leverage_every: Annotated[int, typer.Option(help="recalibrate the SLV leverage every n-th valuation")] = 1,
    out: Annotated[str | None, typer.Option(help="file in <results> (default replay.json)")] = None,
    costs: Annotated[
        bool, typer.Option(help="deal at the desk's client price (the quote's lines become reserves)")
    ] = False,
) -> None:
    """Replay products on the lake's real closes: <results>/replay.json (performances, never a level)."""
    from optlab.server.tools import replay

    replay.main(action, every, paths, model, only, leverage_every, out, costs)


# --- risk --------------------------------------------------------------------------------------------------------------
Store_ = Annotated[
    Path | None, typer.Option("--store", help="the desk database (default: the showcase's, else the settings')")
]
Precision = Annotated[str, typer.Option(help="fast, standard, precise or max")]
Engine_ = Annotated[str, typer.Option(help="auto, gpu, cpu or numpy")]
AsJob = Annotated[bool, typer.Option("--as-job", help="leave it to the worker that serves the database")]


def _eod_job(store: Path | None, start: str, end: str, precision: str, greeks: bool) -> None:
    from optlab.server.api import Desk
    from optlab.server.tools.risk_console import default_store

    desk = Desk(store or default_store(), run_jobs=False)
    try:
        out = desk.risk.pnl_eod({"from": start, "to": end, "options": {"precision": precision}, "greeks": greeks})
    finally:
        desk.close()
    say(f"job {out['job']} queued: optlab jobs wait {out['job']}")


@risk_app.command("eod")
def risk_eod(
    date: Annotated[str, typer.Option(help="the day, YYYY-MM-DD")],
    store: Store_ = None,
    precision: Precision = "standard",
    engine: Engine_ = "auto",
    no_greeks: Annotated[bool, typer.Option("--no-greeks", help="values only")] = False,
    as_job: AsJob = False,
) -> None:
    """The EOD run of a day on the lake's closes (stored; asked again, it is read back)."""
    _choice("--engine", engine, ENGINES)
    if as_job:
        return _eod_job(store, date, date, precision, not no_greeks)
    from optlab.server.tools import risk_console

    risk_console.eod_runs("eod", store, date=date, precision=precision, engine=engine, greeks=not no_greeks)


@risk_app.command("backfill")
def risk_backfill(
    start: Annotated[str, typer.Option("--from", help="first day")],
    end: Annotated[str, typer.Option("--to", help="last day")],
    store: Store_ = None,
    precision: Precision = "standard",
    engine: Engine_ = "auto",
    no_greeks: Annotated[bool, typer.Option("--no-greeks", help="values only")] = False,
    as_job: AsJob = False,
) -> None:
    """The EOD runs of every day between two dates (those already stored are read back)."""
    _choice("--engine", engine, ENGINES)
    if as_job:
        return _eod_job(store, start, end, precision, not no_greeks)
    from optlab.server.tools import risk_console

    risk_console.eod_runs(
        "backfill", store, start=start, end=end, precision=precision, engine=engine, greeks=not no_greeks
    )


@risk_app.command("pnl")
def risk_pnl(
    start: Annotated[str, typer.Option("--from", help="first day")],
    end: Annotated[str, typer.Option("--to", help="last day")],
    store: Store_ = None,
) -> None:
    """The explained P&L between two stored EOD runs: the cascade, the Greeks' prediction, the unexplained."""
    from optlab.server.tools import risk_console

    risk_console.eod_runs("pnl", store, start=start, end=end)


@risk_app.command("var")
def risk_var(
    store: Store_ = None,
    precision: Precision = "standard",
    engine: Engine_ = "auto",
    workers: Annotated[int, typer.Option(help="processes of the full revaluation")] = 5,
    days: Annotated[int, typer.Option(help="historical window")] = 500,
    market: Annotated[str | None, typer.Option(help="snapshot of the database (default: the one in use)")] = None,
    as_job: AsJob = False,
) -> None:
    """Historical VaR by full revaluation: 1 day, 10 days, stressed."""
    _choice("--engine", engine, ENGINES)
    from optlab.server.tools import risk_console

    risk_console.queued("var", store, precision, engine, workers, days, market, as_job)


@risk_app.command("ladder")
def risk_ladder(
    store: Store_ = None,
    precision: Precision = "standard",
    engine: Engine_ = "auto",
    market: Annotated[str | None, typer.Option(help="snapshot of the database (default: the one in use)")] = None,
    as_job: AsJob = False,
) -> None:
    """The spot ladder under each vol rule (sticky moneyness, sticky strike, realistic)."""
    _choice("--engine", engine, ENGINES)
    from optlab.server.tools import risk_console

    risk_console.queued("ladder", store, precision, engine, market=market, as_job=as_job)


# --- calibration, structured, demo, bench ------------------------------------------------------------------------------
@calib_app.command("run")
def calib_run(
    source: Annotated[
        str, typer.Option(help="simulated, or spx (needs `optlab calib download-spx` first)")
    ] = "simulated",
    mix: Annotated[float, typer.Option(help="SLV mixing fraction")] = 0.5,
    particles: Annotated[int, typer.Option(help="particles of the leverage")] = 200_000,
    paths: Annotated[int, typer.Option(help="validation paths")] = 400_000,
    skip_sweep: Annotated[bool, typer.Option(help="no mixing-fraction sweep")] = False,
    replot: Annotated[bool, typer.Option(help="redraw the charts from the saved JSON only")] = False,
) -> None:
    """SSVI -> SVI -> Heston -> SLV: <results>/<source>_calibration.json and <reports>/figures (extra `reports`)."""
    _choice("--source", source, ("simulated", "spx"))
    from optlab.server.tools import calibration

    calibration.main(source, mix, particles, paths, skip_sweep, replot)


@calib_app.command("report")
def calib_report() -> None:
    """<reports>/calibration.md from the calibrations."""
    from optlab.server.tools import calibration_report

    calibration_report.main()


@calib_app.command("download-spx")
def calib_download_spx() -> None:
    """A snapshot of the SPX option chain in <raw> (personal use, never published; US hours)."""
    from optlab.marketdata.chains import download_chain

    say(download_chain(out_dir=_settings().raw))


@structured_app.command("examples")
def structured_examples(
    paths: Annotated[int, typer.Option(help="Monte Carlo paths")] = 200_000,
    batch: Annotated[int, typer.Option(help="paths per batch")] = 25_000,
    quick: Annotated[bool, typer.Option(help="20 000 paths, for a smoke run")] = False,
) -> None:
    """Example quotes, the autocall model-risk table and timings, on the settings' calibration."""
    from optlab.server.tools import structured_examples as tool

    tool.main(paths, batch, quick)


@structured_app.command("pricing-report")
def structured_pricing_report(
    day: Annotated[str, typer.Option(help="the lake's day")] = "2026-11-04",
    paths: Annotated[int, typer.Option(help="Monte Carlo paths")] = 100_000,
    workers: Annotated[int, typer.Option(help="processes")] = 6,
) -> None:
    """The desk price on the real lake: <results>/pricing_examples.json (in % of notional)."""
    from optlab.server.tools import pricing_report

    pricing_report.main(day, paths, workers)


@demo_app.command("data")
def demo_data_cmd(
    to: Annotated[Path | None, typer.Option(help="the folder (default: ~/.cache/optlab/demo)")] = None,
    password_stdin: Annotated[
        bool, typer.Option("--password-stdin", help="the demo account's password from standard input")
    ] = False,
    rebuild: Annotated[bool, typer.Option(help="build it again even when it is there")] = False,
    n_paths: Annotated[int, typer.Option(help="paths of the trades' strikes")] = 4_000,
) -> None:
    """Build the demo folder (made-up numbers only): then `optlab --data <folder> serve` or `optlab serve --demo-data`."""
    from optlab.server import demo

    password = sys.stdin.readline().rstrip("\n") if password_stdin else None
    out = demo.build(to, password=password, n_paths=n_paths, rebuild=rebuild, log=say)
    say(f"demo data in {out['folder']}: market {out['market']}, trades {', '.join(out['trades'])}")
    if "password" in out:
        say(f"account demo, password {out['password']} (shown once)")
    elif rebuild or password_stdin:
        say("account demo, with the password given")
    else:
        say("already built: the demo account keeps its password (`optlab users password demo` changes it)")


@demo_app.command("seed-showcase")
def demo_seed_showcase(
    store: Annotated[Path | None, typer.Option(help="default: <data>/showcase/optlab.sqlite")] = None,
    day: Annotated[str | None, typer.Option(help="valuation date (default: the last SPX close in the lake)")] = None,
    n_paths: Annotated[int, typer.Option(help="paths of each strike")] = 20_000,
) -> None:
    """The showcase book: a dozen products sold on past dates of the real lake, in their own database."""
    from optlab.server.tools import showcase

    showcase.main(store, day, n_paths)


@demo_app.command("widget-data")
def demo_widget_data() -> None:
    """docs/pricer-data.js from the SPX calibration (until session 2 replaces docs/lab.js)."""
    from optlab.server.tools import widget_data

    widget_data.main()


@bench_app.command("accel")
def bench_accel(
    paths: Annotated[int, typer.Option(help="paths per case")] = 1_000_000,
    batch: Annotated[int, typer.Option(help="paths per batch")] = 50_000,
    workers: Annotated[str, typer.Option(help="NumPy process counts, comma-separated")] = "1,6",
    cases: Annotated[str | None, typer.Option(help="cases, comma-separated (default: all)")] = None,
    engines: Annotated[str, typer.Option(help="engines, comma-separated")] = "numpy,cpu,gpu",
    no_leverage: Annotated[bool, typer.Option("--no-leverage", help="skip the SLV leverage timings")] = False,
    desk: Annotated[
        str | None, typer.Option(help="precisions of a full desk quote to time, e.g. standard,precise")
    ] = None,
    out: Annotated[str | None, typer.Option(help="a JSON summary")] = None,
) -> None:
    """NumPy against the CPU and GPU engines: simulation, evaluation, leverage (run it on an idle machine)."""
    from optlab.server.tools import bench_accel as tool

    tool.main(paths, batch, workers, cases or ",".join(tool.CASES), engines, no_leverage, desk, out)


@bench_app.command("numpy")
def bench_numpy(
    out: Annotated[Path | None, typer.Option(help="default: packages/native/cuda/bench/numpy_results.csv")] = None,
) -> None:
    """NumPy timings for the CUDA benchmark table (bench.exe reads them)."""
    from optlab.server.tools import bench_numpy as tool

    tool.main(out)


@app.command()
def version() -> None:
    """The versions of the six packages and the Monte Carlo engines this machine has."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as dist

    for name in (
        "optlab-core",
        "optlab-native",
        "optlab-structured",
        "optlab-marketdata",
        "optlab-risk",
        "optlab-server",
    ):
        try:
            say(f"{name:18s} {dist(name)}")
        except PackageNotFoundError:
            say(f"{name:18s} not installed")
    from optlab.core import engines

    native = engines.compiled()
    if native is None or not native.available():
        say(f"engines: numpy (no compiled engine); default {engines.default_engine()}")
    else:
        gpu = "gpu, " if native.gpu_available() else ""
        say(f"engines: {gpu}cpu ({native.cpu_threads()} threads), numpy; default {engines.default_engine()}")


# --- entry points ------------------------------------------------------------------------------------------------------
def run(argv: list[str] | None = None) -> int:
    """Run the command line on `argv` (default: this process's) and return its exit code; a library error prints its
    message, not a trace."""
    try:
        app(args=argv, prog_name="optlab")
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            return int(exc.code or 0)
        print(exc.code, file=sys.stderr)
        return 1
    except FAILURES as exc:
        message = str(exc).strip("'\"") if isinstance(exc, KeyError) else str(exc)
        print(f"error: {message}", file=sys.stderr)
        return 1
    except Exception as exc:  # the API's own errors (ApiError) and the like: their message, not a trace
        if type(exc).__name__ in ("ApiError", "ScriptError", "EngineError"):
            print(f"error: {exc}", file=sys.stderr)
            return 1
        raise
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
