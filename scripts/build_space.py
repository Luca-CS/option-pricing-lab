"""Assemble the Hugging Face Space in build/space/: app, library, calibrated parameters and charts.

Nothing from data/ is copied: the Space only ships derived results.
    python scripts/build_space.py
    hf upload Luca-CS/option-pricing-lab build/space . --repo-type space
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "space"

if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "results").mkdir(parents=True)
    (OUT / "figures").mkdir()
    for name in ("app.py", "README.md", "requirements.txt"):
        shutil.copy(ROOT / "space" / name, OUT / name)
    shutil.copytree(ROOT / "optlab", OUT / "optlab", ignore=shutil.ignore_patterns("__pycache__", "data.py"))
    shutil.copy(ROOT / "results" / "spx_calibration.json", OUT / "results")
    for fig in (ROOT / "reports" / "figures").glob("spx_*.png"):
        shutil.copy(fig, OUT / "figures")
    shutil.copy(ROOT / "LICENSE", OUT / "LICENSE")
    files = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*") if p.is_file())
    print(f"{len(files)} files in {OUT}:")
    print("\n".join("  " + f for f in files))
