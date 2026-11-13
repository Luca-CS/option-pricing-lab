"""Settings of an optlab process: one object, read from the environment (prefix `OPTLAB_`) and then from
`<data>/config/optlab.env`, which lives outside the repository with restricted rights (ADR-0012). Every path the
domain packages use comes from here and is passed to them as an argument: nothing else reads the environment.

`data` is the folder that holds everything that is not code: the lake, the stores, the logs, the backups, the
configuration. It is `OPTLAB_DATA` when set (containers set it), else the development default, `optlab-data` beside
the main checkout of this repository, else `data/` in the repository (git-ignored). The other paths default under it.

Validation happens when the object is built, so a process stops at start with a clear message: an unknown key in the
env file, a host other than this machine without the `lan` flag, a folder that should exist and does not.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "OPTLAB_"
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}


def repository_root() -> Path | None:
    """The repository this code runs from (an editable install), or None for an installed wheel."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "packages").is_dir():
            return parent
    return None


def default_data() -> Path:
    """`optlab-data` beside the main checkout (worktrees live in `<main>/.claude/worktrees/<name>`), else the
    repository's `data/`, else `~/optlab-data` for an installed wheel."""
    repo = repository_root()
    if repo is None:
        return Path.home() / "optlab-data"
    main = repo.parents[2] if repo.parent.name == "worktrees" else repo
    sibling = main.parent / "optlab-data"
    return sibling if sibling.is_dir() else repo / "data"


def _in_repository(relative: str, otherwise: Path) -> Path:
    """A folder of the repository the code runs from, or `otherwise` for an installed wheel."""
    repo = repository_root()
    return repo / relative if repo is not None else otherwise


def env_file(data: Path) -> Path:
    return data / "config" / "optlab.env"


class Settings(BaseSettings):
    """What a process needs to know about its machine. Build it with `Settings.load()`."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="forbid", validate_default=True, frozen=True)

    # --- paths: each default is computed from the fields above it, once they are validated ----------------------
    data: Path = Field(default_factory=default_data, description="everything that is not code")
    store: Path = Field(default_factory=lambda f: f["data"] / "store", description="the desk's store folder")
    database: Path = Field(
        default_factory=lambda f: f["store"] / "optlab.sqlite", description="the operational database (ADR-0007)"
    )
    lake: Path = Field(default_factory=lambda f: f["data"] / "lake", description="the historical lake")
    results: Path = Field(default_factory=lambda f: _in_repository("results", f["data"] / "archive"))
    calibration: Path = Field(
        default_factory=lambda f: f["results"] / "spx_calibration.json", description="the desk's starting market"
    )
    raw: Path = Field(
        default_factory=lambda f: _in_repository("data/raw", f["data"] / "raw"),
        description="personal-use option chain snapshots",
    )
    logs: Path = Field(default_factory=lambda f: f["data"] / "logs")
    backups: Path = Field(default_factory=lambda f: f["data"] / "backups")

    # --- network ---------------------------------------------------------------------------------------------------
    host: str = Field("127.0.0.1", description="where the server listens; beyond this machine only with lan")
    port: int = Field(8765, ge=1, le=65535)
    lan: bool = Field(False, description="the LAN profile (ADR-0002): off unless Luca turns it on")

    frontend: Path | None = Field(None, description="the front's static build, served on the same origin (session 2)")
    docs: bool = Field(False, description="the Swagger UI page, whose script comes from a CDN: off unless asked")

    # --- security (ADR-0020) ---------------------------------------------------------------------------------------
    session_idle_minutes: int = Field(480, ge=5, le=1440, description="a session unused this long ends")
    session_absolute_hours: int = Field(168, ge=1, le=720, description="a session ends this long after its login")
    login_max_failures: int = Field(5, ge=3, le=20, description="failed logins of an account before it is locked")
    login_lockout_minutes: int = Field(15, ge=1, le=1440)
    login_per_address: int = Field(20, ge=5, le=200, description="failed logins from one address per 10 minutes")
    max_body_bytes: int = Field(2_000_000, ge=10_000, le=50_000_000, description="largest request body accepted")

    # --- computing -------------------------------------------------------------------------------------------------
    engine: Literal["numpy", "cpu", "gpu", "auto"] = Field("auto", description="where Monte Carlo paths come from")
    workers: int | None = Field(None, ge=1, le=64, description="process pool of NumPy Monte Carlo batches")

    # --- jobs and schedules (ADR-0006) -----------------------------------------------------------------------------
    worker: Literal["embedded", "external"] = Field(
        "embedded",
        description="the job worker runs in the api process (embedded), or as python -m optlab.server.worker",
    )
    job_concurrency: int = Field(2, ge=1, le=8, description="jobs a worker runs at a time")
    job_keep_days: int = Field(30, ge=1, le=3650, description="finished jobs older than this are removed")
    backup_keep_days: int = Field(14, ge=1, le=365, description="nightly database copies kept")
    log_keep_days: int = Field(14, ge=1, le=365, description="daily log files kept")
    timezone: str = Field("Europe/Paris", description="the local time of the schedules")

    @field_validator(
        "data",
        "store",
        "database",
        "lake",
        "results",
        "calibration",
        "raw",
        "logs",
        "backups",
        "frontend",
        mode="after",
    )
    @classmethod
    def _absolute(cls, v: Path | None) -> Path | None:
        return None if v is None else v.expanduser().resolve()

    @model_validator(mode="after")
    def _safety(self) -> Settings:
        if not self.lan and not _is_loopback(self.host):
            raise ValueError(
                f"host {self.host!r} listens beyond this machine: set OPTLAB_LAN=true only with Luca's agreement "
                "(ADR-0002), or keep 127.0.0.1"
            )
        return self

    @classmethod
    def load(cls, **overrides) -> Settings:
        """The environment, then `<data>/config/optlab.env` when it exists, then `overrides` (command-line options
        not left at their default). The env file is read only from the data folder the environment names."""
        data = Path(os.environ.get(f"{ENV_PREFIX}DATA") or overrides.get("data") or default_data())
        file = env_file(data)
        given = {k: v for k, v in overrides.items() if v is not None}
        return cls(_env_file=file if file.is_file() else None, **given)  # pyright: ignore[reportCallIssue] (a pydantic-settings init option)

    def describe(self) -> dict:
        """Where everything is, for a start-up line or a health check (no secret in here)."""
        return {k: str(v) if isinstance(v, Path) else v for k, v in self.model_dump().items()}


def _is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False
