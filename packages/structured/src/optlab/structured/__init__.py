"""Structured equity products: a small payoff scripting language, term-sheet templates and pricers.

Layers: the engine (dates, script language, scenarios, cash flows, note and swap wrappers), the product builder
(term-sheet schema, product masks, compiler to scripts) and the analysis (solver, Greeks, pricing policy).
"""

from __future__ import annotations

from optlab.structured.calendars import CALENDARS, NYSECalendar
from optlab.structured.cashflows import Note, Swap
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.lifecycle import Event, events
from optlab.structured.policy import RULES, decide
from optlab.structured.portfolio import Book, BookResult, Position
from optlab.structured.presets import PRESETS, preset
from optlab.structured.pricer import StructuredResult, price, price_many
from optlab.structured.qmc import price_rqmc
from optlab.structured.quote import Quote, quote
from optlab.structured.schema import MASKS, TermSheet, TermSheetError, describe, json_schema
from optlab.structured.script.lexer import ScriptError
from optlab.structured.script.parser import parse
from optlab.structured.script.printer import to_text
from optlab.structured.script.validate import validate
from optlab.structured.trade import Trade

__all__ = [
    "Note",
    "Swap",
    "PricingContext",
    "StructuredResult",
    "price",
    "price_many",
    "ScriptError",
    "parse",
    "to_text",
    "validate",
    "compile_sheet",
    "RULES",
    "decide",
    "PRESETS",
    "preset",
    "Quote",
    "quote",
    "MASKS",
    "TermSheet",
    "TermSheetError",
    "describe",
    "json_schema",
    "Book",
    "BookResult",
    "Position",
    "Trade",
    "CALENDARS",
    "NYSECalendar",
    "Event",
    "events",
    "price_rqmc",
]
