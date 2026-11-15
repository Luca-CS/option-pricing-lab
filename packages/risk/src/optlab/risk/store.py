"""Storage of the risk module: versioned records (spot-vol betas, scenarios, the stressed VaR window) and runs (EOD, VaR,
ladders), each with the code version and the parameters it ran with.

Records are append-only: a `put` adds a version, `get` reads the latest (or a given one). Runs are keyed by a hash of what
determines their numbers (kind, date, trades, market, settings, code): a run asked again with the same key is the same run,
which makes a backfill idempotent. Payloads are JSON; numbers are amounts in the payment currency and % of notional, never
market levels.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Protocol

from optlab.core import programs


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def code_version() -> str:
    """The commit of the code, with "+dirty" when the working tree has changes under packages/ (git is asked from this
    file's folder, so it finds the repository the code runs from)."""
    try:
        git = programs.require("git")
        head = subprocess.run(  # noqa: S603 (an absolute path, fixed arguments, no shell)
            [git, "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(  # noqa: S603 (an absolute path, fixed arguments, no shell)
            [git, "status", "--porcelain", "--", ":/packages"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return (head or "unknown") + ("+dirty" if dirty else "")
    except OSError, subprocess.SubprocessError:
        return "unknown"


def run_key(**parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


class RiskStorage(Protocol):
    """Where the risk engine keeps its records and runs; `optlab.server.persistence.risk.RiskRepository` implements it
    on the operational database (ADR-0007). Payloads are JSON-ready dicts."""

    def put(self, kind: str, key: str, payload, meta: dict | None = None) -> int: ...

    def get(self, kind: str, key: str, version: int | None = None) -> dict | None: ...

    def versions(self, kind: str, key: str) -> list[dict]: ...

    def keys(self, kind: str) -> list[str]: ...

    def find_run(self, key: str) -> dict | None: ...

    def save_run(
        self, kind: str, day, key: str, params: dict, payload: dict, seconds: float, code: str | None = None
    ) -> str: ...

    def run(self, run_id: str) -> dict: ...

    def runs(self, kind: str | None = None, start=None, end=None, latest: bool = True) -> list[dict]: ...
