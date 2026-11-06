"""Views of a `RiskReport`: Greeks per name, buckets per tenor, the vega matrix, net / gross / weighted, by position or for the book.

Measure keys are "greek|name|bucket" (`greeks.plan`): "delta|SPX|", "vega_node|SPX|100%@1y", "rho_tenor|USD|1y". Sums over
groups are exact for the values; their standard errors add in quadrature (groups run on independent seeds).
"""

from __future__ import annotations

import numpy as np

from . import grid


def split(key: str) -> tuple[str, str, str]:
    greek, name, bucket = (key.split("|") + ["", ""])[:3]
    return greek, name, bucket


def source(report, position: str | None = None) -> dict:
    if position is None:
        return report.book
    return next(p for p in report.positions if p["name"] == position)["greeks"]


def names(report) -> list[str]:
    """Underlyings of the report, in the order they first appear."""
    out = []
    for k in report.book:
        g, n, _ = split(k)
        if g == "delta" and n not in ("*", "") and n not in out:
            out.append(n)
    return out


def name_table(report, position: str | None = None) -> dict:
    """{name: {greek: (value, se)}} of the scalar Greeks."""
    src = source(report, position)
    out: dict = {}
    for k, v in src.items():
        g, n, b = split(k)
        if b:
            continue
        out.setdefault(n, {})[g] = tuple(v)
    return out


def tenor_buckets(report, greek: str, name: str, position: str | None = None) -> list:
    """[(tenor label, value, se)] of "vega_tenor", "rho_tenor" or "epsilon_tenor" for a name (or currency)."""
    src = source(report, position)
    out = []
    for label in grid.TENOR_LABELS:
        v = src.get(f"{greek}|{name}|{label}")
        if v is not None:
            out.append((label, v[0], v[1]))
    return out


def vega_matrix(report, name: str, position: str | None = None):
    """(values, se) of the node vegas as strikes x tenors arrays, or None without node vegas."""
    src = source(report, position)
    vals = np.full((len(grid.STRIKES), len(grid.TENORS)), np.nan)
    ses = np.full_like(vals, np.nan)
    found = False
    for i, k in enumerate(grid.STRIKE_LABELS):
        for j, t in enumerate(grid.TENOR_LABELS):
            v = src.get(f"vega_node|{name}|{k}@{t}")
            if v is not None:
                vals[i, j], ses[i, j] = v
                found = True
    return (vals, ses) if found else None


def vega_summary(report, name: str, position: str | None = None) -> dict:
    """Net, gross and weighted vega of a name: gross over the nodes when they were computed, else over the tenors."""
    src = source(report, position)
    m = vega_matrix(report, name, position)
    buckets = np.array([b[1] for b in tenor_buckets(report, "vega_tenor", name, position)])
    gross = float(np.nansum(np.abs(m[0]))) if m is not None else float(np.sum(np.abs(buckets)))
    return {"parallel": tuple(src.get(f"vega|{name}|", (np.nan, np.nan))), "net": tuple(src.get(f"vega_net|{name}|", (np.nan, np.nan))),
            "gross": gross, "gross_over": "nodes" if m is not None else "tenors",
            "weighted": tuple(src.get(f"vega_weighted|{name}|", (np.nan, np.nan))),
            "weighted_direct": tuple(src.get(f"vega_weighted_direct|{name}|", (np.nan, np.nan))),
            "relative": tuple(src.get(f"vega_relative|{name}|", (np.nan, np.nan))),
            "nonlinearity": float(src[f"vega_net|{name}|"][0] - src[f"vega|{name}|"][0]) if f"vega_net|{name}|" in src else None}


def gamma_summary(report, position: str | None = None) -> dict:
    """Net (signed sum over names) and gross (sum of absolute values over names and positions) gamma."""
    rows = report.positions if position is None else [p for p in report.positions if p["name"] == position]
    net = gross = 0.0
    for p in rows:
        for k, v in p["greeks"].items():
            g, n, b = split(k)
            if g == "gamma" and n not in ("*", "") and not b:
                net += v[0]
                gross += abs(v[0])
    return {"net": net, "gross": gross}


def rho_summary(report, currency: str = "USD", position: str | None = None) -> dict:
    src = source(report, position)
    buckets = tenor_buckets(report, "rho_tenor", currency, position)
    return {"total": tuple(src.get(f"rho|{currency}|", (np.nan, np.nan))), "discount": tuple(src.get(f"rho_discount|{currency}|", (np.nan, np.nan))),
            "forward": tuple(src.get(f"rho_forward|{currency}|", (np.nan, np.nan))), "net": float(sum(b[1] for b in buckets)),
            "gross": float(sum(abs(b[1]) for b in buckets)), "buckets": buckets}
