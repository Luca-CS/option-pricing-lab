"""Structured equity products: a small payoff scripting language, term-sheet templates and pricers.

Layers: the engine (dates, script language, scenarios, cash flows, note and swap wrappers), the product builder
(term-sheet schema, product masks, compiler to scripts) and the analysis (solver, Greeks, pricing policy).
"""

from __future__ import annotations

from .cashflows import Note, Swap
from .compiler import compile_sheet
from .context import PricingContext
from .policy import RULES, decide
from .presets import PRESETS, preset
from .pricer import StructuredResult, price, price_many
from .quote import Quote, quote
from .schema import MASKS, TermSheet, TermSheetError, describe
from .script.lexer import ScriptError
from .script.parser import parse
from .script.printer import to_text
from .script.validate import validate

__all__ = ["Note", "Swap", "PricingContext", "StructuredResult", "price", "price_many", "ScriptError", "parse",
           "to_text", "validate", "compile_sheet", "RULES", "decide", "PRESETS", "preset", "Quote", "quote", "MASKS",
           "TermSheet", "TermSheetError", "describe"]
