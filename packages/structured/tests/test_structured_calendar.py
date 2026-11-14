"""Exchange calendar (NYSE) through schedules, windows and quotes, and the lifecycle of running products."""

import datetime as dt

import pytest
from _quotes import some
from _structured_refs import flat_context

from optlab.structured import compile_sheet, preset, quote
from optlab.structured.calendars import NYSECalendar, easter
from optlab.structured.dates import WeekendCalendar, business_days, schedule
from optlab.structured.lifecycle import events
from optlab.structured.script.timeline import Clock, build_timeline
from optlab.structured.trade import Trade

D = dt.date
NYSE = NYSECalendar()
CTX = flat_context()


def test_easter_and_nyse_holidays():
    assert [easter(y) for y in (2026, 2027, 2028)] == [D(2026, 4, 5), D(2027, 3, 28), D(2028, 4, 16)]
    assert NYSE.holidays(2027) == (
        D(2027, 1, 1),
        D(2027, 1, 18),
        D(2027, 2, 15),
        D(2027, 3, 26),
        D(2027, 5, 31),
        D(2027, 6, 18),
        D(2027, 7, 5),
        D(2027, 9, 6),
        D(2027, 11, 25),
        D(2027, 12, 24),
    )
    assert NYSE.holidays(2026)[-4:] == (D(2026, 7, 3), D(2026, 9, 7), D(2026, 11, 26), D(2026, 12, 25))
    assert D(2028, 1, 1) not in NYSE.holidays(2028) and D(2027, 12, 31) not in NYSE.holidays(2027)  # Saturday rule


def test_the_ten_us_holidays_of_a_one_year_window():
    days = set(business_days(D(2026, 10, 27), D(2027, 10, 27))) - set(
        NYSE.business_days(D(2026, 10, 27), D(2027, 10, 27))
    )
    assert sorted(days) == [
        D(2026, 11, 26),
        D(2026, 12, 25),
        D(2027, 1, 1),
        D(2027, 1, 18),
        D(2027, 2, 15),
        D(2027, 3, 26),
        D(2027, 5, 31),
        D(2027, 6, 18),
        D(2027, 7, 5),
        D(2027, 9, 6),
    ]


def test_rolls_and_payment_lags_skip_holidays():
    assert NYSE.adjust(D(2027, 1, 18), "following") == D(2027, 1, 19)
    assert NYSE.add_business_days(D(2026, 11, 23), 5) == D(2026, 12, 1)  # Thanksgiving in between
    assert WeekendCalendar().add_business_days(D(2026, 11, 23), 5) == D(2026, 11, 30)
    obs, pay = schedule(D(2026, 10, 18), 3, 3, "following", 5, NYSE)[0]
    assert obs == D(2027, 1, 19) and pay == D(2027, 1, 26)


def test_default_calendar_leaves_every_script_unchanged():
    for name in ("phoenix", "barrier_reverse_convertible"):
        sheet = preset(name)
        assert (
            compile_sheet(sheet, CTX.valuation).script
            == compile_sheet(sheet, CTX.valuation, calendar=WeekendCalendar()).script
        )


def test_daily_windows_follow_the_sheet_calendar():
    sheet = preset("barrier_reverse_convertible", **{"general.calendar": "nyse"})
    p = compile_sheet(sheet, CTX.valuation)
    tl = build_timeline(p.program, Clock(p.valuation, p.calendar))
    (fix,) = tl.windows.values()
    assert len(fix) == 262 - 10


def test_a_running_brc_priced_with_exchange_fixings_only():
    strike = D(2026, 4, 27)
    sheet = preset(
        "barrier_reverse_convertible",
        **{"general.strike_date": strike, "wrapper.solve": "upfront", "general.calendar": "nyse"},
    )
    closes = {d: 100.0 for d in NYSE.business_days(strike, CTX.valuation)}
    q = quote(sheet, CTX, model="bs", control=None, n_paths=4_000, batch=None, fixings={"SPX": closes})
    assert 80 < some(q.upfront) < 110
    with pytest.raises(KeyError, match="no fixing of SPX"):  # Monday-Friday wants the Good Friday close too
        quote(
            sheet.replace(**{"general.calendar": "weekend"}),
            CTX,
            model="bs",
            control=None,
            n_paths=4_000,
            batch=None,
            fixings={"SPX": closes},
        )


# --- lifecycle ---


def phoenix(**changes):
    return preset(
        "phoenix",
        **{
            "general.strike_date": D(2026, 4, 27),
            "general.maturity_months": 12,
            "wrapper.solve": "upfront",
            "coupon.rate": 0.08,
            **changes,
        },
    )


def test_a_phoenix_called_at_its_first_date():
    trade = Trade.from_term_sheet("PHX-7", phoenix(), 1.0, D(2026, 4, 27))
    ev = events(trade, {"SPX": {D(2026, 4, 27): 100.0, D(2026, 7, 27): 104.0}}, CTX.valuation)
    assert [(e.kind, e.amount) for e in ev] == [("coupon_paid", 2.0), ("called", 100.0)]
    assert ev[1].date == D(2026, 7, 27) and ev[1].payment == D(2026, 8, 3) and ev[0].trade_id == "PHX-7"


def test_coupons_missed_then_paid_with_memory():
    fix = {"SPX": {D(2026, 4, 27): 100.0, D(2026, 7, 27): 65.0, D(2026, 10, 27): 80.0}}
    ev = events(phoenix(), fix, CTX.valuation)
    assert [(e.kind, e.amount) for e in ev] == [("coupon_missed", 0.0), ("coupon_paid", 4.0)]


def test_a_daily_knock_in_is_dated_at_the_first_close_below():
    strike = D(2026, 4, 27)
    sheet = preset("barrier_reverse_convertible", **{"general.strike_date": strike, "downside.barrier": 0.7})
    closes = {d: 100.0 for d in business_days(strike, CTX.valuation)}
    closes[D(2026, 8, 5)] = 65.0
    ev = events(sheet, {"SPX": closes}, CTX.valuation)
    assert [e.kind for e in ev] == ["coupon_paid", "knocked_in", "coupon_paid"]
    assert ev[1].date == D(2026, 8, 5)


def test_a_matured_product_reports_its_redemption():
    sheet = preset("reverse_convertible", **{"general.strike_date": D(2025, 10, 27)})
    fix = {
        "SPX": {
            D(2025, 10, 27): 100.0,
            D(2026, 1, 27): 90.0,
            D(2026, 4, 27): 90.0,
            D(2026, 7, 27): 90.0,
            D(2026, 10, 27): 90.0,
        }
    }
    ev = events(sheet, fix, CTX.valuation)
    assert [e.kind for e in ev] == ["coupon_paid"] * 4 + ["knocked_in", "matured"]
    assert ev[-1].amount == pytest.approx(90.0)  # the preset knocks in at the strike: redeemed at 90 %


def test_a_missing_fixing_that_matters_is_named():
    with pytest.raises(KeyError, match="no fixing for the observations of 2026-07-27"):
        events(phoenix(), {"SPX": {D(2026, 4, 27): 100.0, D(2026, 10, 27): 90.0}}, CTX.valuation)
