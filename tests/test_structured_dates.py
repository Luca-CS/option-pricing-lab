"""Calendar: business days, roll conventions, month arithmetic, day counts and schedules."""

import datetime as dt

import pytest

from optlab.structured.dates import (
    add_business_days,
    add_months,
    adjust,
    business_days,
    is_business_day,
    schedule,
    year_fraction,
)

D = dt.date


def test_weekends_are_not_business_days():
    assert is_business_day(D(2026, 10, 30))  # Friday
    assert not is_business_day(D(2026, 10, 31))  # Saturday
    assert not is_business_day("2026-11-01")  # Sunday


@pytest.mark.parametrize(
    "d, n, expected",
    [
        (D(2026, 10, 27), 5, D(2026, 11, 3)),  # Tuesday + 5 = next Tuesday
        (D(2026, 10, 30), 1, D(2026, 11, 2)),  # Friday + 1 = Monday
        (D(2026, 10, 31), 1, D(2026, 11, 2)),  # from a Saturday
        (D(2026, 11, 2), -1, D(2026, 10, 30)),
        (D(2026, 10, 27), 0, D(2026, 10, 27)),
    ],
)
def test_add_business_days(d, n, expected):
    assert add_business_days(d, n) == expected


@pytest.mark.parametrize(
    "d, n, expected",
    [
        (D(2027, 1, 31), 1, D(2027, 2, 28)),
        (D(2028, 1, 31), 1, D(2028, 2, 29)),  # leap year
        (D(2026, 10, 31), 4, D(2027, 2, 28)),
        (D(2026, 10, 27), 24, D(2028, 10, 27)),
        (D(2026, 12, 15), 1, D(2027, 1, 15)),
        (D(2027, 3, 31), -1, D(2027, 2, 28)),
    ],
)
def test_add_months_clamps_to_month_end(d, n, expected):
    assert add_months(d, n) == expected


def test_roll_conventions():
    sat = D(2027, 1, 30)  # Saturday, last business day of January is Friday 29
    assert adjust(sat, "following") == D(2027, 2, 1)
    assert adjust(sat, "modified_following") == D(2027, 1, 29)  # rolling forward would change month
    assert adjust(sat, "preceding") == D(2027, 1, 29)
    assert adjust(D(2027, 1, 23), "modified_following") == D(2027, 1, 25)  # same month: plain following
    assert adjust(sat, "unadjusted") == sat
    assert adjust(D(2027, 1, 29), "following") == D(2027, 1, 29)
    with pytest.raises(ValueError, match="unknown convention"):
        adjust(sat, "nearest")


def test_year_fractions():
    assert year_fraction("2026-10-27", "2027-10-27") == 1.0
    assert year_fraction("2027-10-27", "2028-10-27") == 366 / 365  # 29/02/2028 inside
    assert year_fraction("2026-10-27", "2027-01-27", "ACT/360") == 92 / 360
    assert year_fraction("2027-01-31", "2027-03-31", "30/360") == 60 / 360
    assert year_fraction("2027-01-30", "2027-03-31", "30/360") == 60 / 360
    assert year_fraction("2027-01-15", "2027-03-31", "30/360") == 76 / 360
    with pytest.raises(ValueError):
        year_fraction("2027-01-15", "2027-03-31", "ACT/ACT")


def test_business_days_window_includes_both_ends():
    days = business_days("2026-10-30", "2026-11-09")
    assert days[0] == D(2026, 10, 30) and days[-1] == D(2026, 11, 9)
    assert len(days) == 7
    assert len(business_days("2026-10-27", "2027-10-27")) == 262


def test_quarterly_schedule_with_modified_following_and_t_plus_5():
    s = schedule(D(2026, 10, 27), 24, 3)
    assert len(s) == 8
    assert s[0] == (D(2027, 1, 27), D(2027, 2, 3))
    # 27/03/2027 is a Saturday: modified following rolls to Monday 29/03
    assert schedule(D(2026, 12, 27), 3, 3)[0][0] == D(2027, 3, 29)
    assert s[-1][0] == D(2028, 10, 27)
    for obs, pay in s:
        assert is_business_day(obs) and is_business_day(pay) and pay > obs
    # month-end start: dates computed from the start, no drift (31/01 -> 30/04 -> 31/07)
    eom = schedule(D(2027, 1, 31), 6, 3, "unadjusted")
    assert [o for o, _ in eom] == [D(2027, 4, 30), D(2027, 7, 31)]


def test_schedule_rejects_stubs():
    with pytest.raises(ValueError, match="multiple of the frequency"):
        schedule(D(2026, 10, 27), 18, 12)


def test_date_window_uses_every_business_day():
    from optlab.structured.script.parser import parse
    from optlab.structured.script.timeline import Clock, build_timeline

    p = parse(
        "underlying SPX\nevent 2027-10-27 pay 2027-11-03\n"
        "  x = lowest(SPX, 2026-10-27, 2027-10-27)\n  pay x as redemption\nend\n"
    )
    tl = build_timeline(p, Clock("2026-10-27"))
    (times,) = tl.windows.values()
    assert len(times) == 262 and times[0] == 0.0 and times[-1] == 1.0
    assert len(tl.sim_times) == 261  # the valuation date is not simulated
    assert tl.pay_times == [year_fraction("2026-10-27", "2027-11-03")]
