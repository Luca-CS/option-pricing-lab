"""Where the library reads and writes: one place instead of paths spread over the modules.

`OPTLAB_HOME` moves the root (default: the repository), `OPTLAB_STORE` the local store of snapshots, trades and
runs (default: data/store, git-ignored like all of data/).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 2026  # quotes, Greeks and books; the low-level `price` keeps seed 0, as in products.py


@dataclass(frozen=True)
class Paths:
    root: Path
    results: Path  # versioned calibrations (parameters only, spot rescaled to 100)
    data: Path  # personal-use market data, never versioned
    store: Path  # local snapshots, trades, fixings and runs


def paths() -> Paths:
    root = Path(os.environ.get("OPTLAB_HOME") or REPO_ROOT)
    data = root / "data"
    return Paths(root, root / "results", data, Path(os.environ.get("OPTLAB_STORE") or data / "store"))
