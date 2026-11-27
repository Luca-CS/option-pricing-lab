"""Linear hedging instruments against independent references: discounting their flows by hand on a flat market (rate r,
dividend yield q, a flat bank spread), the forward, and the integral of the dividend yield along the forward."""

import datetime as dt
import math

import numpy as np
import pytest

from optlab.core.market import Market
from optlab.structured.hedges import HedgeError, SpreadCurve, Valuation, parse
from optlab.structured.hedges.contracts import quarterly_expiries, third_friday
from optlab.structured.hedges.instruments import forward_dividends

R, Q, S0, SPREAD = 0.04, 0.015, 100.0, 0.006
TODAY = dt.date(2026, 11, 26)
FLAT = Market.flat(S0, R, Q)
BANK = SpreadCurve.flat(SPREAD)


def v(scale=60.0, since=None, swap_spread=0.0, valuation=TODAY):
    return Valuation(FLAT, valuation, scale, BANK, swap_spread, since)


def t(d) -> float:
    return (d - TODAY).days / 365.0


def test_a_future_is_its_forward_less_its_price_times_the_multiplier():
    expiry = third_friday(2027, 3)
    f = parse({"type": "future", "name": "SPX", "expiry": expiry.isoformat(), "price": 6010.0})
    T = t(expiry)
    assert f.value(v()) == pytest.approx(50.0 * (60.0 * S0 * math.exp((R - Q) * T) - 6010.0), rel=1e-12)
    assert f.label == "ES Mar27 future" and f.currency == "USD" and f.underlying == "SPX"
    assert parse(f.to_dict()) == f


def test_quarterly_expiries_are_third_fridays():
    got = quarterly_expiries(dt.date(2026, 12, 18), 3)
    assert got == [dt.date(2027, 3, 19), dt.date(2027, 6, 18), dt.date(2027, 9, 17)]
    assert all(d.weekday() == 4 for d in got)


def test_cash_is_the_real_spot():
    c = parse({"type": "cash", "name": "SX5E", "price": 5100.0})
    assert c.value(v(scale=51.0)) == pytest.approx(5100.0) and c.currency == "EUR"


def _annual(start, end, months=12):
    """Period ends rolled back from the end, by hand."""
    ends, k = [end], 1
    while True:
        y, m = divmod(end.month - 1 - months * k, 12)
        d = dt.date(end.year + y, m + 1, min(end.day, 28))
        if d <= start:
            break
        ends.append(d)
        k += 1
    return [start, *sorted(ends)]


def test_a_receiver_swap_is_its_fixed_leg_less_its_floating_leg_discounted_by_hand():
    start, end = dt.date(2026, 12, 1), dt.date(2031, 12, 1)
    swap = parse({"type": "irs", "ccy": "USD", "start": start, "end": end, "rate": 0.042})
    d = _annual(start, end)
    fixed = sum(0.042 * (b - a).days / 365 * math.exp(-R * t(b)) for a, b in zip(d, d[1:]))
    floating = math.exp(-R * t(start)) - math.exp(-R * t(end))  # one curve: the floating leg telescopes
    assert swap.value(v()) == pytest.approx(fixed - floating, rel=1e-9)
    annuity = fixed / 0.042
    par = parse({"type": "irs", "ccy": "USD", "start": start, "end": end, "rate": floating / annuity})
    assert abs(par.value(v())) < 1e-12
    spread = 0.002  # the government curve plus a swap spread: the same by hand on r + spread
    D = lambda x: math.exp(-(R + spread) * t(x))  # noqa: E731
    fixed_s = sum(0.042 * (b - a).days / 365 * D(b) for a, b in zip(d, d[1:]))
    assert swap.value(v(swap_spread=spread)) == pytest.approx(fixed_s - (D(start) - D(end)), rel=1e-9)


