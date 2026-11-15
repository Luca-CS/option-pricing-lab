"""The worker's schedules (ADR-0006): rows of the database that submit a job on given days at a given time.

| schedule | job | default |
|---|---|---|
| `backup` | a consistent copy of the database (`VACUUM INTO`) in `<backups>/nightly`, the last 14 days kept | on, 03:00 |
| `housekeeping` | finished jobs older than 30 days, expired sessions, old log files | on, 03:30 |
| `lake_backup` | a copy of the lake in `<backups>/lake` (unchanged files linked to the last copy), 4 kept | on, Sun 04:00 |
| `lake` | the lake's public sources (`marketdata.ingest.update_all`) | off, Mon-Fri 22:30 |
| `eod` | the risk EOD of the day on the book (`risk.pnl.backfill`) | off, Mon-Fri 23:00 |

Times are local to `Settings.timezone` (Europe/Paris). A schedule whose time passed while no worker ran fires once when
one starts (a missed backup is made at the next start), then at its next time. The admin role edits them
(`PATCH /api/v1/admin/pipelines/{name}`, audited) and can run one now; jobs a schedule submits belong to the system
(user None), so only admins see them. The Yahoo option chains stay a manual step: Yahoo blanks bid and ask outside US
hours, and a schedule that runs while the machine sleeps would store empty chains.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select, update

from optlab.core.log import get_logger
from optlab.server.jobs import canonical, current_submitter, submitting
from optlab.server.persistence.schema import schedules, sessions
from optlab.server.tasks import Frozen, envelope, register

LOG = get_logger(__name__)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEK, WEEKDAYS = ",".join(DAYS), ",".join(DAYS[:5])
DEFAULTS = {
    "backup": {"kind": "backup", "days": WEEK, "at": "03:00", "enabled": True, "params": {}},
    "housekeeping": {"kind": "housekeeping", "days": WEEK, "at": "03:30", "enabled": True, "params": {}},
    "lake_backup": {"kind": "lake_backup", "days": "sun", "at": "04:00", "enabled": True, "params": {}},
    "lake": {"kind": "lake", "days": WEEKDAYS, "at": "22:30", "enabled": False, "params": {}},
    "eod": {"kind": "eod_today", "days": WEEKDAYS, "at": "23:00", "enabled": False, "params": {"greeks": True}},
}
PARAMS = {  # what an admin may set per kind
    "backup": {"keep_days"},
    "housekeeping": {"job_keep_days", "log_keep_days"},
    "lake_backup": {"keep"},
    "lake": {"only"},
    "eod_today": {"greeks", "options"},
}


def _risk_options(v) -> bool:
    from pydantic import ValidationError

    from optlab.server.schemas import RiskOptions

    try:
        RiskOptions.model_validate(v)
    except ValidationError:
        return False
    return True


def parse_days(days: str) -> tuple[int, ...]:
    out = []
    for d in str(days).lower().replace(" ", "").split(","):
        if d not in DAYS:
            raise ValueError(f"days: {d!r} is not one of {', '.join(DAYS)}")
        out.append(DAYS.index(d))
    if not out:
        raise ValueError("days: at least one day")
    return tuple(sorted(set(out)))


def parse_at(at: str) -> dt.time:
    try:
        hh, mm = str(at).split(":")
        return dt.time(int(hh), int(mm))
    except ValueError:
        raise ValueError(f"at: {at!r} is not a time such as 03:00") from None


def next_run(days: str, at: str, timezone: str, after: float) -> float:
    """The first time strictly after `after` (epoch seconds) that falls on one of `days` at `at`, local time."""
    tz = ZoneInfo(timezone)
    wanted, clock = parse_days(days), parse_at(at)
    local = dt.datetime.fromtimestamp(after, tz)
    for k in range(8):
        day = local.date() + dt.timedelta(days=k)
        if day.weekday() not in wanted:
            continue
        when = dt.datetime.combine(day, clock, tz)
        if when.timestamp() > after:
            return when.timestamp()
    raise AssertionError("a weekday within eight days")


class Schedules:
    """The schedules of a desk's database, fired by the worker."""

    def __init__(self, desk, clock=time.time):
        self.desk, self.db, self.clock = desk, desk.db, clock
        self.timezone = desk.settings.timezone

    def ensure_defaults(self) -> None:
        """Add the schedules of this version that the database lacks; an edited one is never overwritten."""
        now = self.clock()
        with self.db.write() as c:
            have = set(c.execute(select(schedules.c.name)).scalars())
            for name, d in DEFAULTS.items():
                if name in have:
                    continue
                c.execute(
                    insert(schedules).values(
                        name=name,
                        kind=d["kind"],
                        params=canonical(d["params"]),
                        days=d["days"],
                        at=d["at"],
                        timezone=self.timezone,
                        enabled=int(d["enabled"]),
                        next_run_at=next_run(d["days"], d["at"], self.timezone, now),
                        updated_at=_iso(now),
                    )
                )

    def all(self) -> list[dict]:
        with self.db.read() as c:
            rows = c.execute(select(schedules).order_by(schedules.c.name)).all()
        return [self._row(r) for r in rows]

    def get(self, name: str) -> dict:
        with self.db.read() as c:
            row = c.execute(select(schedules).where(schedules.c.name == name)).first()
        if row is None:
            raise KeyError(f"no schedule {name!r}")
        return self._row(row)

    @staticmethod
    def _row(r) -> dict:
        return {
            "name": r.name,
            "kind": r.kind,
            "params": json.loads(r.params),
            "days": r.days,
            "at": r.at,
            "timezone": r.timezone,
            "enabled": bool(r.enabled),
            "last_run_at": r.last_run_at,
            "last_job_id": r.last_job_id,
            "next_run_at": r.next_run_at if r.enabled else None,
            "updated_at": r.updated_at,
            "updated_by": r.updated_by,
        }

    def update(self, name: str, changes: dict, user_id: str | None) -> dict:
        """Enable or disable a schedule, move it, or change its parameters (validated here: a bad one is refused)."""
        old = self.get(name)
        new = {**old, **{k: v for k, v in changes.items() if v is not None}}
        unknown = set(new["params"] or {}) - PARAMS[old["kind"]]
        if unknown:
            allowed = ", ".join(sorted(PARAMS[old["kind"]])) or "none"
            raise ValueError(f"params: {', '.join(sorted(unknown))} not a parameter of {old['kind']} ({allowed})")
        for key, value in (new["params"] or {}).items():
            ok = {
                "keep_days": lambda v: isinstance(v, int) and 1 <= v <= 365,
                "keep": lambda v: isinstance(v, int) and 1 <= v <= 52,
                "job_keep_days": lambda v: isinstance(v, int) and 1 <= v <= 3650,
                "log_keep_days": lambda v: isinstance(v, int) and 1 <= v <= 365,
                "only": lambda v: isinstance(v, list) and len(v) <= 50 and all(isinstance(x, str) for x in v),
                "greeks": lambda v: isinstance(v, bool),
                "options": _risk_options,  # the bounds of POST /risk/eod-runs
            }[key](value)
            if not ok:
                raise ValueError(f"params.{key}: {value!r} is not a valid value")
        parse_days(new["days"])
        parse_at(new["at"])
        ZoneInfo(new["timezone"])
        values = {
            "enabled": int(bool(new["enabled"])),
            "days": ",".join(DAYS[i] for i in parse_days(new["days"])),
            "at": parse_at(new["at"]).strftime("%H:%M"),
            "timezone": new["timezone"],
            "params": canonical(new["params"] or {}),
            "next_run_at": next_run(new["days"], new["at"], new["timezone"], self.clock()),
            "updated_at": _iso(self.clock()),
            "updated_by": user_id,
        }
        with self.db.write() as c:
            c.execute(update(schedules).where(schedules.c.name == name).values(**values))
        return self.get(name)

    def submit(self, name: str, user_id: str | None = None) -> dict:
        """Submit the job of a schedule now: by the worker when it is due (the system), or by an admin (whose
        Idempotency-Key, if any, still holds)."""
        s = self.get(name)
        outer = current_submitter()
        with submitting(user_id, outer.idem_key if outer and user_id else None, schedule=name):
            out = SUBMIT[s["kind"]](self.desk, s["params"])
        with self.db.write() as c:
            c.execute(
                update(schedules)
                .where(schedules.c.name == name)
                .values(last_run_at=self.clock(), last_job_id=out["job"])
            )
        return out

    def fire(self) -> list[str]:
        """Submit every enabled schedule whose time has come, and move it to its next time."""
        now = self.clock()
        with self.db.read() as c:
            due = c.execute(select(schedules).where(schedules.c.enabled == 1, schedules.c.next_run_at <= now)).all()
        fired = []
        for r in due:
            with self.db.write() as c:  # moved first: a failing submission does not fire again every 20 seconds
                c.execute(
                    update(schedules)
                    .where(schedules.c.name == r.name)
                    .values(next_run_at=next_run(r.days, r.at, r.timezone, now))
                )
            try:
                self.submit(r.name)
                fired.append(r.name)
            except Exception as exc:  # e.g. the EOD with no live trade: logged, tried again at its next time
                LOG.warning("schedule %s not submitted: %s", r.name, exc)
        return fired


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).isoformat(timespec="seconds")


