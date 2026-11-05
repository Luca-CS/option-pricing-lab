"""Product builder: term-sheet schema, masks, compiler, profile and pricing policy."""

import datetime as dt

import pytest

from optlab.structured.compiler import compile_sheet
from optlab.structured.policy import decide
from optlab.structured.presets import PRESETS, preset
from optlab.structured.profile import profile
from optlab.structured.schema import TermSheet, TermSheetError, describe
from optlab.structured.script.parser import parse
from optlab.structured.script.printer import event as event_lines
from optlab.structured.script.printer import to_text
from optlab.structured.script.timeline import Clock, build_timeline

VAL = dt.date(2026, 10, 27)


def compiled(sheet):
    return compile_sheet(sheet, VAL)


def prof(sheet):
    p = compiled(sheet)
    return profile(p.program, build_timeline(p.program, Clock(VAL)))


# --- schema and masks ---

def test_masks_expose_their_sections():
    assert [s["name"] for s in describe("autocall")["sections"]] == ["general", "wrapper", "autocall", "coupon",
                                                                     "downside", "costs"]
    assert [s["name"] for s in describe("bullet")["sections"]] == ["general", "wrapper", "coupon", "upside",
                                                                   "downside", "costs"]


def test_visible_fields_follow_the_choices():
    def fields(sheet, section):
        sec = next(s for s in describe(sheet.mask.name, sheet)["sections"] if s["name"] == section)
        return [f["name"] for f in sec["fields"]]

    pdi = TermSheet("bullet", {"downside": {"type": "pdi"}})
    assert fields(pdi, "downside") == ["type", "strike", "barrier", "observation"]
    geared = TermSheet("bullet", {"downside": {"type": "geared_put", "strike": 0.8}})
    assert fields(geared, "downside") == ["type", "strike", "gearing"]
    assert geared.get("downside.gearing") == pytest.approx(1.25)
    assert fields(TermSheet("bullet", {"downside": {"type": "none"}}), "downside") == ["type"]
    cond = TermSheet("bullet", {"coupon": {"type": "conditional"}})
    assert fields(cond, "coupon") == ["type", "rate", "frequency_months", "barrier", "memory"]
    snow = TermSheet("autocall", {"coupon": {"type": "snowball"}})
    assert fields(snow, "coupon") == ["type", "rate"]  # accrues per call period, no own frequency
    assert "issuer_spread" not in fields(TermSheet("bullet", {"wrapper": {"format": "swap"}}), "wrapper")


@pytest.mark.parametrize("mask, values, message", [
    ("bullet", {"coupon": {"type": "snowball"}}, "not allowed for the bullet mask"),
    ("autocall", {"upside": {"type": "call"}}, "section 'upside' is not part of the autocall mask"),
    ("bullet", {"downside": {"type": "none", "barrier": 0.5}}, "downside.barrier is not used here"),
    ("bullet", {"downside": {"type": "geared_put", "gearing": 2.0}}, "is computed"),
    ("bullet", {"downside": {"strike": 0.8, "barrier": 0.9}}, "at or below the put strike"),
    ("bullet", {"general": {"maturity_months": 12}, "coupon": {"frequency_months": 5}}, "multiple of"),
    ("bullet", {"coupon": {"type": "none"}}, "no coupon to solve for"),
    ("bullet", {"coupon": {"rate": "high"}}, "must be a number"),
    ("bullet", {"downside": {"strike": -0.1}}, "below the minimum"),
    ("bullet", {"coupon": {"colour": 1}}, "unknown field"),
    ("swap", {}, "unknown product type"),
    ("autocall", {"general": {"maturity_months": 12}, "autocall": {"first_call": 5}}, "after the last"),
])
def test_term_sheet_errors(mask, values, message):
    with pytest.raises(TermSheetError, match=message):
        TermSheet(mask, values)


def test_replace_drops_fields_hidden_by_the_change():
    rc = preset("barrier_reverse_convertible")
    geared = rc.replace(**{"downside.type": "geared_put", "downside.strike": 0.75})
    assert geared.get("downside.barrier") is None and geared.get("downside.gearing") == pytest.approx(1 / 0.75)
    with pytest.raises(TermSheetError, match="not used"):
        rc.replace(**{"downside.type": "none", "downside.barrier": 0.5})


