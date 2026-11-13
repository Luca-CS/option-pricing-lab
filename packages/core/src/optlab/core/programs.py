"""Where an external program is (curl, git): an absolute path, from the absolute folders of the PATH only.

`subprocess.run(["curl", ...])` with a bare name lets Windows look in the interpreter's folder and the current folder
before the PATH, so a `curl.exe` planted in the working directory would run (security.md, findings 19 and 44). The
packages that start a program resolve it here first and pass the absolute path. Nothing runs at import.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def find(name: str, path: str | None = None) -> str | None:
    """The absolute path of `name` in the absolute folders of `path` (default: the PATH), or None. Relative entries
    ("." included) and empty ones are skipped; on Windows the extensions of PATHEXT are tried."""
    if sys.platform == "win32" and not Path(name).suffix:
        exts = [e for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").lower().split(";") if e]
        names = [name + e for e in exts]
    else:
        names = [name]
    for folder in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if not folder or not os.path.isabs(folder):
            continue
        for candidate in names:
            p = Path(folder) / candidate
            if p.is_file():
                return str(p)
    return None


def require(name: str) -> str:
    """`find(name)`, or FileNotFoundError naming the program."""
    found = find(name)
    if found is None:
        raise FileNotFoundError(f"{name} is not in any absolute folder of the PATH")
    return found
