"""End-of-day risk runs of a desk database on the lake's closes, the explained P&L, the VaR and the ladder, from the
console (`optlab risk ...`).

    optlab risk eod --date 2026-11-04 [--store PATH]
    optlab risk backfill --from 2026-10-01 --to 2026-11-04 [--no-greeks]
    optlab risk pnl --from 2026-10-01 --to 2026-11-04
    optlab risk var [--workers 5] [--days 500]
    optlab risk ladder

Runs go to the risk tables of the desk's database (by default the showcase's when it exists, else the settings');
`--precision max` takes 2 million paths on the engine. Every run is stored with its code version and parameters, and a
run asked again with the same key is read back: a backfill started twice computes nothing the second time. VaR and
ladder go through the job queue, as the API's do: run here by a worker of this process, or with `--as-job` left to the
worker that serves the database.
"""

from __future__ import annotations

import time
from pathlib import Path

from optlab.server.settings import Settings
from optlab.server.tools import say


def default_store() -> Path:
    machine = Settings.load()
    showcase = machine.data / "showcase" / "optlab.sqlite"
    return showcase if showcase.exists() else machine.database


def _settings(precision: str, engine: str):
    from optlab.core import engines
    from optlab.risk import RiskSettings
    from optlab.server.api import PRECISION

    if precision not in PRECISION:
        raise ValueError(f"--precision: {precision!r} is not one of {', '.join(sorted(PRECISION))}")
    engines.set_default(engine)
    n_paths, batch = PRECISION[precision]
    return RiskSettings(n_paths=n_paths, batch=batch, engine=engine)


def eod_runs(
    command: str,
    store: Path | None = None,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    precision: str = "standard",
    engine: str = "auto",
    greeks: bool = True,
    log=say,
) -> None:
    """`eod`, `backfill` and `pnl`: the library's runs on the trades of the database, in this process."""
    from optlab.marketdata.lake import Lake
    from optlab.risk import pnl as P
    from optlab.server.persistence import Database
    from optlab.server.persistence.desk import DeskRepository
    from optlab.server.persistence.risk import RiskRepository

    t0 = time.perf_counter()
    settings = _settings(precision, engine)
    db = Database(Path(store or default_store()))
    try:
        risk = RiskRepository(db)
        lake = Lake(Settings.load().lake)
        trades = DeskRepository(db).trades()
        if command == "eod":
            if not date:
                raise ValueError("eod needs --date")
            r = P.eod(lake, trades, date, risk, settings, greeks=greeks)
            log(
                f"{r['run_id']}{' (reused)' if r.get('reused') else ''}: {len(r['positions'])} trades, "
                f"value {r['value']:,.0f} ± {r['value_se']:,.0f}"
            )
        elif command == "backfill":
            if not (start and end):
                raise ValueError("backfill needs --from and --to")
            P.backfill(lake, trades, start, end, risk, settings, greeks=greeks, log=log)
        else:
            if not (start and end):
                raise ValueError("pnl needs --from and --to")
            c = P.cumulative(risk, start, end)
            log(f"P&L {c['from']} to {c['to']}: {c['total']:,.0f}")
            for s in c["order"]:
                log(
                    f"  {s:10s} cascade {c['steps'][s]:>14,.0f}   greeks {c['predicted'][s]:>14,.0f}   "
                    f"unexplained {c['unexplained'][s]:>12,.0f}"
                )
    finally:
        db.close()
    log(f"{time.perf_counter() - t0:.0f} s")


def queued(
    command: str,
    store: Path | None = None,
    precision: str = "standard",
    engine: str = "auto",
    workers: int = 5,
    days: int = 500,
    market: str | None = None,
    as_job: bool = False,
    log=say,
) -> str | None:
    """`var` and `ladder`: a job of the database's queue, run by a worker of this process (or, with `as_job`, left to
    the worker that serves the database: its id is returned)."""
    from optlab.server.api import Desk

    t0 = time.perf_counter()
    _settings(precision, engine)
    desk = Desk(Path(store or default_store()), engine=engine, run_jobs=not as_job)
    try:
        if market:
            desk.use_snapshot({"snapshot_id": market})
        body = {"options": {"precision": precision}, "workers": workers, "days": days}
        job = (desk.risk.var(body) if command == "var" else desk.risk.ladder(body))["job"]
        if as_job:
            log(f"job {job} queued: optlab jobs follow, or GET /api/v1/jobs/{job}")
            return job
        if desk.worker is None:
            raise RuntimeError("another worker serves this database: stop it, or pass --as-job")
        while (j := desk.job(job))["state"] in ("queued", "running"):
            time.sleep(1)
        if j["state"] != "done":
            raise RuntimeError(f"{command} {j['state']}: {(j.get('error') or {}).get('message')}")
        res = j["result"]
        if command == "var":
            for k in ("one_day", "ten_day", "stressed"):
                if res.get(k):
                    r = res[k]
                    log(
                        f"{k:9s} VaR {r['var']:>12,.0f} (SE of its scenario {r['var_se']:,.0f})  ES {r['es']:>12,.0f}  "
                        f"on {r['var_date']}  window {r['start']} to {r['end']}  {r['seconds']:.0f} s"
                    )
        else:
            for rule in res["rules"]:
                log(rule, "  ".join(f"{p:+.0%} {res['book'][f'pnl@{rule}@{p:+.4f}'][0]:,.0f}" for p in res["points"]))
        log(f"stored as {res['run_id']}")
    finally:
        desk.close()
    log(f"{time.perf_counter() - t0:.0f} s")
    return None
