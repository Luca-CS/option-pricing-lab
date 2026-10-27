"""The JavaScript pricer of the demo page (docs/pricer.js) against the Python library. Needs node."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from optlab import barrier_price, barrier_shift_discrete, binomial_price, bs_price, geometric_asian_price

JS = Path(__file__).resolve().parent.parent / "docs" / "pricer.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

CASES = [  # S, K, T, r, q, vol, call
    (100, 100, 1.0, 0.04, 0.01, 0.20, True), (100, 90, 0.5, 0.03, 0.0, 0.35, False),
    (100, 120, 2.0, 0.05, 0.02, 0.15, True), (100, 105, 0.25, 0.0, 0.01, 0.25, False),
]
BARRIERS = [("down-out", 80), ("down-in", 85), ("up-out", 120), ("up-in", 130)]


def run_js(expr):
    script = f"global.window = {{}};\n{JS.read_text(encoding='utf-8')}\nconst W = window.PricerWidget;\n" \
             f"console.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout)


def test_black_scholes_and_tree():
    js = run_js("[" + ",".join(f"[W.bs({S},{K},{T},{r},{q},{v},{str(c).lower()}).price,"
                               f"W.crr({S},{K},{T},{r},{q},{v},{str(c).lower()},400,true),"
                               f"W.crr({S},{K},{T},{r},{q},{v},{str(c).lower()},400,false)]"
                               for S, K, T, r, q, v, c in CASES) + "]")
    for (S, K, T, r, q, v, c), (eu, am_tree, eu_tree) in zip(CASES, js):
        cp = "call" if c else "put"
        assert eu == pytest.approx(bs_price(S, K, T, r, q, v, cp), abs=1e-9)
        # the page shows the exact European plus the tree's early-exercise premium
        am = eu + am_tree - eu_tree
        assert am >= eu - 1e-12
        assert am == pytest.approx(binomial_price(S, K, T, r, q, v, cp, True, 2000), abs=0.005)


def test_geometric_asian():
    js = run_js("[" + ",".join(f"W.geoAsian({S},{K},{T},{r},{q},{v},12,{str(c).lower()})"
                               for S, K, T, r, q, v, c in CASES) + "]")
    for (S, K, T, r, q, v, c), price in zip(CASES, js):
        fix = np.linspace(T / 12, T, 12)
        exact = float(geometric_asian_price(S, K, r, q, v, fix, "call" if c else "put"))
        assert price == pytest.approx(exact, abs=1e-9)


def test_barriers_continuous_and_daily():
    calls = []
    for S, K, T, r, q, v, c in CASES:
        for kind, H in BARRIERS:
            calls.append(f"[W.barrier({S},{K},{H},{T},{r},{q},{v},'{kind}',{str(c).lower()},false),"
                         f"W.barrier({S},{K},{H},{T},{r},{q},{v},'{kind}',{str(c).lower()},true)]")
    js = iter(run_js("[" + ",".join(calls) + "]"))
    for S, K, T, r, q, v, c in CASES:
        for kind, H in BARRIERS:
            cont, daily = next(js)
            cp = "call" if c else "put"
            assert cont == pytest.approx(barrier_price(S, K, H, T, r, q, v, kind, cp), abs=1e-9)
            shifted = barrier_shift_discrete(H, S, v, 1 / 252)
            assert daily == pytest.approx(barrier_price(S, K, shifted, T, r, q, v, kind, cp), abs=1e-9)


def test_asian_monte_carlo_is_unbiased():
    S, K, T, r, q, v = 100, 100, 1.0, 0.04, 0.01, 0.2
    res = run_js(f"W.asianMC({S},{K},{T},{r},{q},{v},12,true,40000,7)")
    from optlab import BlackScholesModel, Market, asian_mc

    ref = asian_mc(BlackScholesModel(v), Market.flat(S, r, q), K, T, 12, "call", n_paths=400_000, rng=1)
    assert abs(res["price"] - ref.price) < 4 * np.hypot(res["se"], ref.stderr)
