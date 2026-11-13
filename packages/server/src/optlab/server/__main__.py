"""Start the API: python -m optlab.server [--port 8765] [--workers N] [--engine auto] [--store FILE] [--market ID]

It listens on 127.0.0.1 (`Settings.host`); another host needs the LAN profile (`OPTLAB_LAN=true`, with Luca's
agreement, ADR-0002). The shared token and the `--tunnel` link of v0 are gone: accounts and sessions replace them
(`python -m optlab.server.users create <name> --roles admin`). `optlab serve` in A7.
"""

from __future__ import annotations

import argparse

import uvicorn

from optlab.core.log import configure
from optlab.server.api import Desk
from optlab.server.app import create_app
from optlab.server.logs import to_files
from optlab.server.settings import Settings


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m optlab.server", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--host", default=None, help="default 127.0.0.1; beyond this machine only with OPTLAB_LAN=true")
    ap.add_argument("--port", type=int, default=None, help="default 8765")
    ap.add_argument("--workers", type=int, default=None, help="process pool for Monte Carlo batches on NumPy (e.g. 6)")
    ap.add_argument(
        "--engine",
        default=None,
        choices=("auto", "gpu", "cpu", "numpy"),
        help="Monte Carlo paths: auto (the GPU if any, else every CPU thread, else NumPy) is the default",
    )
    ap.add_argument("--store", help="the database file (default: the settings' database)")
    ap.add_argument("--calibration", help="the starting market's calibration file (default: the settings')")
    ap.add_argument("--market", help="start on this stored snapshot instead of the calibration")
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    configure(args.log)
    settings = Settings.load(
        host=args.host,
        port=args.port,
        workers=args.workers,
        engine=args.engine,
        calibration=args.calibration,
        database=args.store,
    )
    to_files(settings, "api", args.log)
    desk = Desk(
        settings=settings, engine=settings.engine, workers=settings.workers, run_jobs=settings.worker == "embedded"
    )
    if args.market:
        desk.use_snapshot({"snapshot_id": args.market})
    eng = desk.health()["engine"]
    gpus = ", ".join(d["name"] for d in eng["devices"]) or "no CUDA device"
    print(f"data {settings.data}; database {desk.db.path}; market {desk.ctx.snapshot_id}")
    print(f"Monte Carlo engine: {eng['resolved']} ({gpus}; {eng['cpu_threads']} CPU threads)")
    if desk.worker is not None:
        print(f"jobs: the worker of this process, {desk.max_jobs} at a time")
    else:
        print("jobs: left to `python -m optlab.server.worker` (OPTLAB_WORKER=external, or another worker is running)")
    print(f"API on http://{settings.host}:{settings.port}/api/v1 (OpenAPI: /api/v1/openapi.json)")
    uvicorn.run(
        create_app(settings, desk),
        host=settings.host,
        port=settings.port,
        log_level=args.log.lower(),
        proxy_headers=settings.lan,
        server_header=False,
    )


if __name__ == "__main__":
    main()
