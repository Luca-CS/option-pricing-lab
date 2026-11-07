"""Replace the SLV leverage stored in a results/*.json calibration by the full one (every time step).

Earlier runs of run_calibration.py kept one time step in three of the leverage, which is piecewise constant in time:
the stored SLV was not the one validated. This recalibrates the leverage with the stored surface, Heston parameters,
mix, particles and the calibration seed, checks that one step in three of it reproduces the stored values, and
writes every step. Nothing else in the file changes.

Usage: python scripts/rewrite_leverage.py [--source spx] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optlab import HestonParams, Market, SVISurface, calibrate_leverage  # noqa: E402

SEED = 2026  # pipeline.calibrate_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="spx")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = ROOT / "results" / f"{args.source}_calibration.json"
    cal = json.loads(path.read_text())
    surface = SVISurface.from_dict(cal["surface"])
    t0 = time.time()
    slv = calibrate_leverage(
        HestonParams(**cal["heston"]),
        surface,
        Market.from_dict(cal["market"]),
        float(surface.maturities[-1]),
        mix=cal["slv"]["mix"],
        n_particles=cal["slv"]["particles"],
        rng=SEED,
    )
    lev = slv.leverage
    keep = np.unique(np.concatenate([np.arange(0, len(lev.times), 3), [len(lev.times) - 1]]))
    stored = cal["slv"]["leverage"]
    gap = max(
        float(np.max(np.abs(np.round(lev.values[keep], 5) - np.array(stored["values"])))),
        float(np.max(np.abs(np.round(lev.times[keep], 6) - np.array(stored["times"])))),
    )
    print(
        f"recalibrated in {time.time() - t0:.1f} s: {len(lev.times)} steps (stored: {len(stored['times'])}); "
        f"largest gap to the stored values on their steps {gap:.1e}"
    )
    if gap > 1e-5:
        raise SystemExit("the recalibration does not reproduce the stored leverage: not rewriting")
    cal["slv"]["leverage"] = {
        "times": np.round(lev.times, 6).tolist(),
        "x_grids": np.round(lev.x_grids, 5).tolist(),
        "values": np.round(lev.values, 5).tolist(),
    }
    if args.dry_run:
        print("dry run: nothing written")
        return
    path.write_text(json.dumps(cal, indent=1))
    print("wrote", path)


if __name__ == "__main__":
    main()
