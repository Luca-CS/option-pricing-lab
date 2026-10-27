"""The JavaScript pricers of the demo page (docs/lab.js) against the Python library. Needs node."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from optlab import barrier_price, barrier_shift_discrete, binomial_price, bs_price, geometric_asian_price

JS = Path(__file__).resolve().parent.parent / "docs" / "lab.js"
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
    with tempfile.TemporaryDirectory() as tmp:  # too long for a Windows command line
        path = Path(tmp) / "run.js"
        path.write_text(script, encoding="utf-8")
        out = subprocess.run([NODE, str(path)], capture_output=True, text=True, check=True, timeout=60)
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


def test_levy_asian_approximation_is_close():
    from optlab import BlackScholesModel, Market, asian_mc

    for K, call in ((90, True), (100, True), (110, False)):
        js = run_js(f"W.levyAsian(100,{K},1.0,0.04,0.01,0.2,12,{str(call).lower()})")
        ref = asian_mc(BlackScholesModel(0.2), Market.flat(100, 0.04, 0.01), K, 1.0, 12, "call" if call else "put",
                       n_paths=200_000, rng=2)
        assert abs(js - ref.price) < 0.005 * ref.price + 4 * ref.stderr  # moment matching: within 0.5 %


def test_longstaff_schwartz_matches_tree():
    expr = ("(() => { const out = []; for (const [r, q, call] of [[0.04, 0.01, false], [0.04, 0.06, true]]) {"
            " const st = {product: 'american', call, model: 'bs', K: 100, T: 1, v: 0.2, r, q};"
            " const c = W.convergence(W.payoffs(st, W.simulate(st, 20000, 50, 3)).pay, 100);"
            " out.push([c[c.length - 1][1], c[c.length - 1][2], W.american(100, 100, 1, r, q, 0.2, call, 600)]); }"
            " return out; })()")
    for lsm, se, tree in run_js(expr):
        # 50 exercise dates against continuous exercise: LSM may sit slightly below the tree
        assert tree - 0.05 - 4 * se < lsm < tree + 4 * se
