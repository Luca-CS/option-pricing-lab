"""Hedges as trades: the instruments a desk hedges its structured book with, and their valuation.

An instrument is a trade like a note (`Trade.product = {"kind": "instrument", "instrument": {...}}`), so it lives in the
same versioned table and the same book, with the same Greeks, scenarios, VaR, P&L and EOD. Linear instruments (futures,
cash, swaps, deposits, dividend swaps and futures) are valued in closed form on a context's market and never simulated;
`instruments.py` holds them, `contracts.py` the listed contracts' specifications.
"""

from optlab.structured.hedges.instruments import (
    LINEAR_TYPES,
    TYPES,
    HedgeError,
    Instrument,
    LinearEnv,
    SpreadCurve,
    Valuation,
    is_instrument,
    parse,
)

__all__ = [
    "LINEAR_TYPES",
    "TYPES",
    "HedgeError",
    "Instrument",
    "LinearEnv",
    "SpreadCurve",
    "Valuation",
    "is_instrument",
    "parse",
]
