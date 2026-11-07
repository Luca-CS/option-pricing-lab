"""Where the library reads and writes: one place instead of paths spread over the modules.

`OPTLAB_HOME` moves the root (default: the repository), `OPTLAB_STORE` the local store of snapshots, trades and
runs, `OPTLAB_LAKE` the historical data lake. Both default to a folder that survives worktrees: `OPTLAB_DATA`, or
`optlab-data` beside the main checkout when it exists, else data/ (git-ignored).
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
    lake: Path  # historical lake: raw files and curated Parquet, never versioned


def _stable_data(home: str | None) -> Path | None:
    """The folder that outlives a worktree, unless OPTLAB_HOME asks for a self-contained root."""
    if os.environ.get("OPTLAB_DATA"):
        return Path(os.environ["OPTLAB_DATA"])
    if home:
        return None
    main = REPO_ROOT.parents[2] if REPO_ROOT.parent.name == "worktrees" else REPO_ROOT
    sibling = main.parent / "optlab-data"
    return sibling if sibling.is_dir() else None


def paths() -> Paths:
    home = os.environ.get("OPTLAB_HOME")
    root = Path(home or REPO_ROOT)
    data = root / "data"
    stable = _stable_data(home) or data
    return Paths(
        root,
        root / "results",
        data,
        Path(os.environ.get("OPTLAB_STORE") or stable / "store"),
        Path(os.environ.get("OPTLAB_LAKE") or stable / "lake"),
    )
