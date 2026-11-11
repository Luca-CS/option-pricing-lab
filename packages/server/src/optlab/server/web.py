"""The HTTP layer of API v1 (ADR-0005): problem+json errors, the request guard, the security headers, and who may call
what. Routers live in `optlab.server.routers`; `optlab.server.app.create_app` puts them together.

The guard refuses, before any route runs:
- a `Host` that is not this machine when the LAN profile is off (a page that rebinds its name to 127.0.0.1);
- a state-changing request whose `Origin` is another site (`null` included);
- a body that is not `application/json`, and a body larger than `Settings.max_body_bytes`.
Every route then declares who may call it with `access(...)`: a session cookie, the CSRF token in `X-CSRF-Token` on
state-changing methods, and one of the roles named (admin may do everything; none named: any signed-in user).
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from optlab.core.log import get_logger
from optlab.server.audit import Audit
from optlab.server.security import COOKIE, CSRF_HEADER, Accounts, User
from optlab.server.settings import Settings

LOG = get_logger(__name__)
SAFE = {"GET", "HEAD", "OPTIONS"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PROBLEM = "application/problem+json"
CSP_API = "default-src 'none'; frame-ancestors 'none'"
CSP_PAGE = (  # the front's build (security.md §4): no inline script, no third-party origin
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
)
CSP_REPORT = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'"
HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}


@dataclass
class State:
    """What the routes share: the settings, the desk (the services), the accounts and the audit log, and who
    submitted each job (until the job table of A6 keeps it)."""

    settings: Settings
    desk: Any
    accounts: Accounts
    audit: Audit
    job_owner: dict[str, str] = field(default_factory=dict)


def state(request: Request) -> State:
    return request.app.state.optlab


# --- problems (RFC 9457) ----------------------------------------------------------------------------------------------
class Problem(Exception):
    def __init__(self, status: int, detail: str, title: str | None = None, errors: list[dict] | None = None):
        super().__init__(detail)
        self.status, self.detail, self.title, self.errors = status, detail, title, errors or []


TITLES = {
    400: "Bad request",
    401: "Not signed in",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Body too large",
    415: "Unsupported media type",
    422: "Invalid input",
    423: "Locked",
    429: "Too many requests",
    500: "Internal error",
}


def problem(
    status: int, detail: str, title: str | None = None, errors: list[dict] | None = None, extra: dict | None = None
) -> JSONResponse:
    """An RFC 9457 problem: `errors` lists the fields at fault ({field, message}, field a dotted path); `extra` adds
    extension members (the term-sheet form keeps its visible form there)."""
    body = {"type": "about:blank", "title": title or TITLES.get(status, "Error"), "status": status, "detail": detail}
    if errors:
        body["errors"] = errors
    if extra:
        body.update({k: v for k, v in extra.items() if k not in body})
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def _field(loc) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    return ".".join(parts)


def install_problems(app) -> None:
    from optlab.server.api import ApiError

    @app.exception_handler(Problem)
    async def _problem(request, exc: Problem):
        return problem(exc.status, exc.detail, exc.title, exc.errors)

    @app.exception_handler(ApiError)
    async def _api_error(request, exc: ApiError):
        errors = [{"field": exc.field, "message": str(exc)}] if exc.field else None
        return problem(exc.status, str(exc), errors=errors)

    @app.exception_handler(KeyError)
    async def _missing(request, exc: KeyError):  # a service looked up an id that does not exist, as v0 answered
        return problem(404, str(exc).strip("'\""))

    @app.exception_handler(RequestValidationError)
    async def _invalid(request, exc: RequestValidationError):
        errors = [{"field": _field(e.get("loc", ())), "message": e.get("msg", "invalid")} for e in exc.errors()]
        return problem(422, "the request is not valid: see errors", errors=jsonable_encoder(errors))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request, exc: StarletteHTTPException):
        return problem(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _crash(request, exc: Exception):  # never a stack trace in a response
        LOG.exception("unhandled error on %s %s", request.method, request.url.path)
        return problem(500, "the server failed on this request; the log has the details")


# --- who may call what --------------------------------------------------------------------------------------------------
def access(*roles: str, public: bool = False) -> dict:
    """The route's keyword arguments that check its caller. `public`: no session (health, ready, login)."""
    if public:
        return {"openapi_extra": {"x-roles": ["public"]}}

    def check(request: Request) -> User:
        st = state(request)
        found = st.accounts.session(request.cookies.get(COOKIE))
        if found is None:
            raise Problem(401, "sign in first (POST /api/v1/auth/login)")
        user, csrf = found
        if request.method not in SAFE and not hmac.compare_digest(request.headers.get(CSRF_HEADER, ""), csrf):
            raise Problem(403, f"missing or wrong {CSRF_HEADER} header (from POST /auth/login or GET /auth/me)")
        if not user.has(*roles):
            raise Problem(403, f"this needs one of the roles {', '.join(roles)}")
        request.state.user = user
        return user

    return {"dependencies": [Depends(check)], "openapi_extra": {"x-roles": sorted(roles) or ["viewer"]}}


