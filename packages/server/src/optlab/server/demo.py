"""The demo data (handoff §7.7): a whole data folder made of made-up numbers, to develop the front and run end-to-end
tests without any real data. `optlab demo data [--to DIR]` builds it, `optlab serve --demo-data` builds it when
missing and serves it.

What it holds, all from fixed seeds:
- `lake/`: the synthetic lake of `optlab.marketdata.synthetic` (seven names, 2019-01-01 to 2021-06-30, with the derived
  curves, carry, bank and reconstructed surfaces of SPX and NDX);
- `archive/simulated_calibration.json`: the simulated calibration (a known Heston model plus noise), the desk's
  starting market;
- `store/optlab.sqlite`: the market of the last day stored and in use, and a small book struck on its own past
  markets, with the synthetic closes as fixings;
- `config/optlab.env`: the paths and the market above, so `OPTLAB_DATA=<folder>` is all a process needs;
- an account `demo` (admin) whose password is printed once (or read from standard input), never written to a file.

Nothing in it comes from a vendor: the folder may go into a CI artefact or a demo build.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from optlab.core.log import get_logger

LOG = get_logger(__name__)
START, END = "2019-01-01", "2021-06-30"
READY = "DEMO.json"  # written last: a folder without it is an unfinished build
MARKER = "optlab-demo"  # written first: a folder that has it may be rebuilt


def default_folder() -> Path:
    """In the user's own profile, not a shared temporary folder (where another account could plant one first)."""
    return Path.home() / ".cache" / "optlab" / "demo"


def paths(folder: Path) -> dict[str, Path]:
    """Every path of the demo's settings, inside its folder: nothing of the real data is read or written."""
    folder = Path(folder)
    return {
        "data": folder,
        "store": folder / "store",
        "database": folder / "store" / "optlab.sqlite",
        "lake": folder / "lake",
        "results": folder / "archive",
        "calibration": folder / "archive" / "simulated_calibration.json",
        "reports": folder / "reports",
        "raw": folder / "raw",
        "logs": folder / "logs",
        "backups": folder / "backups",
    }


def check_paths(folder: Path, settings) -> None:
    """Refuse settings whose database or lake is not the demo's (an OPTLAB_DATABASE or OPTLAB_LAKE of the
    environment, which names the real ones)."""
    root = Path(folder).resolve()
    for name in ("database", "lake", "backups"):
        if not Path(getattr(settings, name)).resolve().is_relative_to(root):
            raise RuntimeError(
                f"demo data: the settings' {name} ({getattr(settings, name)}) is outside {root}: unset "
                f"OPTLAB_{name.upper()} first"
            )


def trust(folder: Path) -> None:
    """On a shared Unix machine, a folder is used only if it is this user's and nobody else may write it."""
    import os
    import stat

    folder = Path(folder)
    if os.name != "posix" or not folder.exists():
        return
    st = folder.stat()
    if st.st_uid != os.getuid() or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):  # pyright: ignore[reportAttributeAccessIssue]
        raise PermissionError(f"{folder} belongs to another user or others may write it: give another folder")


def deals():
    from optlab.server.tools.showcase import Deal

    return (
        Deal("DEMO-01", "phoenix", ("SPX",), "2020-06-15", 24, 5e6, "Demo bank A", {"coupon.barrier": 0.8}),
        Deal("DEMO-02", "barrier_reverse_convertible", ("SPX",), "2020-09-15", 18, 2e6, "Demo network B"),
        Deal("DEMO-03", "capital_protected", ("SPX",), "2019-09-16", 36, 8e6, "Demo insurer C"),
        Deal(
            "DEMO-04",
            "reverse_convertible",
            ("SPX",),
            "2021-03-15",
            12,
            1.5e6,
            "Demo bank A",
            {"downside.barrier": 0.9},
            quantity=0.5,
        ),
    )


def info(folder: Path) -> dict | None:
    """What a finished demo folder holds (None when it is not one)."""
    path = Path(folder) / READY
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def build(
    folder: Path | None = None,
    password: str | None = None,
    n_paths: int = 4_000,
    rebuild: bool = False,
    log: Callable[..., None] = LOG.info,
) -> dict:
    """Build the demo folder (see the module's docstring); returns its `info`, with the password when it was generated
    here. A finished folder is kept as it is unless `rebuild`; a folder that is neither empty nor a demo is refused."""
    import shutil

    from optlab.server.settings import Settings

    folder = Path(folder or default_folder()).resolve()
    trust(folder)
    done = info(folder)
    if done and not rebuild:
        return done
    if folder.exists() and any(folder.iterdir()) and not (folder / MARKER).exists():
        raise FileExistsError(f"{folder} is not empty and not a demo folder: give another one")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / MARKER).write_text("made-up numbers only (optlab.server.demo)\n", encoding="utf-8")
    t0 = time.perf_counter()

    from optlab.marketdata.synthetic import synthetic_lake

    synthetic_lake(folder / "lake", START, END)
    log(f"synthetic lake {START} to {END}: {time.perf_counter() - t0:.0f} s")

    from optlab.server.tools import calibration

    t1 = time.perf_counter()
    calib = calibration.main(
        source="simulated",
        particles=50_000,
        paths=10_000,  # the validation and the model-risk table are coarse here: a demo, not a measurement
        exotic_paths=20_000,
        skip_sweep=True,
        results=folder / "archive",
        plots=False,
        log=log,
    )
    cal = json.loads(calib.read_text(encoding="utf-8"))  # the simulated market has no date: the lake's last day
    cal["meta"].update(snapshot=f"{END}T16:00:00", note="demo data: a simulated Heston market plus noise")
    calib.write_text(json.dumps(cal, indent=1), encoding="utf-8")
    log(f"simulated calibration: {time.perf_counter() - t1:.0f} s")
    # built here, not by Settings.load: an OPTLAB_DATA of the environment would bring the real folder's optlab.env
    settings = Settings(_env_file=None, **{**paths(folder), "calibration": calib})  # pyright: ignore[reportCallIssue]
    check_paths(folder, settings)

    from optlab.marketdata.lake import Lake
    from optlab.server.api import Desk
    from optlab.server.audit import Audit
    from optlab.server.security import Accounts
    from optlab.server.tools.showcase import seed

    desk = Desk(settings=settings, run_jobs=False)
    try:
        added = seed(
            desk, Lake(settings.lake), dt.date.fromisoformat(END), deals(), n_paths=n_paths, log=log, look_back=()
        )
        sid = desk.ctx.snapshot_id
        generated = password is None
        password = password or secrets.token_urlsafe(15)
        Accounts(desk.db, settings).create("demo", password, ["admin"])
        Audit(desk.db).record(None, "demo_create_user", "demo", detail={"roles": ["admin"]})
    finally:
        desk.close()
    config = folder / "config"
    config.mkdir(exist_ok=True)
    (config / "optlab.env").write_text(
        "# optlab demo data: made-up numbers only (optlab demo data)\n"
        f"OPTLAB_RESULTS={folder / 'archive'}\n"
        f"OPTLAB_CALIBRATION={calib}\n"
        f"OPTLAB_MARKET={sid}\n",
        encoding="utf-8",
    )
    out = {
        "folder": str(folder),
        "lake": [START, END],
        "market": sid,
        "trades": added,
        "account": "demo",
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.perf_counter() - t0, 1),
    }
    (folder / READY).write_text(json.dumps(out, indent=1), encoding="utf-8")
    log(f"demo data in {folder}: {len(added)} trades, market {sid}, {out['seconds']:.0f} s")
    return {**out, **({"password": password} if generated else {})}
