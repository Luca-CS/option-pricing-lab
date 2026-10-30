"""Payoff language: parsing, printing, validation errors and evaluation semantics on hand-made paths."""

import numpy as np
import pytest

from optlab.structured.scenario import Scenario
from optlab.structured.script.evaluator import Evaluator
from optlab.structured.script.lexer import ScriptError
from optlab.structured.script.nodes import Binary, Name, Num, Unary
from optlab.structured.script.parser import parse
from optlab.structured.script.printer import expr, to_text
from optlab.structured.script.timeline import build_timeline
from optlab.structured.script.validate import validate

PHOENIX = """\
underlying SPX
param N = 100
param cpn = 0.02          # per period
param ac = 1.00
param cb = 0.70
param ki = 0.60

event 2026-10-27
  S0 = SPX
  missed = 0
end

event 2027-01-27 pay 2027-02-03
  perf = SPX / S0
  if perf >= cb then
    pay N * cpn * (1 + missed) as coupon
    missed = 0
  else
    missed = missed + 1
  end
  if perf >= ac then
    pay N as redemption
    terminate
  end
end

event 2027-04-27 pay 2027-05-04
  perf = SPX / S0
  if perf >= cb then
    pay N * cpn * (1 + missed) as coupon
  end
  if perf >= ki then
    pay N as redemption
  else
    pay N * perf as redemption
  end
end
"""


def run(script, spots, times=None, params=None, smoothing=0.0):
    """Evaluate a time-mode script on given paths: spots is (n_paths, n_times) at the script's times."""
    program = validate(parse(script))
    tl = build_timeline(program)
    spots = np.atleast_2d(np.asarray(spots, dtype=float))
    scenario = Scenario("SPX", tl.sim_times if times is None else times, spots, 100.0)
    return Evaluator(program, tl, params, smoothing).run(scenario)


# --- parsing and printing ---

def test_phoenix_example_parses():
    p = validate(parse(PHOENIX))
    assert p.underlying_names == ("SPX",)
    assert p.param_values == {"N": 100, "cpn": 0.02, "ac": 1.0, "cb": 0.7, "ki": 0.6}
    assert [e.when.value for e in p.events] == ["2026-10-27", "2027-01-27", "2027-04-27"]
    assert p.events[1].pay.value == "2027-02-03"


def test_round_trip_print_parse():
    p = parse(PHOENIX)
    text = to_text(p)
    assert parse(text) == p
    assert to_text(parse(text)) == text


@pytest.mark.parametrize("src", [
    "a - (b - c)", "(a - b) - c", "-(a + b) * c", "a / (b * c)", "a * -b", "--a",
    "not (a < b and c > d) or e == f", "(a < b) < c", "a < (b < c)", "not not a", "(a or b) and c",
    "max(a, min(b, -c)) / abs(log(exp(a)))", "1e-05 * a + 0.1 + 3", "worst(a, b, c) - best(a, -b)",
    "lowest(SPX, t=0, t=1.5, n=252) / highest(SPX, 2026-10-27, 2027-10-27)",
])
def test_expression_round_trip(src):
    script = f"underlying SPX\nparam a = 1\nparam b = 2\nparam c = 3\nparam d = 4\nparam e = 5\nparam f = 6\n" \
             f"event t=1\n  x = {src}\nend\n"
    p = parse(script)
    e = p.events[0].body[0].expr
    assert parse(script.replace(src, expr(e))) == p


def test_printer_parenthesises_by_precedence():
    tree = Binary("-", Name("a"), Binary("-", Name("b"), Name("c")))
    assert expr(tree) == "a - (b - c)"
    assert expr(Unary("-", Binary("+", Name("a"), Num(2.0)))) == "-(a + 2)"


# --- errors carry the line ---

