"""NumPy timings of the optlab library for the GPU benchmark table (handoff, section 6).

Not the same algorithm as the CUDA engine: optlab simulates Heston by full-truncation Euler on a graded grid
(more than 252 steps), and stores every path, so N is capped by memory as well as by time. When the capped N is
below the requested N, the time is extrapolated linearly and flagged.

    .venv/Scripts/python cuda/bench/bench_numpy.py   (from the repository root)
"""

import csv
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from optlab import BlackScholesModel, HestonModel, HestonParams, Market, barrier_mc, european_mc  # noqa: E402
from optlab.models import time_grid  # noqa: E402

TIME_BUDGET = 5.0      # seconds per repetition
MEMORY_BUDGET = 4e9    # bytes for the stored paths (two float64 arrays of n_paths x n_obs)
REPS = 5

market = Market.flat(100.0, 0.04, 0.01)
heston = HestonModel(HestonParams(**json.loads((ROOT / "results" / "spx_calibration.json").read_text())["heston"]))
bs = BlackScholesModel(0.2)

CASES = [  # name, n_obs, steps of the CUDA engine, sizes, function(n, seed)
    ("BS European", 1, 1, [10**6, 10**7, 10**8],
     lambda n, s: european_mc(bs, market, 100, 1.0, "call", n_paths=n, rng=s)),
    ("BS barrier", 252, 252, [10**6, 10**7],
     lambda n, s: barrier_mc(bs, market, 100, 80, 1.0, "down-out", "call", n_paths=n, rng=s)),
    ("Heston European", 1, 252, [10**5, 10**6, 10**7],
     lambda n, s: european_mc(heston, market, 100, 1.0, "call", n_paths=n, rng=s)),
    ("Heston barrier", 252, 252, [10**5, 10**6, 10**7],
     lambda n, s: barrier_mc(heston, market, 100, 80, 1.0, "down-out", "call", n_paths=n, rng=s)),
]


def timed(fn, n, seed):
    t = time.perf_counter()
    res = fn(n, seed)
    return time.perf_counter() - t, res


def main():
    rows = []
    for name, n_obs, _, sizes, fn in CASES:
        grid_steps = 1 if name.startswith("BS") and n_obs == 1 else len(time_grid(
            [1.0] if n_obs == 1 else [(i + 1) / n_obs for i in range(n_obs)], 1 / 252,
            None if name.startswith("BS") else 1 / 2000)) - 1
        probe = 20_000
        timed(fn, probe, 0)  # warm-up
        rate = probe / timed(fn, probe, 0)[0]
        for n in sizes:
            cap = min(n, int(rate * TIME_BUDGET), int(MEMORY_BUDGET / (16 * n_obs)))
            cap -= cap % 2
            runs = [timed(fn, cap, 1 + k) for k in range(REPS)]
            ts = [t for t, _ in runs]
            med = statistics.median(ts)
            row = {"product": name, "n_requested": n, "n_measured": cap, "python_grid_steps": grid_steps,
                   "median_s": med, "min_s": min(ts), "max_s": max(ts), "time_for_n_s": med * n / cap,
                   "extrapolated": cap < n, "price": runs[0][1].price, "stderr": runs[0][1].stderr}
            rows.append(row)
            print(f"{name:16s} N {n:>11,d} measured {cap:>11,d} ({grid_steps} steps): median {med:.3f} s "
                  f"[{min(ts):.3f}, {max(ts):.3f}] -> {row['time_for_n_s']:.2f} s for N"
                  f"{' (extrapolated)' if cap < n else ''}; price {row['price']:.4f} +- {row['stderr']:.4f}",
                  flush=True)
    out = Path(__file__).with_name("numpy_results.csv")
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("wrote", out)


if __name__ == "__main__":
    main()
