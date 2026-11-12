"""API v1 (ADR-0005): `create_app` builds the FastAPI application over the desk's services.

Routes are under `/api/v1`, one router per domain (`optlab.server.routers`). The OpenAPI document is
`/api/v1/openapi.json` and its versioned copy `api/openapi.json` (`python -m optlab.server.openapi export`).
The front's static build (`OPTLAB_FRONTEND`, session 2) is served on the same origin; there is no CORS.
"""

from __future__ import annotations

import base64
import hashlib
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from optlab.server.api import Desk
from optlab.server.audit import Audit
from optlab.server.risk_api import RiskApi
from optlab.server.routers import admin, market, quant, risk, structuring, system, trading
from optlab.server.security import COOKIE, CSRF_HEADER, Accounts
from optlab.server.settings import Settings
from optlab.server.web import Guard, State, install_problems

PREFIX = "/api/v1"
METHODS = ("get", "post", "put", "patch", "delete")


def operations(app: FastAPI) -> list[dict]:
    """Every operation of the API from its OpenAPI document: method, path, operationId and the roles it allows."""
    out = []
    for path, item in app.openapi()["paths"].items():
        for method in METHODS:
            op = item.get(method)
            if op is not None:
                out.append(
                    {"method": method.upper(), "path": path, "id": op["operationId"], "roles": op.get("x-roles")}
                )
    return out


ROUTERS = (system, structuring, trading, market, risk, quant, admin)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    st = getattr(app.state, "optlab", None)
    if st is not None:  # the job threads, then the database file
        st.desk.jobs.shutdown()
        st.desk.db.close()


def api_app(docs: bool = False) -> FastAPI:
    """The routes alone, without a desk: what the OpenAPI document describes (`optlab.server.openapi`)."""
    app = FastAPI(
        lifespan=_lifespan,
        title="optlab",
        version=version("optlab-server"),
        summary="Option pricing, structured products and risk: the desk's API (ADR-0005).",
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=None,  # our own page below: a pinned Swagger UI with its integrity, not FastAPI's floating @5
        redoc_url=None,
        generate_unique_id_function=lambda route: route.name,  # operationId = the endpoint's name, unique
    )
    install_problems(app)
    for module in ROUTERS:
        app.include_router(module.router, prefix=PREFIX)
    base = app.openapi

    def openapi() -> dict:
        if app.openapi_schema is None:
            describe_problems(base())  # base() keeps the document in app.openapi_schema
        doc = app.openapi_schema
        assert doc is not None
        return doc

    app.openapi = openapi
    if docs:
        app.add_api_route(f"{PREFIX}/docs", docs_page, methods=["GET"], include_in_schema=False)
    return app


# Swagger UI from a CDN, pinned and checked by its integrity (the files are those of the npm tarball, whose sha512 the
# registry signs); off unless `Settings.docs` (OPTLAB_DOCS=true), because the page runs a third-party script on the
# desk's origin (ADR-0021).
SWAGGER = "5.34.0"
SWAGGER_JS = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER}/swagger-ui-bundle.js",
    "sha384-9Td7MKiIr2S83E1kZyyVi8KdJw/S8CpAvu0llLyMeg0xq/hDGiZJfVn0fV4llCmK",
)
SWAGGER_CSS = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER}/swagger-ui.css",
    "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF",
)
DOCS_INIT = (
    f'window.ui = SwaggerUIBundle({{url: "{PREFIX}/openapi.json", dom_id: "#ui", deepLinking: true, '
    "persistAuthorization: false, withCredentials: true});"
)


def docs_page() -> HTMLResponse:
    """The API's interactive documentation: sign in with POST /auth/login, then paste its csrf_token in Authorize."""
    digest = base64.b64encode(hashlib.sha256(DOCS_INIT.encode()).digest()).decode()
    csp = (
        f"default-src 'none'; script-src https://cdn.jsdelivr.net 'sha256-{digest}'; "
        "style-src https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>optlab API v1</title>'
        f'<link rel="stylesheet" href="{SWAGGER_CSS[0]}" integrity="{SWAGGER_CSS[1]}" crossorigin="anonymous">'
        '</head><body><div id="ui"></div>'
        f'<script src="{SWAGGER_JS[0]}" integrity="{SWAGGER_JS[1]}" crossorigin="anonymous"></script>'
        f"<script>{DOCS_INIT}</script></body></html>"
    )
    return HTMLResponse(html, headers={"content-security-policy": csp, "cache-control": "no-store"})


PROBLEM_REF = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}


def describe_problems(doc: dict) -> None:
    """Errors as the API sends them (RFC 9457), not FastAPI's default 422 body: a `Problem` schema, 422 on every
    operation that reads input, 401 and 403 on every one that needs a session."""
    schemas = doc.setdefault("components", {}).setdefault("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        schemas.pop(name, None)
    schemas["FieldError"] = {
        "type": "object",
        "required": ["field", "message"],
        "properties": {
            "field": {"type": "string", "description": "dotted path of the field at fault (coupon.barrier)"},
            "message": {"type": "string"},
        },
    }
    schemas["Problem"] = {
        "type": "object",
        "required": ["type", "title", "status", "detail"],
        "description": "RFC 9457 problem details; other members may follow (the term-sheet form adds its form)",
        "properties": {
            "type": {"type": "string", "example": "about:blank"},
            "title": {"type": "string"},
            "status": {"type": "integer"},
            "detail": {"type": "string"},
            "errors": {"type": "array", "items": {"$ref": "#/components/schemas/FieldError"}},
        },
        "additionalProperties": True,
    }
    doc["components"]["securitySchemes"] = {
        "session": {"type": "apiKey", "in": "cookie", "name": COOKIE, "description": "set by POST /auth/login"},
        "csrf": {
            "type": "apiKey",
            "in": "header",
            "name": CSRF_HEADER,
            "description": "the csrf_token of POST /auth/login or GET /auth/me, on every state-changing request",
        },
    }
    for item in doc.get("paths", {}).values():
        for method in METHODS:
            op = item.get(method)
            if op is None:
                continue
            if "public" not in (op.get("x-roles") or []):
                op["security"] = [{"session": []}] if method == "get" else [{"session": [], "csrf": []}]
            responses = op.setdefault("responses", {})
            if "422" in responses:
                responses["422"] = {
                    "description": "invalid input: the fields at fault in `errors`",
                    "content": PROBLEM_REF,
                }
            if "public" not in (op.get("x-roles") or []):
                responses.setdefault("401", {"description": "not signed in", "content": PROBLEM_REF})
                responses.setdefault(
                    "403", {"description": "a role or the CSRF header is missing", "content": PROBLEM_REF}
                )
            responses.setdefault("default", {"description": "any other error", "content": PROBLEM_REF})


def create_app(settings: Settings | None = None, desk: Desk | None = None) -> FastAPI:
    """The served application: the routes over a desk, the accounts and the audit log, the front's build if any, and
    the guard around everything."""
    settings = settings or Settings.load()
    if desk is None:
        desk = Desk(settings=settings, engine=settings.engine, workers=settings.workers)
    desk.shared = settings.lan  # raw quotes and index levels stay on this machine when the LAN profile is on
    if getattr(desk, "risk", None) is None:
        desk.risk = RiskApi(desk)
    app = api_app(docs=settings.docs)
    app.state.optlab = State(settings, desk, Accounts(desk.db, settings), Audit(desk.db))
    if settings.frontend is not None:
        app.mount("/", StaticFiles(directory=settings.frontend, html=True), name="frontend")
    app.add_middleware(Guard, settings=settings)
    return app