@pytest.mark.parametrize("script, line, message", [
    ("underlying SPX\nevent t=1 pay t=1\n  pay x as redemption\nend\n", 3, "'x' is used before it is assigned"),
    ("underlying SPX\nevent t=1\n  pay 1 as redemption\nend\n", 3, "without a payment date"),
    ("".join(f"underlying U{i}\n" for i in range(6)) + "event t=1\nend\n", 6, "at most 5 underlyings"),
    ("underlying SPX\nunderlying SPX\nevent t=1\nend\n", 2, "declared twice"),
    ("underlying SPX\nevent t=1\nend\nevent t=0.5\nend\n", 4, "strictly increasing"),
    ("underlying SPX\nevent t=1\nend\nevent 2027-01-01\nend\n", 4, "cannot be mixed"),
    ("underlying SPX\nevent t=1 pay t=1\n  pay 1 as bonus\nend\n", 3, "unknown flow tag"),
    ("underlying SPX\nevent t=1\n  x = lowest(SPX, t=0, t=2, n=10)\nend\n", 3, "after the event date"),
    ("underlying SPX\nevent t=1\n  x = lowest(SPX, t=0, t=1)\nend\n", 3, "needs the number of fixings"),
    ("underlying SPX\nparam K = 1\nevent t=1\n  K = 2\nend\n", 4, "cannot assign"),
    ("underlying SPX\nevent t=1\n  if SPX > 1 then\n    x = 1\n  end\n  y = x\nend\n", 6, "'x' is used before"),
])
def test_validation_errors_have_line_numbers(script, line, message):
    with pytest.raises(ScriptError, match=message) as err:
        validate(parse(script))
    assert err.value.line == line


@pytest.mark.parametrize("script, line, col, message", [
    ("underlying SPX\nevent t=1\n  x = (1 + 2\nend\n", 3, 13, "expected \\)"),
    ("underlying SPX\nevent t=1\n  x = 1 +\nend\n", 3, 10, "expected an expression"),
    ("underlying SPX\nevent t=1\n  x = foo(1)\nend\n", 3, 7, "unknown function"),
    ("underlying SPX\nevent t=1\n  x = 1 < 2 < 3\nend\n", 3, 13, "chained comparisons"),
    ("underlying SPX\nevent t=1\n  x = 1\n", 4, 1, "missing 'end'"),
    ("underlying SPX\nevent t=1\n  x = 1 $ 2\nend\n", 3, 9, "unexpected character"),
    ("underlying SPX\nevent t=1\n  x = max(1)\nend\n", 3, 7, "takes 2 argument"),
    ("underlying SPX\nevent t=1\n  x = worst(1)\nend\n", 3, 7, "takes at least 2 argument"),
])
def test_syntax_errors_have_line_and_column(script, line, col, message):
    with pytest.raises(ScriptError, match=message) as err:
        parse(script)
    assert (err.value.line, err.value.col) == (line, col)


def test_missing_redemption_on_some_paths_is_an_error():
    script = "underlying SPX\nevent t=1 pay t=1\n  if SPX > 100 then\n    pay 100 as redemption\n  end\nend\n"
    with pytest.raises(ScriptError, match="not paid exactly once on 50.00%"):
        run(script, [[90.0], [110.0]])


def test_double_redemption_is_an_error():
    script = "underlying SPX\nevent t=1 pay t=1\n  pay 1 as redemption\n  pay 1 as redemption\nend\n"
    with pytest.raises(ScriptError, match="not paid exactly once"):
        run(script, [[90.0], [110.0]])


# --- evaluation semantics, on hand-made paths ---

PHOENIX_T = PHOENIX.replace("2026-10-27", "t=0").replace("2027-01-27 pay 2027-02-03", "t=0.25 pay t=0.25") \
    .replace("2027-04-27 pay 2027-05-04", "t=0.5 pay t=0.5")


