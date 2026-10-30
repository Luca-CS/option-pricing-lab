"""Structured equity products: a small payoff scripting language, term-sheet templates and pricers.

Layers: the engine (dates, script language, scenarios, cash flows, note and swap wrappers), the product builder
(term-sheet schema, product masks, compiler to scripts) and the analysis (solver, Greeks, pricing policy).
"""

from __future__ import annotations

from .cashflows import Note, Swap
from .context import PricingContext
from .pricer import StructuredResult, price, price_many
from .script.lexer import ScriptError
from .script.parser import parse
from .script.printer import to_text
from .script.validate import validate

__all__ = ["Note", "Swap", "PricingContext", "StructuredResult", "price", "price_many", "ScriptError", "parse",
           "to_text", "validate"]
