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
    events.append(f"event t={T} pay t={T}\n  total = total + SPX\n"
                  f"  pay max(total / {len(times)} - K, 0) as redemption\nend\n")
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
