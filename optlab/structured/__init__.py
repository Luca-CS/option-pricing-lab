"""Structured equity products: a small payoff scripting language, term-sheet templates and pricers.

Layers: the engine (dates, script language, scenarios, cash flows, note and swap wrappers), the product builder
(term-sheet schema, product masks, compiler to scripts) and the analysis (solver, Greeks, pricing policy).
"""

from __future__ import annotations

__all__: list[str] = []
