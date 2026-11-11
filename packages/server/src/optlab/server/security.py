"""Accounts, sessions and roles (ADR-0020).

- Passwords are hashed with argon2id (`argon2-cffi`'s defaults, above OWASP's minimum) and rehashed at login when the
  parameters change. A login that fails says only "wrong name or password", and an unknown name costs the same hash
  as a known one, so neither the answer nor its time tells whether an account exists.
- An account is locked for `login_lockout_minutes` after `login_max_failures` failures in a row; a client address
  gets `login_per_address` attempts per 10 minutes.
- A session is a random 256-bit value in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` under TLS); the database
  keeps its sha256 only. It ends after `session_idle_minutes` unused or `session_absolute_hours` after its login, at
  logout, and for every session of a user whose password, roles or account change.
- Each session has a CSRF token that every state-changing request sends back in `X-CSRF-Token`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import insert, select, update

from optlab.server.persistence import Database
from optlab.server.persistence.schema import sessions, users
from optlab.server.settings import Settings

ROLES = ("admin", "trader", "structurer", "risk", "quant", "viewer")
COOKIE = "optlab_session"
CSRF_HEADER = "X-CSRF-Token"
NAME = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")
MIN_PASSWORD = 12
WRONG = "wrong name or password"


class LoginError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    roles: frozenset[str]

    def has(self, *wanted: str) -> bool:
        """Admin may do everything; otherwise one of `wanted` (no role named: any signed-in user, a viewer)."""
        return "admin" in self.roles or not wanted or bool(self.roles & set(wanted))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_roles(roles) -> list[str]:
    out = sorted(set(roles))
    bad = [r for r in out if r not in ROLES]
    if bad or not out:
        raise ValueError(f"roles must be among {', '.join(ROLES)}, not {bad or 'none'}")
    return out


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"a password has at least {MIN_PASSWORD} characters")
    if len(password) > 1024:
        raise ValueError("a password has at most 1024 characters")


class Accounts:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings
        self.hasher = PasswordHasher()
        self._dummy = self.hasher.hash(secrets.token_urlsafe(16))  # verified for unknown names: the same cost
        self._attempts: dict[str, deque] = {}
        self._lock = threading.Lock()

    # --- accounts (the CLI `optlab.server.users`, and the admin routes) ---------------------------------------------
    def create(self, name: str, password: str, roles) -> User:
        if not NAME.match(name):
            raise ValueError("a name is 2 to 32 lower-case letters, digits, '.', '_' or '-', starting with a letter")
        check_password(password)
        roles = check_roles(roles)
        now = _iso(_now())
        user = User(uuid.uuid4().hex[:12], name, frozenset(roles))
        with self.db.write() as c:
            if c.execute(select(users.c.user_id).where(users.c.name == name)).first() is not None:
                raise ValueError(f"user {name!r} exists")
            c.execute(
                insert(users).values(
                    user_id=user.user_id,
                    name=name,
                    password_hash=self.hasher.hash(password),
                    roles=json.dumps(roles),
                    disabled=0,
                    failed_logins=0,
                    locked_until=None,
                    created_at=now,
                    password_changed_at=now,
                )
            )
        return user

    def get(self, name: str) -> dict | None:
        with self.db.read() as c:
            row = c.execute(select(users).where(users.c.name == name)).first()
        return None if row is None else self._public(row)

    def all(self) -> list[dict]:
        with self.db.read() as c:
            return [self._public(r) for r in c.execute(select(users).order_by(users.c.name))]

    @staticmethod
    def _public(row) -> dict:
        return {
            "user_id": row.user_id,
            "name": row.name,
            "roles": json.loads(row.roles),
            "disabled": bool(row.disabled),
            "locked_until": row.locked_until,
            "created_at": row.created_at,
            "password_changed_at": row.password_changed_at,
        }

    def set_roles(self, name: str, roles) -> dict:
        roles = check_roles(roles)
        self._change(name, roles=json.dumps(roles))
        return self.get(name) or {}

    def set_password(self, name: str, password: str) -> None:
        check_password(password)
        self._change(
            name,
            password_hash=self.hasher.hash(password),
            password_changed_at=_iso(_now()),
            failed_logins=0,
            locked_until=None,
        )

    def set_disabled(self, name: str, disabled: bool) -> None:
        self._change(name, disabled=int(disabled))

    def _change(self, name: str, **values) -> None:
        """Change an account and end all its sessions (rotation on a change of privilege or password)."""
        with self.db.write() as c:
            uid = c.execute(select(users.c.user_id).where(users.c.name == name)).scalar()
            if uid is None:
                raise KeyError(f"no user {name!r}")
            c.execute(update(users).where(users.c.user_id == uid).values(**values))
            c.execute(update(sessions).where(sessions.c.user_id == uid).values(revoked=1))

    # --- login ----------------------------------------------------------------------------------------------------
    def _failures(self, address: str) -> deque:
        now, window = time.monotonic(), 600.0
        q = self._attempts.setdefault(address, deque())
        while q and now - q[0] > window:
            q.popleft()
        return q

    def authenticate(self, name: str, password: str, address: str) -> User:
        """The user, or LoginError: 401 (one message whatever was wrong), 423 (locked), 429 (this address failed
        `login_per_address` times in 10 minutes; successful logins do not count, so users behind one address do not
        block each other)."""
        with self._lock:
            if len(self._failures(address)) >= self.settings.login_per_address:
                raise LoginError("too many failed logins from this address; wait a few minutes", 429)
        try:
            return self._authenticate(name, password)
        except LoginError as exc:
            if exc.status == 401:
                with self._lock:
                    self._failures(address).append(time.monotonic())
            raise

    def _authenticate(self, name: str, password: str) -> User:
        with self.db.read() as c:
            row = c.execute(select(users).where(users.c.name == name)).first()
        if row is None:
            try:
                self.hasher.verify(self._dummy, password)
            except VerificationError, InvalidHashError:
                pass
            raise LoginError(WRONG)
        now = _now()
        if row.locked_until and dt.datetime.fromisoformat(row.locked_until) > now:
            raise LoginError("this account is locked for a few minutes after failed logins", 423)
        try:
            self.hasher.verify(row.password_hash, password)
        except VerifyMismatchError, InvalidHashError:
            failures = row.failed_logins + 1
            values: dict = {"failed_logins": failures}
            if failures >= self.settings.login_max_failures:
                values = {
                    "failed_logins": 0,
                    "locked_until": _iso(now + dt.timedelta(minutes=self.settings.login_lockout_minutes)),
                }
            with self.db.write() as c:
                c.execute(update(users).where(users.c.user_id == row.user_id).values(**values))
            raise LoginError(WRONG) from None
        if row.disabled:
            raise LoginError(WRONG)
        values = {"failed_logins": 0, "locked_until": None}
        if self.hasher.check_needs_rehash(row.password_hash):
            values["password_hash"] = self.hasher.hash(password)
        with self.db.write() as c:
            c.execute(update(users).where(users.c.user_id == row.user_id).values(**values))
        return User(row.user_id, row.name, frozenset(json.loads(row.roles)))

    # --- sessions -------------------------------------------------------------------------------------------------
    def open_session(self, user: User) -> tuple[str, str]:
        """(cookie value, CSRF token) of a new session; the database keeps the cookie's hash only."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = _now()
        with self.db.write() as c:
            c.execute(
                insert(sessions).values(
                    session_hash=_digest(token),
                    user_id=user.user_id,
                    csrf_token=csrf,
                    created_at=_iso(now),
                    last_seen=_iso(now),
                    expires_at=_iso(now + dt.timedelta(hours=self.settings.session_absolute_hours)),
                    revoked=0,
                )
            )
        return token, csrf

    def session(self, token: str | None) -> tuple[User, str] | None:
        """(user, CSRF token) of a live session, or None (unknown, revoked, expired, idle, or a disabled account)."""
        if not token or len(token) > 128:
            return None
        key, now = _digest(token), _now()
        with self.db.read() as c:
            row = c.execute(
                select(sessions, users.c.name, users.c.roles, users.c.disabled)
                .join(users, users.c.user_id == sessions.c.user_id)
                .where(sessions.c.session_hash == key)
            ).first()
        if row is None or row.revoked or row.disabled:
            return None
        idle = dt.timedelta(minutes=self.settings.session_idle_minutes)
        last = dt.datetime.fromisoformat(row.last_seen)
        if dt.datetime.fromisoformat(row.expires_at) <= now or last + idle <= now:
            return None
        if now - last > dt.timedelta(minutes=1):  # one write a minute at most, not one per request
            with self.db.write() as c:
                c.execute(update(sessions).where(sessions.c.session_hash == key).values(last_seen=_iso(now)))
        return User(row.user_id, row.name, frozenset(json.loads(row.roles))), row.csrf_token

    def close_session(self, token: str) -> None:
        with self.db.write() as c:
            c.execute(update(sessions).where(sessions.c.session_hash == _digest(token)).values(revoked=1))

    def close_all(self, user_id: str) -> None:
        with self.db.write() as c:
            c.execute(update(sessions).where(sessions.c.user_id == user_id).values(revoked=1))
