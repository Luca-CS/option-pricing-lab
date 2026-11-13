"""Parity with v0 (handoff §7.2): every v1 route of docs/architecture/feature-inventory.md, section 1, exists in the
OpenAPI document, and every route of the API is either in the inventory or named here as new. Replaces v0's
`test_routes_and_screens_are_wired`; the screens are session 2's, checked against the same inventory."""

import re
from pathlib import Path

import pytest
from _api import api

from optlab.server.app import operations

INVENTORY = Path(__file__).resolve().parents[3] / "docs" / "architecture" / "feature-inventory.md"
METHOD = r"(GET|POST|PUT|PATCH|DELETE)"
LATER: dict[tuple[str, str], str] = {}  # routes of the inventory that a later step brings, with that step
NEW = {  # routes v0 had no equivalent for: accounts replace the shared token (A5), the worker's queue and schedules (A6)
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/me"),
    ("GET", "/admin/users"),
    ("POST", "/admin/users"),
    ("PATCH", "/admin/users/{}"),
    ("GET", "/admin/audit"),
    ("GET", "/admin/jobs"),
    ("PATCH", "/admin/pipelines/{}"),
    ("POST", "/admin/pipelines/{}/run"),
}


def _norm(path: str) -> str:
    return re.sub(r"\{\w+(?::path)?\}", "{}", path.split("?")[0])


def inventory_routes(text: str) -> set[tuple[str, str]]:
    """(method, path) of the v1 column of every kept or merged row of section 1; `GET`, `PUT /x` gives both."""
    out = set()
    for line in text.split("\n## 2.")[0].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("| ") or len(cells) < 3 or cells[0] in ("v0 route", "v0") or set(cells[0]) <= {"-"}:
            continue
        if "dropped" in cells[-1]:
            continue
        pending = []
        for tok in re.findall(r"`([^`]*)`", cells[2]):
            m = re.fullmatch(METHOD + r"(?: (/\S*))?(?: .*)?", tok)
            if m is None:
                continue
            if m.group(2) is None:
                pending.append(m.group(1))
                continue
            out |= {(meth, _norm(m.group(2))) for meth in (*pending, m.group(1))}
            pending = []
    return out


@pytest.fixture(scope="module")
def api_routes(tmp_path_factory):
    with api(tmp_path_factory.mktemp("inventory")) as a:
        return {(op["method"], _norm(op["path"].removeprefix("/api/v1"))) for op in operations(a.app)}


def test_every_route_of_the_inventory_exists(api_routes):
    wanted = inventory_routes(INVENTORY.read_text(encoding="utf-8"))
    assert len(wanted) >= 65
    assert wanted - api_routes == set(LATER), sorted(wanted - api_routes - set(LATER))


def test_every_route_is_in_the_inventory_or_new(api_routes):
    wanted = inventory_routes(INVENTORY.read_text(encoding="utf-8"))
    assert api_routes - wanted == NEW, sorted(api_routes - wanted - NEW)