def current_user(request: Request) -> User:
    """The caller, as `access` checked it (route dependencies run before the endpoint's own)."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise Problem(401, "sign in first (POST /api/v1/auth/login)")
    return user


# --- the guard ------------------------------------------------------------------------------------------------------
def _host(netloc: str) -> str | None:
    try:
        return urlsplit(f"//{netloc}").hostname
    except ValueError:
        return None


class Guard:
    """Pure ASGI middleware: the checks above, then the security headers on every response."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        method, host = scope["method"], headers.get("host", "")
        refusal = self._refusal(method, host, headers)
        if refusal is not None:
            status, detail = refusal
            return await self._send_problem(scope, receive, send, status, detail)
        limit = self.settings.max_body_bytes
        seen = 0

        async def receive_limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge(413, f"the body exceeds {limit} bytes")
            return message

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.decode("latin-1").lower() for k, _ in message.get("headers", [])}
                extra = [(k.encode(), v.encode()) for k, v in HEADERS.items() if k not in present]
                if "content-security-policy" not in present:
                    path = scope.get("path", "")
                    extra.append(
                        (b"content-security-policy", (CSP_API if path.startswith("/api/") else CSP_PAGE).encode())
                    )
                if scope.get("path", "").startswith("/api/") and "cache-control" not in present:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            await self.app(scope, receive_limited, send_with_headers)
        except _TooLarge:
            await self._send_problem(scope, receive, send, 413, f"the body exceeds {limit} bytes")

    def _refusal(self, method: str, host: str, headers: dict) -> tuple[int, str] | None:
        if not self.settings.lan and _host(host) not in LOOPBACK:
            return 403, "this server answers on 127.0.0.1 or localhost only"
        if method in SAFE:
            return None
        origin = headers.get("origin")
        if origin is not None and (origin == "null" or urlsplit(origin).netloc.lower() != host.lower()):
            return 403, "cross-site request refused"
        try:
            length = int(headers.get("content-length") or 0)
        except ValueError:
            return 400, "bad Content-Length"
        if length > self.settings.max_body_bytes:
            return 413, f"the body exceeds {self.settings.max_body_bytes} bytes"
        ctype = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if (length or ctype) and ctype != "application/json":
            return 415, "the body must be application/json"
        return None

    async def _send_problem(self, scope, receive, send, status: int, detail: str) -> None:
        body = json.dumps(
            {"type": "about:blank", "title": TITLES.get(status, "Error"), "status": status, "detail": detail}
        ).encode()
        headers = [(b"content-type", PROBLEM.encode()), (b"content-length", str(len(body)).encode())]
        headers += [(k.encode(), v.encode()) for k, v in HEADERS.items()]
        headers.append((b"content-security-policy", CSP_API.encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class _TooLarge(StarletteHTTPException):
    """A body without Content-Length that grows past the limit. An HTTP exception, so that FastAPI, reading the body,
    passes it on (it turns any other exception into a 400) and the problem handler answers 413."""
