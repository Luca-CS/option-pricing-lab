"""Recompute the Black-Scholes Asian cell of the exotics table in results/*.json with the control variate on the
forward curve (the earlier one used constant rates and was biased on the SPX curve). Nothing else changes.

Usage: python scripts/fix_asian_exotic.py [--source spx]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optlab import BlackScholesModel, Market, SVISurface, asian_mc  # noqa: E402

KEY, MODEL = "Asian call, 12 monthly fixings, K 100 %", "Black-Scholes (ATM vol)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="spx")
    args = ap.parse_args()
    path = ROOT / "results" / f"{args.source}_calibration.json"
    cal = json.loads(path.read_text())
    market, surface = Market.from_dict(cal["market"]), SVISurface.from_dict(cal["surface"])
    S0, T = market.spot, 1.0  # as pipeline.exotics_table: 200,000 paths, seed 11
    res = asian_mc(BlackScholesModel(float(surface.iv(0.0, T))), market, S0, T, 12, "call", n_paths=200_000, rng=11)
    old = cal["exotics"][KEY][MODEL]
    cal["exotics"][KEY][MODEL] = {"price": res.price / S0 * 100, "stderr": res.stderr / S0 * 100}
    path.write_text(json.dumps(cal, indent=1))
    print(f"{KEY}, {MODEL}: {old['price']:.4f} ({old['stderr']:.4f}) -> {res.price / S0 * 100:.4f} "
          f"({res.stderr / S0 * 100:.4f}); wrote {path}")


if __name__ == "__main__":
    main()
