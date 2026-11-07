"""pyright against a recorded baseline: the type errors of v0 may only go down.

    uv run python tools/pyright_baseline.py            # fails if any (file, rule) has more errors than the baseline
    uv run python tools/pyright_baseline.py --update   # rewrites the baseline (reviewed in the pull request)

The baseline (`pyright-baseline.json`) counts errors per file and per rule, so a fix in one place cannot hide a new
error elsewhere. Warnings are not counted. The clean-up of A8 brings every count to zero (ADR-0014).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "pyright-baseline.json"


def current() -> Counter:
    out = subprocess.run([sys.executable, "-m", "pyright", "--outputjson"], cwd=ROOT, capture_output=True, text=True)
    try:
        report = json.loads(out.stdout[out.stdout.index("{") :])
    except ValueError:
        raise SystemExit(f"pyright gave no JSON report:\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}") from None
    counts: Counter = Counter()
    for d in report["generalDiagnostics"]:
        if d["severity"] == "error":
            rel = Path(d["file"]).resolve().relative_to(ROOT).as_posix()
            counts[f"{rel}::{d.get('rule', 'unknown')}"] += 1
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args(argv)
    now = current()
    if args.update:
        BASELINE.write_text(json.dumps(dict(sorted(now.items())), indent=1) + "\n", encoding="utf-8")
        print(f"baseline written: {sum(now.values())} errors in {len({k.split('::')[0] for k in now})} files")
        return 0
    base = Counter(json.loads(BASELINE.read_text(encoding="utf-8")))
    worse = {k: (base.get(k, 0), n) for k, n in now.items() if n > base.get(k, 0)}
    better = sum(max(base[k] - now.get(k, 0), 0) for k in base)
    print(f"pyright: {sum(now.values())} errors (baseline {sum(base.values())}, {better} fixed)")
    for k, (was, n) in sorted(worse.items()):
        print(f"  more errors: {k}: {was} -> {n}")
    if better and not worse:
        print("  the baseline can go down: run with --update")
    return 1 if worse else 0


if __name__ == "__main__":
    raise SystemExit(main())
