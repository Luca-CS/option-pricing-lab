"""Named products as pre-filled term sheets. A preset is only a starting point: every field can be changed."""

from __future__ import annotations

from optlab.structured.schema import TermSheet

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
    "lizard_phoenix": (
        "autocall",
        {
            "general": {"maturity_months": 24},
            "autocall": {
                "frequency_months": 3,
                "trigger": 1.0,
                "lizard": True,
                "lizard_months": 12,
                "lizard_barrier": 0.75,
                "lizard_coupon": 0.05,
            },
            "coupon": {"type": "conditional", "frequency_months": 3, "barrier": 0.7, "memory": True},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 0.6, "observation": "european"},
        },
    ),
    "asynchronous_autocall": (
        "autocall",
        {
            "general": {"maturity_months": 24, "basket": "worst_of", "underlyings": ("SPX", "NDX")},
            "autocall": {"frequency_months": 3, "trigger": 1.0, "mechanism": "asynchronous"},
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
    "variance_swap": ("variance", {"variance": {"type": "variance", "strike": 0.2, "cap": 2.5}}),
    "volatility_swap": ("variance", {"variance": {"type": "volatility", "strike": 0.2}}),
    "corridor_variance_swap": (
        "variance",
        {"variance": {"type": "variance", "strike": 0.2, "corridor": "range", "lower": 0.8, "upper": 1.1}},
    ),
    "corridor_volatility_swap": (
        "variance",
        {"variance": {"type": "volatility", "strike": 0.2, "corridor": "range", "lower": 0.8, "upper": 1.1}},
    ),
    "accumulator": (
        "accumulator",
        {
            "wrapper": {"format": "swap"},
            "accumulator": {
                "direction": "accumulate",
                "strike": 0.9,
                "knock_out": 1.05,
                "gearing": 2.0,
                "guarantee_months": 1,
            },
        },
    ),
    "decumulator": (
        "accumulator",
        {
            "wrapper": {"format": "swap"},
            "accumulator": {
                "direction": "decumulate",
                "strike": 1.1,
                "knock_out": 0.95,
                "gearing": 2.0,
                "guarantee_months": 1,
            },
        },
    ),
    "funded_accumulator": (
        "accumulator",
        {
            "wrapper": {"format": "note"},
            "accumulator": {
                "direction": "accumulate",
                "strike": 0.9,
                "knock_out": 1.05,
                "gearing": 2.0,
                "guarantee_months": 1,
            },
        },
    ),
    "funded_decumulator": (
        "accumulator",
        {
            "wrapper": {"format": "note"},
            "accumulator": {
                "direction": "decumulate",
                "strike": 1.1,
                "knock_out": 0.95,
                "gearing": 2.0,
                "guarantee_months": 1,
            },
        },
    ),
    "callable_reverse_convertible": (
        "callable",
        {
            "general": {"maturity_months": 24},
            "issuer_call": {"first_call_months": 6, "notice_days": 5},
            "coupon": {"type": "fixed", "frequency_months": 3},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 1.0, "observation": "european"},
        },
    ),
    "callable_barrier_reverse_convertible": (
        "callable",
        {
            "general": {"maturity_months": 24},
            "issuer_call": {"first_call_months": 6, "notice_days": 5},
            "coupon": {"type": "fixed", "frequency_months": 3},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 0.7, "observation": "daily"},
        },
    ),
    "callable_phoenix": (
        "callable",
        {
            "general": {"maturity_months": 36},
            "issuer_call": {"first_call_months": 12, "notice_days": 5},
            "coupon": {"type": "conditional", "frequency_months": 3, "barrier": 0.7, "memory": True},
            "downside": {"type": "pdi", "strike": 1.0, "barrier": 0.6, "observation": "european"},
        },
    ),
    "callable_fixed_coupon_note": (
        "callable",
        {
            "general": {"maturity_months": 60},
            "issuer_call": {"first_call_months": 12, "notice_days": 5},
            "coupon": {"type": "fixed", "frequency_months": 12},
            "downside": {"type": "none"},
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