@pytest.mark.parametrize("name", list(PRESETS))
def test_every_preset_compiles_to_a_valid_round_tripping_script(name):
    p = compiled(preset(name))
    assert parse(to_text(p.program)) == p.program
    assert p.maturity > VAL


# --- compiler details ---

def test_schedules_coupon_and_call_frequencies_independently():
    sheet = preset("phoenix", **{"coupon.frequency_months": 1, "autocall.frequency_months": 3,
                                 "general.maturity_months": 12})
    events = compiled(sheet).program.events
    assert len(events) == 1 + 12  # strike + monthly observations; quarterly calls fall on coupon dates
    calls = [e for e in events if any("terminate" in line for line in event_lines(e))]
    assert len(calls) == 3  # months 3, 6, 9 (maturity redeems instead)


def test_step_down_and_non_call_period():
    sheet = preset("phoenix", **{"autocall.step_down": 0.05, "autocall.first_call": 2,
                                 "general.maturity_months": 12})
    text = compiled(sheet).script
    assert "param step_down = 0.05" in text
    assert "if perf >= trigger then" in text and "if perf >= trigger - 1 * step_down then" in text
    assert "2027-01-27" in text  # the first quarter is a coupon date
    assert text.count("terminate") == 2  # calls at months 6 and 9 only


def test_coupon_is_per_period_in_the_script():
    p = compiled(preset("reverse_convertible", **{"coupon.rate": 0.1, "coupon.frequency_months": 6}))
    assert p.program.param_values["cpn"] == pytest.approx(0.05)
    assert p.coupon_rate(0.05) == pytest.approx(0.1)
    snow = compiled(preset("athena", **{"coupon.rate": 0.08}))
    assert snow.program.param_values["cpn"] == pytest.approx(0.02)
    assert "pay N * cpn * 8 as coupon" in snow.script  # 8 quarters at maturity


def test_payment_dates_are_t_plus_5_business_days():
    events = compiled(preset("reverse_convertible")).program.events
    assert (events[1].when.value, events[1].pay.value) == ("2027-01-27", "2027-02-03")


# --- profile and policy ---

@pytest.mark.parametrize("name, cls", [
    ("reverse_convertible", "european"), ("airbag", "european"), ("capital_protected", "european"),
    ("barrier_reverse_convertible", "path_dependent"), ("phoenix", "callable"), ("athena", "callable"),
])
def test_profile_classes(name, cls):
    assert prof(preset(name)).product_class == cls


def test_conditional_coupons_make_a_bullet_path_dependent():
    sheet = preset("reverse_convertible", **{"coupon.type": "conditional"})
    p = prof(sheet)
    assert p.product_class == "path_dependent" and len(p.observation_times) == 4


def test_policy_picks_replication_for_european_and_slv_otherwise():
    d = decide(prof(preset("reverse_convertible")), t_max=2.14)
    assert (d.method, d.model, d.control) == ("replication", "surface", "lv")
    d = decide(prof(preset("phoenix")), t_max=2.14)
    assert (d.method, d.model, d.control) == ("monte_carlo", "slv", "lv")
    assert "forward skew" in d.reason
    d = decide(prof(preset("barrier_reverse_convertible")), t_max=2.14)
    assert d.model == "slv"


def test_policy_overrides_and_warnings():
    d = decide(prof(preset("phoenix")), t_max=2.14, model="bs")
    assert d.forced and d.model == "bs" and "policy default: slv" in d.reason
    with pytest.raises(ValueError, match="needs a European"):
        decide(prof(preset("phoenix")), t_max=2.14, model="surface")
    long = decide(prof(preset("phoenix", **{"general.maturity_months": 36})), t_max=2.14)
    assert any("beyond the last calibrated maturity" in w for w in long.warnings)
    assert decide(prof(preset("phoenix")), t_max=2.14, model="lv").control is None


def test_describe_is_json_serialisable_for_a_front_end():
    import json
    sheet = preset("phoenix", **{"general.strike_date": dt.date(2026, 11, 2)})
    out = json.loads(json.dumps(describe("autocall", sheet)))
    general = next(s for s in out["sections"] if s["name"] == "general")
    assert next(f for f in general["fields"] if f["name"] == "strike_date")["value"] == "2026-11-02"