def test_a_deposit_is_discounted_on_the_issuer_curve_and_its_flows_in_a_roll_are_cash():
    start, end = TODAY - dt.timedelta(days=30), TODAY + dt.timedelta(days=335)
    dep = parse({"type": "deposit", "ccy": "USD", "start": start, "end": end, "rate": 0.045})
    back = 1 + 0.045 * (end - start).days / 365
    assert dep.value(v()) == pytest.approx(back * math.exp(-(R + SPREAD) * t(end)), rel=1e-12)
    rolled = v(valuation=end, since=end - dt.timedelta(days=1))
    assert dep.value(rolled) == pytest.approx(back, rel=1e-12)  # repaid during the roll: cash
    assert dep.value(v(valuation=end + dt.timedelta(days=1))) == 0.0
    later = TODAY + dt.timedelta(days=10)
    forward = parse({"type": "deposit", "ccy": "USD", "start": later, "end": end, "rate": 0.045})
    tau = (end - later).days / 365
    want = (1 + 0.045 * tau) * math.exp(-(R + SPREAD) * t(end)) - math.exp(-(R + SPREAD) * t(later))
    assert forward.value(v()) == pytest.approx(want, rel=1e-12)


def test_a_funding_swap_pays_the_floating_rate_plus_the_forward_bank_spread():
    start, end = dt.date(2026, 12, 1), dt.date(2029, 12, 1)
    fs = parse({"type": "funding_swap", "ccy": "USD", "start": start, "end": end, "rate": 0.047, "float_months": 3})
    d = _annual(start, end, 3)
    pv = 0.0
    for a, b in zip(d, d[1:]):
        tau = (b - a).days / 365
        rate = (math.exp(R * (t(b) - t(a))) - 1) / (t(b) - t(a))
        spread = (math.exp(SPREAD * (t(b) - t(a))) - 1) / (t(b) - t(a))
        pv += tau * (0.047 - rate - spread) * math.exp(-R * t(b))
    assert fs.value(v()) == pytest.approx(pv, rel=1e-9)
    widened = Valuation(FLAT, TODAY, 1.0, BANK.bumped(lambda T: np.full(np.shape(T), 1e-4)))
    assert widened.bank.spread(2.0) == pytest.approx(SPREAD + 1e-4)
    assert fs.value(widened) < fs.value(v())  # receiving fixed against the spread: a hedge of issued notes


def test_dividends_are_the_integral_of_the_yield_along_the_forward():
    a, b = 0.2, 1.2
    exact = Q * S0 * (math.exp((R - Q) * b) - math.exp((R - Q) * a)) / (R - Q)
    assert forward_dividends(FLAT, a, b) == pytest.approx(exact, rel=1e-6)
    ds = parse({"type": "dividend_swap", "name": "SPX", "year": 2027, "strike": 80.0, "realised": 0.0})
    a, b = t(dt.date(2027, 1, 1)), t(dt.date(2028, 1, 1))
    points = 60.0 * Q * S0 * (math.exp((R - Q) * b) - math.exp((R - Q) * a)) / (R - Q)
    assert ds.value(v()) == pytest.approx((points - 80.0) * math.exp(-R * t(dt.date(2028, 1, 31))), rel=1e-6)
    fut = parse({"type": "dividend_future", "name": "SX5E", "year": 2027, "price": 150.0})
    assert fut.value(v()) == pytest.approx(100.0 * (points - 150.0), rel=1e-6)
    running = parse({"type": "dividend_swap", "name": "SPX", "year": 2026, "strike": 80.0, "realised": 70.0})
    rest = 60.0 * forward_dividends(FLAT, 0.0, t(dt.date(2027, 1, 1)))
    assert running.value(v()) == pytest.approx((70.0 + rest - 80.0) * math.exp(-R * t(dt.date(2027, 1, 31))))


