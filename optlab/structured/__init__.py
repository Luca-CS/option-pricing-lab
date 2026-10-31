"""Structured equity products: a small payoff scripting language, term-sheet templates and pricers.

Layers: the engine (dates, script language, scenarios, cash flows, note and swap wrappers), the product builder
(term-sheet schema, product masks, compiler to scripts) and the analysis (solver, Greeks, pricing policy).
"""

from __future__ import annotations

from .calendars import CALENDARS, NYSECalendar
from .cashflows import Note, Swap
from .compiler import compile_sheet
from .context import PricingContext
from .lifecycle import Event, events
from .policy import RULES, decide
from .portfolio import Book, BookResult, Position
from .presets import PRESETS, preset
from .pricer import StructuredResult, price, price_many
from .qmc import price_rqmc
from .quote import Quote, quote
from .schema import MASKS, TermSheet, TermSheetError, describe, json_schema
from .script.lexer import ScriptError
from .trade import Trade
from .script.parser import parse
from .script.printer import to_text
from .script.validate import validate

__all__ = ["Note", "Swap", "PricingContext", "StructuredResult", "price", "price_many", "ScriptError", "parse",
           "to_text", "validate", "compile_sheet", "RULES", "decide", "PRESETS", "preset", "Quote", "quote", "MASKS",
           "TermSheet", "TermSheetError", "describe", "json_schema", "Book", "BookResult", "Position", "Trade",
           "CALENDARS", "NYSECalendar", "Event", "events", "price_rqmc"]
