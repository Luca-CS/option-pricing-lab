"""Typed accessors of optional results, for the tests that arranged them (pyright cannot know they did): a quote's desk
price, and any optional value the test expects to be there."""

from optlab.structured.costs import DeskPrice


def desk_of(q) -> DeskPrice:
    """The desk price of a quote asked with costs."""
    assert q.desk is not None, "the quote was asked without costs"
    return q.desk


def some[T](x: T | None) -> T:
    """x, which the test expects to be there (a solved coupon, a control price, Greeks asked for, Monte Carlo pairs)."""
    assert x is not None, "expected a value, got None"
    return x
