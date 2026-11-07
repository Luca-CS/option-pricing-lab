"""The versioned stub of the compiled module (optlab.native._ext) is the one nanobind generates from the built module:
pyright reads it, so the CI type-checks the native package without compiling it (ADR-0003, ADR-0008)."""

import subprocess
import sys
from pathlib import Path

import pytest

ext = pytest.importorskip("optlab.native._ext", reason="the compiled module is not built")
STUB = Path(__file__).resolve().parents[1] / "src" / "optlab" / "native" / "_ext.pyi"

pytestmark = pytest.mark.native


def test_the_versioned_stub_matches_the_built_module(tmp_path):
    out = tmp_path / "_ext.pyi"
    subprocess.run(
        [sys.executable, "-m", "nanobind.stubgen", "-m", "optlab.native._ext", "-o", str(out), "-q"],
        check=True,
        capture_output=True,
        timeout=120,
    )
    assert out.read_text(encoding="utf-8") == STUB.read_text(encoding="utf-8"), (
        "regenerate it: uv run python -m nanobind.stubgen -m optlab.native._ext -o " + str(STUB)
    )
