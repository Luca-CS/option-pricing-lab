"""NumPy timings of the optlab library for the GPU benchmark table (handoff, section 6): `optlab bench numpy`.

Not the same algorithm as the CUDA engine: optlab simulates Heston by full-truncation Euler on a graded grid
(more than 252 steps), and stores every path, so N is capped by memory as well as by time. When the capped N is
below the requested N, the time is extrapolated linearly and flagged. The CSV goes where `bench.exe` reads it,
`packages/native/cuda/bench/numpy_results.csv` of the checkout (or `--out`); the Heston parameters are those of the
settings' calibration.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from pathlib import Path

from optlab.core import BlackScholesModel, HestonModel, HestonParams, Market, barrier_mc, european_mc
from optlab.core.models import time_grid
from optlab.server.settings import Settings, repository_root
from optlab.server.tools import say

TIME_BUDGET = 5.0  # seconds per repetition
MEMORY_BUDGET = 4e9  # bytes for the stored paths (two float64 arrays of n_paths x n_obs)
REPS = 5


def cases(calibration: Path) -> list:
    """Name, observations, steps of the CUDA engine, sizes, function(n, seed)."""
    market = Market.flat(100.0, 0.04, 0.01)
    heston = HestonModel(HestonParams(**json.loads(Path(calibration).read_text())["heston"]))
    bs = BlackScholesModel(0.2)
    return [
        (
            "BS European",
            1,
            1,
            [10**6, 10**7, 10**8],
            lambda n, s: european_mc(bs, market, 100, 1.0, "call", n_paths=n, rng=s),
        ),
        (
            "BS barrier",
            252,
            252,
            [10**6, 10**7],
            lambda n, s: barrier_mc(bs, market, 100, 80, 1.0, "down-out", "call", n_paths=n, rng=s),
        ),
        (
            "Heston European",
            1,
            252,
            [10**5, 10**6, 10**7],
            lambda n, s: european_mc(heston, market, 100, 1.0, "call", n_paths=n, rng=s),
        ),
        (
            "Heston barrier",
            252,
            252,
            [10**5, 10**6, 10**7],
            lambda n, s: barrier_mc(heston, market, 100, 80, 1.0, "down-out", "call", n_paths=n, rng=s),
        ),
    ]


def timed(fn, n, seed):
    t = time.perf_counter()
    res = fn(n, seed)
    return time.perf_counter() - t, res


def main(out: Path | None = None, log=say) -> Path:
    if out is None:
        repo = repository_root()
        if repo is None:
            raise RuntimeError("bench numpy writes beside bench.exe's sources: run it from a checkout, or pass --out")
        out = repo / "packages" / "native" / "cuda" / "bench" / "numpy_results.csv"
    rows = []
    for name, n_obs, _, sizes, fn in cases(Settings.load().calibration):
        grid_steps = (
            1
            if name.startswith("BS") and n_obs == 1
            else len(
                time_grid(
                    [1.0] if n_obs == 1 else [(i + 1) / n_obs for i in range(n_obs)],
                    1 / 252,
                    None if name.startswith("BS") else 1 / 2000,
                )
            )
            - 1
        )
        probe = 20_000
        timed(fn, probe, 0)  # warm-up
        rate = probe / timed(fn, probe, 0)[0]
        for n in sizes:
            cap = min(n, int(rate * TIME_BUDGET), int(MEMORY_BUDGET / (16 * n_obs)))
            cap -= cap % 2
            runs = [timed(fn, cap, 1 + k) for k in range(REPS)]
            ts = [t for t, _ in runs]
            med = statistics.median(ts)
            row = {
                "product": name,
                "n_requested": n,
                "n_measured": cap,
                "python_grid_steps": grid_steps,
                "median_s": med,
                "min_s": min(ts),
                "max_s": max(ts),
                "time_for_n_s": med * n / cap,
                "extrapolated": cap < n,
                "price": runs[0][1].price,
                "stderr": runs[0][1].stderr,
            }
            rows.append(row)
            log(
                f"{name:16s} N {n:>11,d} measured {cap:>11,d} ({grid_steps} steps): median {med:.3f} s "
                f"[{min(ts):.3f}, {max(ts):.3f}] -> {row['time_for_n_s']:.2f} s for N"
                f"{' (extrapolated)' if cap < n else ''}; price {row['price']:.4f} +- {row['stderr']:.4f}"
            )
    out = Path(out)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    log("wrote", out)
    return out
