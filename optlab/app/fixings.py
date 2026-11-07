"""Fixings typed or pasted by the user: `date level` lines, two pasted spreadsheet columns, or a CSV export with a
header (its Close column is taken, as in a Yahoo download). Dates as 2026-07-27 or 27/07/2026."""

from __future__ import annotations

import datetime as dt
import re

SPLIT = re.compile(r"[,;\t]|\s+")
DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _date(cell: str) -> str | None:
    m = DMY.match(cell)
    if m:
        cell = f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    try:
        return dt.date.fromisoformat(cell).isoformat()
    except ValueError:
        return None


def parse_fixings(text: str) -> tuple[dict, list]:
    """({iso date: level}, [lines that are not a date and a positive level])."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    col = 1
    if lines and not lines[0][0].isdigit():  # a header row
        head = [c.strip().lower() for c in SPLIT.split(lines.pop(0).lower().replace("adj close", "adj_close")) if c]
        if "close" in head[1:]:
            col = head.index("close")
    levels, skipped = {}, []
    for line in lines:
        cells = [c for c in SPLIT.split(line) if c]
        day = _date(cells[0]) if cells else None
        try:
            level = float(cells[col])
        except IndexError, ValueError:
            level = float("nan")
        if day is not None and level > 0:
            levels[day] = level
        else:
            skipped.append(line)
    return levels, skipped
