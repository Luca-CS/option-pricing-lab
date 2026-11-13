"""Monte Carlo numbers as the API shows them: `{"value": v, "se": s}` (ADR-0005, api.md §5).

The library and the stores keep their own shapes (a price beside its `stderr`, a delta beside its `delta_se`, a risk
measure as a `[value, se]` pair): stored payloads, reports and the risk engine read them, so they do not change. The
API converts at its boundary, by three rules applied everywhere in a payload:

1. a key `x` beside `x_se` or `x_stderr` becomes `x: {value, se}` (element by element when both are maps or lists);
2. `price` (else `value`) beside `stderr`, `unit_price` beside `unit_stderr`, `x_price` beside `x_se`, and a cost line's
   `cost` beside its `se`, the same;
3. in a risk payload, the `[value, se]` pairs of a measure map (`greeks`, `book`, `gross`, `values`, `pnl`,
   `factors`) and the pairs `book` and `smoothing_bias`.

A number without an error (a closed form, a replication) stays a number; `se` is null where the library has none.
Errors that stand alone keep their name: a book's `independent_stderr` (the error of its value if its positions were
priced on independent paths) and a quote's `control_gap_se` (a gap counted in standard errors).
"""

from __future__ import annotations

from numbers import Real

SUFFIXES = ("_se", "_stderr")
RISK_MAPS = ("greeks", "book", "gross", "values", "pnl", "factors")
RISK_PAIRS = ("book", "smoothing_bias")


def _num(x) -> bool:
    return isinstance(x, Real) and not isinstance(x, bool)


def _pairable(x) -> bool:
    return x is None or _num(x)


def estimate(value, se):
    """{value, se}, element by element through maps and lists of the same shape."""
    if isinstance(value, dict) and isinstance(se, dict):
        return {k: estimate(v, se.get(k)) for k, v in value.items()}
    if isinstance(value, list) and isinstance(se, list) and len(value) == len(se):
        return [estimate(v, s) for v, s in zip(value, se)]
    if value is None:
        return None
    return {"value": value, "se": se if _pairable(se) else None}


def _is_pair(x) -> bool:
    return isinstance(x, list) and len(x) == 2 and _num(x[0]) and _pairable(x[1])


def estimates(x, risk: bool = False):
    """The payload with every Monte Carlo number as {value, se} (rules above); `risk` turns on rule 3."""
    if isinstance(x, list):
        return [estimates(v, risk) for v in x]
    if not isinstance(x, dict):
        return x
    out = {k: estimates(v, risk) for k, v in x.items()}
    for key in list(out):
        if key not in out:
            continue
        for suffix in SUFFIXES:
            base = key[: -len(suffix)]
            if key.endswith(suffix) and base in out and (_pairable(out[base]) or isinstance(out[base], (dict, list))):
                out[base] = estimate(out[base], out.pop(key))
                break
    for key in list(out):  # base_price beside base_se (the desk price's lines of costs.py)
        if key.endswith("_price") and f"{key[:-6]}_se" in out and _pairable(out[key]):
            out[key] = estimate(out[key], out.pop(f"{key[:-6]}_se"))
    if "se" in out and "cost" in out and "value" not in out:  # a line of the desk price
        out["cost"] = estimate(out["cost"], out.pop("se"))
    if "unit_stderr" in out and "unit_price" in out:
        out["unit_price"] = estimate(out["unit_price"], out.pop("unit_stderr"))
    if "stderr" in out:
        base = "price" if "price" in out else "value" if "value" in out else None
        if base is not None:
            out[base] = estimate(out[base], out.pop("stderr"))
    if risk:
        for key in RISK_MAPS:
            v = out.get(key)
            if isinstance(v, dict):
                out[key] = {k: ({"value": p[0], "se": p[1]} if _is_pair(p) else p) for k, p in v.items()}
        for key in RISK_PAIRS:
            if _is_pair(out.get(key)):
                out[key] = {"value": out[key][0], "se": out[key][1]}
    return out


RISK_KINDS = ("risk", "ladder", "scenarios", "var", "eod")


def result(kind: str, envelope: dict | None) -> dict | None:
    """A job's stored envelope {market, method, result} as the API shows it."""
    if envelope is None:
        return None
    return {**envelope, "result": estimates(envelope.get("result"), risk=kind in RISK_KINDS)}