def test_phoenix_semantics_on_four_paths():
    # columns: t=0.25, t=0.5 ; S0 = 100
    spots = [[105, 50],   # called at the first date with its coupon
             [80, 90],    # coupon twice, no call, capital back
             [60, 75],    # first coupon missed, paid with memory at the second date
             [60, 50]]    # no coupon, capital loss N * 0.5
    out = run(PHOENIX_T, spots)
    cpn = out.flows[(0.25, "coupon")] + out.flows[(0.5, "coupon")]
    np.testing.assert_allclose(cpn, [2, 4, 4, 0])
    red = out.flows[(0.25, "redemption")] + out.flows[(0.5, "redemption")]
    np.testing.assert_allclose(red, [100, 100, 100, 50])
    np.testing.assert_array_equal(out.terminated[1], [1, 0, 0, 0])
    np.testing.assert_array_equal(out.redemption_weights[0.25], [1, 0, 0, 0])


def test_params_override_and_unknown_param():
    out = run(PHOENIX_T, [[80, 90]], params={"cpn": 0.05})
    assert out.flows[(0.25, "coupon")][0] == pytest.approx(5.0)
    with pytest.raises(KeyError, match="unknown parameter"):
        run(PHOENIX_T, [[80, 90]], params={"coupon": 0.05})


def test_masked_assignment_keeps_old_value_and_nested_masks_multiply():
    script = """underlying SPX
event t=1 pay t=1
  x = 1
  if SPX > 100 then
    x = 2
    if SPX > 120 then
      x = 3
    else
      x = 4
    end
  end
  pay x as redemption
end
"""
    out = run(script, [[90.0], [110.0], [130.0]])
    np.testing.assert_array_equal(out.flows[(1.0, "redemption")], [1, 4, 3])


def test_lowest_and_highest_windows():
    script = """underlying SPX
event t=1 pay t=1
  lo = lowest(SPX, t=0, t=1, n=4)
  hi = highest(SPX, t=0, t=1, n=4)
  pay lo + 1000 * hi as redemption
end
"""
    out = run(script, [[101, 99, 103, 98], [100, 100, 100, 100]])
    np.testing.assert_array_equal(out.flows[(1.0, "redemption")], [98 + 103_000, 100 + 100_000])


def test_logic_operators_on_masks():
    script = """underlying SPX
event t=1 pay t=1
  a = SPX > 100
  b = SPX < 120
  pay 1 * (a and b) + 10 * (a or b) + 100 * (not a) as redemption
end
"""
    out = run(script, [[90.0], [110.0], [130.0]])
    np.testing.assert_array_equal(out.flows[(1.0, "redemption")], [110, 11, 10])


def test_smoothing_turns_a_digital_into_a_ramp():
    script = "underlying SPX\nevent t=1 pay t=1\n  if SPX >= 100 then\n    pay 1 as redemption\n  else\n" \
             "    pay 0 as redemption\n  end\nend\n"
    out = run(script, [[98.0], [99.5], [100.0], [100.5], [102.0]], smoothing=0.02)
    np.testing.assert_allclose(out.flows[(1.0, "redemption")], [0.0, 0.25, 0.5, 0.75, 1.0])


def test_terminate_stops_later_payments():
    script = """underlying SPX
event t=1 pay t=1
  if SPX > 100 then
    pay 100 as redemption
    terminate
  end
  pay 1 as coupon
end
event t=2 pay t=2
  pay 5 as coupon
  pay 100 as redemption
end
"""
    out = run(script, [[110.0, 1.0], [90.0, 1.0]])
    np.testing.assert_array_equal(out.flows[(1.0, "coupon")], [0, 1])
    np.testing.assert_array_equal(out.flows[(2.0, "coupon")], [0, 5])


def test_worst_and_best_take_any_number_of_arguments():
    script = "underlying SPX\nevent t=1 pay t=1\n  pay worst(SPX, 95, 120) + 1000 * best(SPX, 95, 50, 101)" \
             " as redemption\nend\n"
    out = run(script, [[90.0], [110.0]])
    np.testing.assert_array_equal(out.flows[(1.0, "redemption")], [90 + 101_000, 95 + 110_000])
