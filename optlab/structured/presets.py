"""Named products as pre-filled term sheets. A preset is only a starting point: every field can be changed."""

from __future__ import annotations

from .schema import TermSheet

PRESETS = {
    "reverse_convertible": (
        "bullet",
        {
            "general": {"maturity_months": 12},
            "coupon": {"type": "fixed", "frequency_months": 3},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 1.0, "observation": "european"},
        },
    ),
    "barrier_reverse_convertible": (
        "bullet",
        {
            "general": {"maturity_months": 12},
            "coupon": {"type": "fixed", "frequency_months": 3},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 0.7, "observation": "daily"},
        },
    ),
    "phoenix": (
        "autocall",
        {
            "general": {"maturity_months": 24},
            "autocall": {"frequency_months": 3, "trigger": 1.0},
            "coupon": {"type": "conditional", "frequency_months": 3, "barrier": 0.7, "memory": True},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 0.6, "observation": "european"},
        },
    ),
    "athena": (
        "autocall",
        {
            "general": {"maturity_months": 24},
            "autocall": {"frequency_months": 3, "trigger": 1.0},
            "coupon": {"type": "snowball"},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 0.6, "observation": "european"},
        },
    ),
    "airbag": (
        "bullet",
        {
            "general": {"maturity_months": 24},
            "wrapper": {"solve": "upfront"},
            "coupon": {"type": "none"},
            "upside": {"type": "call", "strike": 1.0, "participation": 1.0},
            "downside": {"type": "geared_put", "strike": 0.8},
        },
    ),
    "capital_protected": (
        "bullet",
        {
            "general": {"maturity_months": 24},
            "wrapper": {"solve": "upfront"},
            "coupon": {"type": "none"},
            "upside": {"type": "call", "strike": 1.0, "participation": 0.5},
            "downside": {"type": "none"},
        },
    ),
}


def preset(name: str, **changes) -> TermSheet:
    """A preset term sheet, with optional dotted-path changes: preset("phoenix", **{"coupon.barrier": 0.6})."""
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}, expected one of {', '.join(PRESETS)}")
    mask, values = PRESETS[name]
    sheet = TermSheet(mask, values)
    return sheet.replace(**changes) if changes else sheet
