"""The costs of hedging instruments: the cost table's rows for what the desk hedges with, versioned with the rest of it.

Each value is a half bid/ask spread, so a trade of size q costs |q| times its unit's half-spread to enter, and as much to
leave. The starting values are round assumptions, labelled as such on the screen until a trader corrects them:
- index futures: half a tick;
- cash (an index basket or its ETF): one basis point of the price;
- interest rate swaps: half a basis point on the fixed rate, over the government curve plus a swap spread per currency
  (the swap curve's proxy, 0 bp to start);
- deposits and funding swaps: two basis points on the rate;
- dividend swaps and futures: 1 % of the year's dividend points;
- OTC vanillas: half a vol point on the surface's mid (a listed vanilla takes the chain's bid and ask instead).
"""

from __future__ import annotations

import math

from optlab.structured.hedges.contracts import dividend_future, index_future
from optlab.structured.hedges.instruments import (
    CURRENCIES,
    Cash,
    Deposit,
    DividendFutureContract,
    DividendSwap,
    FundingSwap,
    Future,
    Instrument,
    Irs,
    Valuation,
)

STARTING = {
    "future_ticks": 0.5,
    "cash_bp": 1.0,
    "irs_bp": 0.5,
    "deposit_bp": 2.0,
    "dividend_pct": 1.0,
    "otc_vol_points": 0.5,
    "swap_spread_bp": {c: 0.0 for c in CURRENCIES},
}
FIELDS = (
    ("future_ticks", "Index futures, half-spread", "ticks", 0.0, 20.0),
    ("cash_bp", "Cash and ETF, half-spread", "bp of the price", 0.0, 100.0),
    ("irs_bp", "Interest rate swaps, half-spread", "bp on the rate", 0.0, 50.0),
    ("deposit_bp", "Deposits and funding swaps, half-spread", "bp on the rate", 0.0, 100.0),
    ("dividend_pct", "Dividend swaps and futures, half-spread", "% of the year's points", 0.0, 20.0),
    ("otc_vol_points", "OTC vanillas, half-spread", "vol points", 0.0, 10.0),
)
SWAP_SPREAD = ("swap_spread_bp", "Swap spread over the government curve", "bp", -200.0, 200.0)


def merged(stored: dict | None) -> dict:
    """The stored rows completed from the starting ones (a row added since the table was saved takes its start)."""
    out = {**STARTING, **{k: v for k, v in (stored or {}).items() if k != "swap_spread_bp"}}
    out["swap_spread_bp"] = {**STARTING["swap_spread_bp"], **((stored or {}).get("swap_spread_bp") or {})}
    return out


def starting(rows: dict) -> list[str]:
    """The rows still at their starting value (shown as assumptions)."""
    out = [k for k, *_ in FIELDS if rows.get(k) == STARTING[k]]
    out += [f"swap_spread_bp.{c}" for c, v in rows["swap_spread_bp"].items() if v == STARTING["swap_spread_bp"].get(c)]
    return out


def _bounded(value, what: str, lo: float, hi: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number, not {value!r}")
    x = float(value)
    if not math.isfinite(x) or not lo <= x <= hi:
        raise ValueError(f"{what} = {value!r} is outside {lo:g} to {hi:g}")
    return x


def validate(rows: dict) -> dict:
    """The rows as stored, or ValueError with what is wrong."""
    if not isinstance(rows, dict):
        raise ValueError("the hedging rows are an object")
    known = {k for k, *_ in FIELDS} | {"swap_spread_bp"}
    unknown = set(rows) - known
    if unknown:
        raise ValueError(f"unknown hedging row(s): {', '.join(sorted(unknown))}")
    out = {}
    for key, label, _unit, lo, hi in FIELDS:
        if key in rows:
            out[key] = _bounded(rows[key], label, lo, hi)
    spreads = rows.get("swap_spread_bp")
    if spreads is not None:
        if not isinstance(spreads, dict) or set(spreads) - set(CURRENCIES):
            raise ValueError(f"the swap spread is given per currency: {', '.join(CURRENCIES)}")
        _, label, _unit, lo, hi = SWAP_SPREAD
        out["swap_spread_bp"] = {c: _bounded(v, f"{label} ({c})", lo, hi) for c, v in spreads.items()}
    return out


def swap_spreads(rows: dict) -> dict:
    """{currency: fraction}: what the swap curve adds to the government curve."""
    return {c: v * 1e-4 for c, v in merged(rows)["swap_spread_bp"].items()}


def half_spread(inst: Instrument, v: Valuation, rows: dict) -> float:
    """The half bid/ask spread of one unit, in the payment currency: what entering (or leaving) one unit costs."""
    r = merged(rows)
    if isinstance(inst, Future):
        spec = index_future(inst.name)
        return r["future_ticks"] * spec.tick * float(inst.multiplier or spec.multiplier)
    if isinstance(inst, Cash):
        return r["cash_bp"] * 1e-4 * inst.value(v)
    if isinstance(inst, Irs):
        return r["irs_bp"] * 1e-4 * inst.annuity(v)
    if isinstance(inst, (Deposit, FundingSwap)):
        return r["deposit_bp"] * 1e-4 * inst.annuity(v)
    if isinstance(inst, DividendSwap):
        return r["dividend_pct"] * 0.01 * abs(inst.points(v)) * v.D(inst.maturity)
    if isinstance(inst, DividendFutureContract):
        spec = dividend_future(inst.name)
        return r["dividend_pct"] * 0.01 * abs(inst.points(v)) * float(inst.multiplier or spec.multiplier)
    raise ValueError(f"no closed-form cost of a {inst.type}: a vanilla's comes from the surface or the chain")
