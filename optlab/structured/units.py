"""Units of every number the structured layer returns, so that outputs say what they are."""

from __future__ import annotations

UNITS = {
    "price": "% of notional",
    "value": "currency",
    "stderr": "same unit as the number it qualifies",
    "coupon_rate": "fraction per annum (0.08 = 8 % p.a.)",
    "margin": "% of notional",
    "levels": "fraction of the initial fixing (0.6 = 60 %)",
    "delta": "% of notional per +1 % spot (sticky moneyness)",
    "vega": "% of notional per +1 implied vol point (parallel shift)",
    "book_delta": "currency per +1 % spot (sticky moneyness)",
    "book_vega": "currency per +1 implied vol point (parallel shift)",
}

GREEK_UNITS = {k: UNITS[k] for k in ("delta", "vega")}
BOOK_UNITS = {"delta": UNITS["book_delta"], "vega": UNITS["book_vega"], "value": UNITS["value"]}
