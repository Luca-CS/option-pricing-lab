"""The versioned OpenAPI document of API v1 (handoff §4.2, ADR-0005): `api/openapi.json` at the root of the repo.

    optlab api export [--out api/openapi.json]   # write it (or python -m optlab.server.openapi export)
    optlab api check  [--out api/openapi.json]   # exit 1 when the file is not the code's

`test_openapi.py` fails when the file drifts from the routes, as the stub test does for the native module. The
document is built from the routes alone (`api_app`): no desk, no database, no market.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from optlab.server.app import api_app

DEFAULT = Path(__file__).resolve().parents[5] / "api" / "openapi.json"


def document() -> dict[str, Any]:
    return api_app().openapi()


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    from optlab.server.cli import run

    return run(["api", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
