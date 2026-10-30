"""Running products: past fixings, flows already paid, knock-ins and calls that already happened."""

import datetime as dt

import numpy as np
import pytest
from _structured_refs import FLAT, S0, flat_context, prob_below, put_fwd

from optlab.structured.compiler import compile_sheet
from optlab.structured.dates import business_days
from optlab.structured.presets import preset
from optlab.structured.pricer import price
from optlab.structured.quote import quote
from optlab.structured.script.timeline import Clock, build_timeline

CTX = flat_context()  # valuation 2026-10-27, spot 100
STRIKE = dt.date(2026, 4, 27)


def running(name, **changes):
    return preset(name, **{"general.strike_date": STRIKE, "wrapper.solve": "upfront", **changes})


def test_running_rc_matches_closed_form_with_its_initial_fixing():
    S_init, K, B = 95.0, 1.0, 0.8
    sheet = running("reverse_convertible", **{"downside.barrier": B, "coupon.rate": 0.06})
    p = compile_sheet(sheet, CTX.valuation)
    tl = build_timeline(p.program, Clock(CTX.valuation))
    pays = [t for t in tl.pay_times if t is not None and t > 0]
    T = float(tl.event_times[-1])
    loss = (put_fwd(B * S_init, T) + (K - B) * S_init * prob_below(B * S_init, T)) / S_init
    ref = 100 * 0.015 * sum(float(FLAT.D(t)) for t in pays) + float(FLAT.D(pays[-1])) * 100 * (1 - loss)
    assert len(pays) == 3  # the July coupon is paid; October is observed today and paid on 3 November
    fixings = {"SPX": {STRIKE: S_init}}
    q = quote(sheet, CTX, control=None, fixings=fixings)
    assert q.decision.method == "replication"
    assert q.upfront == pytest.approx(ref, abs=1e-8)
    mc = price(p.program, CTX.model("bs"), FLAT, n_paths=100_000, seed=81, valuation=CTX.valuation, fixings=fixings)
    assert abs(mc.price - ref) <= 4 * mc.stderr
    assert any("already paid" in w for w in mc.warnings)


def test_missing_and_future_fixings_are_errors():
    sheet = running("reverse_convertible")
    with pytest.raises(KeyError, match="no fixing of SPX"):
        quote(sheet, CTX, control=None)
    with pytest.raises(ValueError, match="after the valuation date"):
        quote(sheet, CTX, control=None, fixings={"SPX": {STRIKE: 100.0, "2026-12-01": 90.0}})
    with pytest.raises(KeyError, match="the script observes SPX"):
        quote(sheet, CTX, control=None, fixings={"SX5E": {STRIKE: 100.0}})
    with pytest.raises(ValueError, match="fixed coupon"):
        quote(sheet.replace(**{"wrapper.solve": "coupon"}), CTX, control=None, fixings={"SPX": {STRIKE: 100.0}})


def test_a_product_called_in_the_past_is_worth_nothing():
    sheet = running("phoenix", **{"general.maturity_months": 12})
    fixings = {"SPX": {STRIKE: 100.0, "2026-07-27": 104.0}}  # above the trigger at the first call date
    p = compile_sheet(sheet, CTX.valuation)
    res = price(p.program, CTX.model("bs"), FLAT, n_paths=2_000, seed=1, valuation=CTX.valuation, fixings=fixings)
    assert res.price == 0.0 and res.stderr == 0.0
    assert res.termination[1] == 1.0


def test_a_daily_knock_in_already_hit_turns_the_brc_into_a_plain_rc():
    sheet = running("barrier_reverse_convertible", **{"downside.barrier": 0.7, "coupon.rate": 0.06})
    past = business_days(STRIKE, CTX.valuation)
    levels = {d: 100.0 for d in past}
    levels[past[40]] = 65.0  # one close below 70 %
    levels[CTX.valuation] = 100.0
    p = compile_sheet(sheet, CTX.valuation)
    hit = price(p.program, CTX.model("bs"), FLAT, n_paths=100_000, seed=91, batch=25_000, valuation=CTX.valuation,
                fixings={"SPX": levels})
    tl = build_timeline(p.program, Clock(CTX.valuation))
    pays = [t for t in tl.pay_times if t is not None and t > 0]
    T = float(tl.event_times[-1])
    ref = 100 * 0.015 * sum(float(FLAT.D(t)) for t in pays) + float(FLAT.D(pays[-1])) * (100 - put_fwd(S0, T))
    assert abs(hit.price - ref) <= 4 * hit.stderr
    levels[past[40]] = 99.0
    clean = price(p.program, CTX.model("bs"), FLAT, n_paths=100_000, seed=91, batch=25_000,
                  valuation=CTX.valuation, fixings={"SPX": levels})
    assert clean.price > hit.price + 1.0
    np.testing.assert_array_less(0, clean.stderr)


def test_expected_life_counts_from_the_valuation_date():
    sheet = running("phoenix", **{"general.maturity_months": 12})
    p = compile_sheet(sheet, CTX.valuation)
    fixings = {"SPX": {STRIKE: 100.0, "2026-07-27": 104.0}}
    res = price(p.program, CTX.model("bs"), FLAT, n_paths=2_000, seed=1, valuation=CTX.valuation, fixings=fixings)
    assert res.expected_life == 0.0  # called before today


def test_a_product_with_no_future_fixing_is_priced_by_replication_without_paths():
    # an RC whose final fixing is today: everything is known, only the discounting remains
    sheet = preset("reverse_convertible", **{"general.strike_date": dt.date(2025, 10, 27),
                                             "wrapper.solve": "upfront", "downside.barrier": 0.7})
    fixings = {"SPX": {"2025-10-27": 100.0, "2026-01-27": 90.0, "2026-04-27": 90.0, "2026-07-27": 90.0}}
    q = quote(sheet, CTX, control=None, fixings=fixings)  # the spot today (100) is the final fixing
    p = q.product
    tl = build_timeline(p.program, Clock(CTX.valuation))
    expected = float(FLAT.D(tl.pay_times[-1])) * 100 * (1 + 0.02)  # last coupon and full redemption, paid T+5
    assert q.decision.method == "replication"
    assert q.upfront == pytest.approx(expected, abs=1e-10)