@pytest.mark.parametrize(
    "bad, match",
    [
        ({"type": "swaption"}, "unknown instrument type"),
        ({"type": "future", "name": "SPX", "expiry": "2027-03-19"}, "needs price"),
        ({"type": "future", "name": "SPX", "expiry": "2027-03-19", "price": 6000, "lots": 3}, "unknown field"),
        ({"type": "future", "name": "AAPL", "expiry": "2027-03-19", "price": 200}, "no listed index future"),
        ({"type": "future", "name": "EURUSD", "expiry": "2027-03-19", "price": 1}, "FX pair"),
        ({"type": "irs", "ccy": "CHF", "start": "2026-12-01", "end": "2031-12-01", "rate": 0.01}, "currency"),
        ({"type": "irs", "ccy": "USD", "start": "2026-12-01", "end": "2031-12-01", "rate": 4.2}, "fraction"),
        ({"type": "deposit", "ccy": "USD", "start": "2027-12-01", "end": "2026-12-01", "rate": 0.04}, "after"),
        ({"type": "dividend_swap", "name": "SPX", "year": 2027, "strike": 80, "payment": "2027-06-30"}, "after"),
    ],
)
def test_instruments_are_validated(bad, match):
    with pytest.raises(HedgeError, match=match):
        parse(bad)


def test_a_curve_needs_increasing_tenors_and_a_bank_valuation_needs_a_curve():
    with pytest.raises(HedgeError):
        SpreadCurve("X", (2.0, 1.0), (0.01, 0.02))
    dep = parse({"type": "deposit", "ccy": "USD", "start": "2026-12-01", "end": "2027-12-01", "rate": 0.04})
    with pytest.raises(HedgeError, match="funding curve"):
        dep.value(Valuation(FLAT, TODAY))
    curve = SpreadCurve("X", (1.0, 5.0), (0.002, 0.01))
    assert curve.spread(0.5) == pytest.approx(0.002) and curve.spread(7.0) == pytest.approx(0.01)
    assert float(curve.log_p(3.0)) == pytest.approx(-(0.002 + (0.05 - 0.002) * 0.5))  # ln P linear between pillars


def test_a_book_values_its_instruments_exactly_beside_its_notes():
    """A future in a book of a vanilla script: its value and its delta are the closed forms, the note's error unchanged."""
    from _risk_refs import VALUATION, flat_ctx, vanilla

    from optlab.structured.hedges import LinearEnv
    from optlab.structured.portfolio import Book, Position

    ctx = flat_ctx()
    expiry = third_friday(2027, 6)
    fut = parse({"type": "future", "name": "SPX", "expiry": expiry, "price": 6000.0})
    env = LinearEnv(scales={"SPX": 60.0})
    alone = Book([Position("call", vanilla(100.0, 365), -1.0)])
    hedged = Book([Position("fut", fut, 2.0), Position("call", vanilla(100.0, 365), -1.0)], env)
    assert [p.name for p in hedged.positions] == ["call", "fut"]  # simulated first, then linear
    a = alone.price(ctx, model="lv", n_paths=4000, seed=3)
    b = hedged.price(ctx, model="lv", n_paths=4000, seed=3)
    T = (expiry - VALUATION).days / 365
    F = float(ctx.market.F(T))
    want = 2.0 * 50.0 * (60.0 * F - 6000.0)
    assert b.positions[1].value == pytest.approx(want, rel=1e-12) and b.positions[1].product_class == "linear"
    assert b.value == pytest.approx(a.value + want, rel=1e-12) and b.stderr == pytest.approx(a.stderr, rel=1e-12)
    g = hedged.greeks(ctx, model="lv", n_paths=4000, seed=3)
    assert g["delta"]["positions"][1] == pytest.approx(2.0 * 50.0 * 60.0 * F * 0.01, rel=1e-9)
    assert g["delta"]["positions_se"][1] < 1e-9 and abs(g["vega"]["positions"][1]) < 1e-9  # exact, up to rounding
    only = Book([Position("fut", fut, 2.0)], env).price(ctx)
    assert only.method == "closed_form" and only.value == pytest.approx(want, rel=1e-12)