# --- the jobs of the schedules ----------------------------------------------------------------------------------------
def _system_market(as_of=None) -> dict:
    return {"snapshot_id": None, "origin": None, "as_of": as_of}


def run_backup(desk, p: dict, f: Frozen) -> dict:
    """A consistent copy of the database, then the copies older than the retention removed."""
    from optlab.server.backups import database_copy, prune_files

    folder = Path(desk.settings.backups) / "nightly"
    target = database_copy(desk.db, folder)
    keep = int(p.get("keep_days", desk.settings.backup_keep_days))
    removed = prune_files(folder, "optlab-*.sqlite", keep, keep=target)
    out = {"file": target.name, "bytes": target.stat().st_size, "removed": removed, "keep_days": keep}
    return envelope(_system_market(), {"name": "vacuum_into"}, out)


def run_lake_backup(desk, p: dict, f: Frozen) -> dict:
    """A copy of the lake (`backups.lake_snapshot`), unless the lake is being updated right now."""
    from optlab.core import progress
    from optlab.server.backups import lake_snapshot

    if any(j["kind"] == "lake" and j["state"] == "running" for j in desk.jobs.active(everyone=True)):
        raise RuntimeError("the lake is being updated: its copy waits for the next time")
    progress.step("lake: copy", 0, 1)
    keep = int(p.get("keep", desk.settings.lake_backup_keep))
    out = lake_snapshot(desk.lake.root, Path(desk.settings.backups) / "lake", keep=keep, log=LOG.info)
    return envelope(_system_market(), {"name": "lake_copy", "linked": "unchanged files"}, out)


