"""Standard logging under the "optlab" logger: silent by default, `configure()` turns it on."""

from __future__ import annotations

import logging
from pathlib import Path

logging.getLogger("optlab").addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("optlab") else f"optlab.{name}")


def configure(level: str = "INFO", file: str | Path | None = None) -> logging.Logger:
    """Send optlab logs to stderr (or to a file) at `level`; calling it again replaces the handler."""
    root = logging.getLogger("optlab")
    for h in list(root.handlers):
        if not isinstance(h, logging.NullHandler):
            root.removeHandler(h)
    handler = logging.FileHandler(file, encoding="utf-8") if file else logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    return root
