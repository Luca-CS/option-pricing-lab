"""Structured pricer: the scripting layer against the existing pricers and independent references."""

import numpy as np
import pytest

from optlab import asian_mc, barrier_mc, european_mc
from optlab.products import fixing_dates
from optlab.structured.context import PricingContext
from optlab.structured.pricer import price
from optlab.structured.script.printer import number

CTX = PricingContext.from_calibration()
MODELS = ["bs", "heston", "lv", "slv"]
N_REPRO = 20_000


EUROPEAN = """underlying SPX
param K = 100
event t=1 pay t=1
  pay max({sign}(SPX - K), 0) as redemption
end
"""

DOWN_OUT_CALL = """underlying SPX
param K = 100
param H = 80
event t=1 pay t=1
  if lowest(SPX, t=0, t=1, n=252) <= H then
    pay 0 as redemption
  else
    pay max(SPX - K, 0) as redemption
  end
end
"""


def asian_script(times):
    events = [f"event t={number(times[0])}\n  total = SPX\nend\n"]
    events += [f"event t={number(t)}\n  total = total + SPX\nend\n" for t in times[1:-1]]
    T = number(times[-1])
    events.append(
        f"event t={T} pay t={T}\n  total = total + SPX\n  pay max(total / {len(times)} - K, 0) as redemption\nend\n"
    )
    return "underlying SPX\nparam K = 100\n" + "\n".join(events)


# --- test 1: the scripting layer reproduces the existing pricers on the same paths ---


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("cp", ["call", "put"])
def test_script_reproduces_european_mc_bit_for_bit(model, cp):
    m = CTX.model(model, 1.0)
    ref = european_mc(m, CTX.market, 100.0, 1.0, cp, n_paths=N_REPRO, rng=5)
    res = price(EUROPEAN.format(sign="" if cp == "call" else "-"), m, CTX.market, n_paths=N_REPRO, seed=5)
    assert res.value.price == ref.price
    assert res.value.stderr == ref.stderr


@pytest.mark.parametrize("model", MODELS)
def test_script_reproduces_barrier_mc_bit_for_bit(model):
    m = CTX.model(model, 1.0)
    ref = barrier_mc(m, CTX.market, 100.0, 80.0, 1.0, "down-out", "call", n_paths=N_REPRO, rng=6)
    res = price(DOWN_OUT_CALL, m, CTX.market, n_paths=N_REPRO, seed=6)
    assert res.value.price == ref.price
    assert res.value.stderr == ref.stderr


@pytest.mark.parametrize("model", MODELS)
def test_script_reproduces_asian_mc(model):
    m = CTX.model(model, 1.0)
    ref = asian_mc(m, CTX.market, 100.0, 1.0, 12, "call", n_paths=N_REPRO, rng=7, control_variate=False)
    res = price(asian_script(fixing_dates(1.0, 12)), m, CTX.market, n_paths=N_REPRO, seed=7)
    # numpy's mean sums the 12 fixings pairwise, the script sequentially: equal to rounding
    assert res.value.price == pytest.approx(ref.price, rel=1e-12)
    assert res.value.stderr == pytest.approx(ref.stderr, rel=1e-10)


def test_script_price_is_in_percent_of_notional():
    script = EUROPEAN.format(sign="").replace("param K = 100", "param K = 100\nparam N = 50")
    res = price(script, CTX.model("bs"), CTX.market, n_paths=N_REPRO, seed=5)
    assert res.price == pytest.approx(100 * res.value.price / 50, rel=1e-15)
    assert np.isfinite(res.seconds) and res.seconds > 0


# --- dates, batches and formats ---


def phoenix_script(months=12, freq=3, cpn=0.02, ac=1.0, cb=0.7, ki=0.6):
    from optlab.structured.dates import schedule

    dates = schedule(CTX.valuation, months, freq, "modified_following", 5)
    lines = [
        "underlying SPX",
        "param N = 100",
        f"param cpn = {cpn}",
        f"param ac = {ac}",
        f"param cb = {cb}",
        f"param ki = {ki}",
        "",
        f"event {CTX.valuation}",
        "  S0 = SPX",
        "end",
    ]
    for k, (obs, pay) in enumerate(dates):
        last = k == len(dates) - 1
        lines += [
            "",
            f"event {obs} pay {pay}",
            "  perf = SPX / S0",
            "  if perf >= cb then",
            "    pay N * cpn as coupon",
            "  end",
        ]
        if last:
            lines += [
                "  if perf >= ki then",
                "    pay N as redemption",
                "  else",
                "    pay N * perf as redemption",
                "  end",
            ]
        else:
            lines += ["  if perf >= ac then", "    pay N as redemption", "    terminate", "  end"]
        lines.append("end")
    return "\n".join(lines) + "\n"


def test_date_script_matches_time_script_on_the_same_paths():
    m = CTX.model("lv")
    by_time = price(EUROPEAN.format(sign=""), m, CTX.market, n_paths=N_REPRO, seed=5)
    by_date = price(
        EUROPEAN.format(sign="").replace("event t=1 pay t=1", "event 2027-10-27 pay 2027-10-27"),
        m,
        CTX.market,
        n_paths=N_REPRO,
        seed=5,
        valuation=CTX.valuation,
    )
    assert by_date.value.price == by_time.value.price


def test_note_minus_swap_is_the_discounted_redemption_date():
    from optlab.structured import Note, Swap

    script, kw = phoenix_script(), dict(n_paths=N_REPRO, seed=3, batch=5_000, valuation=CTX.valuation)
    note = price(script, CTX.model("bs"), CTX.market, fmt=Note(), **kw)
    swap = price(script, CTX.model("bs"), CTX.market, fmt=Swap(), **kw)
    expected = 100 * sum(float(CTX.market.D(t)) * p for t, p in note.redemption_probabilities.items())
    assert note.price - swap.price == pytest.approx(expected, abs=1e-10)
    assert sum(note.redemption_probabilities.values()) == pytest.approx(1.0, abs=1e-12)
    assert note.termination[-1] == 0.0  # the last event has no terminate
    assert 0.25 < note.expected_life <= 1.0


def test_issuer_spread_lowers_the_note_consistently_with_flow_timing():
    from optlab.structured import Note

    script, kw = phoenix_script(), dict(n_paths=N_REPRO, seed=3, valuation=CTX.valuation)
    m = CTX.model("bs")
    p0 = price(script, m, CTX.market, fmt=Note(0.0), **kw)
    h = 1e-6
    p1 = price(script, m, CTX.market, fmt=Note(h), **kw)
    slope = -100 * sum(t * float(CTX.market.D(t)) * f for (t, _), f in p0.expected_flows.items()) / 100
    assert p1.price < p0.price
    assert (p1.price - p0.price) / h == pytest.approx(slope, rel=1e-4)
    # duration-like slope: between the first payment time and maturity, weighted by the flows
    assert -slope / p0.price < max(t for t, _ in p0.expected_flows)


def test_batches_pool_the_pair_statistics():
    m, script = CTX.model("bs"), phoenix_script()
    one = price(script, m, CTX.market, n_paths=40_000, seed=11, valuation=CTX.valuation)
    many = price(script, m, CTX.market, n_paths=40_000, seed=11, batch=6_000, valuation=CTX.valuation)
    again = price(script, m, CTX.market, n_paths=40_000, seed=11, batch=6_000, valuation=CTX.valuation)
    assert many.price == again.price  # reproducible for (seed, n_paths, batch)
    assert many.price != one.price
    assert abs(many.price - one.price) <= 4 * np.hypot(many.stderr, one.stderr)
    assert many.stderr == pytest.approx(one.stderr, rel=0.1)