def run_housekeeping(desk, p: dict, f: Frozen) -> dict:
    s = desk.settings
    pruned = desk.jobs.prune(float(p.get("job_keep_days", s.job_keep_days)))
    old_sessions = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    with desk.db.write() as c:  # sessions over a week past their expiry, or revoked a week ago
        res = c.execute(delete(sessions).where(sessions.c.expires_at < old_sessions.isoformat(timespec="seconds")))
    logs, cutoff = [], time.time() - int(p.get("log_keep_days", s.log_keep_days)) * 86400
    rotated = [*Path(s.logs).glob("api.log.*"), *Path(s.logs).glob("worker.log.*")]  # ours only (logs.py)
    for old in rotated:
        if old.stat().st_mtime < cutoff:
            old.unlink()
            logs.append(old.name)
    out = {"jobs_removed": pruned, "sessions_removed": res.rowcount or 0, "logs_removed": logs}
    return envelope(_system_market(), {"name": "housekeeping"}, out)


def run_lake(desk, p: dict, f: Frozen) -> dict:
    from optlab.core import progress
    from optlab.marketdata.ingest import update_all

    progress.step("lake: public sources")
    out = update_all(desk.lake, p.get("only"), log=LOG.info)
    return envelope(_system_market(dt.date.today()), {"name": "lake_update", "only": p.get("only")}, out)


def submit_system(kind: str):
    return lambda desk, params: desk._submit(kind, params)


def submit_eod_today(desk, params: dict) -> dict:
    """The EOD of today (local to the schedules' time zone) on the book as it is."""
    today = dt.datetime.now(ZoneInfo(desk.settings.timezone)).date().isoformat()
    return desk.risk.pnl_eod({**params, "from": today, "to": today})


SUBMIT = {
    "backup": submit_system("backup"),
    "housekeeping": submit_system("housekeeping"),
    "lake_backup": submit_system("lake_backup"),
    "lake": submit_system("lake"),
    "eod_today": submit_eod_today,
}
register("backup", run_backup)
register("housekeeping", run_housekeeping)
register("lake_backup", run_lake_backup)
register("lake", run_lake)
