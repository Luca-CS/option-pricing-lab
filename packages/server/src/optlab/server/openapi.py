"""The versioned OpenAPI document of API v1 (handoff §4.2, ADR-0005): `api/openapi.json` at the root of the repo.

    python -m optlab.server.openapi export [--out api/openapi.json]   # write it
    python -m optlab.server.openapi check  [--out api/openapi.json]   # exit 1 when the file is not the code's

`test_openapi.py` fails when the file drifts from the routes, as the stub test does for the native module. The
document is built from the routes alone (`api_app`): no desk, no database, no market. `optlab api export` in A7.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from optlab.server.app import api_app

DEFAULT = Path(__file__).resolve().parents[5] / "api" / "openapi.json"


def document() -> dict:
    return api_app().openapi()


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m optlab.server.openapi",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("command", choices=("export", "check"))
    ap.add_argument("--out", type=Path, default=DEFAULT)
    args = ap.parse_args(argv)
    doc = document()
    if args.command == "export":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(doc), encoding="utf-8", newline="\n")
        print(f"wrote {args.out} ({len(doc['paths'])} paths)")
        return 0
    current = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else None
    if current != json.loads(render(doc)):
        print(f"{args.out} is not the code's document: run python -m optlab.server.openapi export", file=sys.stderr)
        return 1
    print(f"{args.out} is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
