"""The desk page's own logic run under node: the order of a sorted table column (optlab.server's static/js/ui.js).
Needs node, like tests/test_widget_js.py."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from optlab.server.server import STATIC

UI = STATIC / "js" / "ui.js"
NODE = shutil.which("node")
pytestmark = [pytest.mark.node, pytest.mark.skipif(NODE is None, reason="node is not installed")]


def run_js(expr):
    """Evaluate `expr` with the functions of ui.js in scope (its exports turned into plain declarations)."""
    source = UI.read_text(encoding="utf-8").replace("export function", "function").replace("export const", "const")
    script = f"{source}\nconsole.log(JSON.stringify({expr}));"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.js"
        path.write_text(script, encoding="utf-8")
        out = subprocess.run([NODE, str(path)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout)


def order(texts, direction=1):
    return run_js(f"sortOrder({json.dumps(texts)}, {direction})")


def test_numbers_with_errors_units_and_signs():
    cells = ["98.000 ± 0.034", "65.6 %", "+0.183", "–", "1,234.5", "-2.5"]
    assert order(cells) == [5, 2, 1, 0, 4, 3]
    assert order(cells, -1) == [4, 0, 1, 2, 5, 3]  # the empty cell stays last


def test_dates_as_shown_and_timestamps():
    assert order(["27 Jan 2027", "03 Feb 2027", "27 Oct 2026"]) == [2, 0, 1]
    assert order(["2026-11-01 04:27", "2026-11-01 00:48", "2026-10-31 23:59"]) == [2, 1, 0]


def test_text_compares_numeric_parts_and_keeps_ties():
    assert order(["AUT-0010-aaaa", "AUT-0002-bbbb", "v10", "v2"]) == [1, 0, 3, 2]
    assert order(["b", "a", "b", "a"]) == [1, 3, 0, 2]
    assert order(["12", "AUT-1", "3"]) == [2, 0, 1]  # one text cell: the column sorts as text, numbers naturally
