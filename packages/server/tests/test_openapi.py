"""The versioned OpenAPI document (`api/openapi.json`, handoff §4.2) is the code's, and it says what the API does:
problem+json errors, the roles of every operation, unique operation ids. Run
`uv run python -m optlab.server.openapi export` after changing a route."""

import json

from optlab.server.app import METHODS
from optlab.server.openapi import DEFAULT, document, main, render


def test_the_versioned_document_is_the_code_s():
    assert DEFAULT.exists(), "api/openapi.json is missing: python -m optlab.server.openapi export"
    on_disk = json.loads(DEFAULT.read_text(encoding="utf-8"))
    assert on_disk == json.loads(render(document())), "api/openapi.json drifted: python -m optlab.server.openapi export"
    assert main(["check"]) == 0


def test_the_document_describes_errors_and_roles():
    doc = document()
    assert doc["openapi"].startswith("3.") and doc["info"]["title"] == "optlab"
    schemas = doc["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and {"Problem", "FieldError"} <= set(schemas)
    text = json.dumps(doc)
    assert "HTTPValidationError" not in text
    ids = []
    for path, item in doc["paths"].items():
        assert path.startswith("/api/v1/")
        for method in METHODS:
            op = item.get(method)
            if op is None:
                continue
            ids.append(op["operationId"])
            assert op.get("x-roles"), (method, path)
            assert "application/problem+json" in op["responses"]["default"]["content"]
            if "public" not in op["x-roles"]:
                assert {"401", "403"} <= set(op["responses"]), (method, path)
    assert len(ids) == len(set(ids)) >= 70


def test_check_fails_on_a_stale_file(tmp_path, capsys):
    stale = tmp_path / "openapi.json"
    stale.write_text("{}", encoding="utf-8")
    assert main(["check", "--out", str(stale)]) == 1
    assert main(["export", "--out", str(stale)]) == 0 and main(["check", "--out", str(stale)]) == 0
