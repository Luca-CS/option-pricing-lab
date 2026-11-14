"""Minimal calendar: Monday-Friday business days (no holidays yet), day counts, roll conventions and schedules."""

from __future__ import annotations

import calendar as _calendar
import datetime as dt

CONVENTIONS = ("following", "modified_following", "preceding", "unadjusted")


def as_date(d) -> dt.date:
    return dt.date.fromisoformat(d) if isinstance(d, str) else d


def is_business_day(d: dt.date | str) -> bool:
    return as_date(d).weekday() < 5


def adjust(d, convention: str = "modified_following") -> dt.date:
    d = as_date(d)
    if convention == "unadjusted" or is_business_day(d):
        return d
    if convention == "following":
        while not is_business_day(d):
            d += dt.timedelta(days=1)
        return d
    if convention == "preceding":
        while not is_business_day(d):
            d -= dt.timedelta(days=1)
        return d
    if convention == "modified_following":
        f = adjust(d, "following")
        return f if f.month == d.month else adjust(d, "preceding")
    raise ValueError(f"unknown convention {convention!r}, expected one of {', '.join(CONVENTIONS)}")


def add_business_days(d, n: int) -> dt.date:
    """n business days after d (before d if n < 0); d itself need not be a business day."""
    d, step = as_date(d), (1 if n >= 0 else -1)
    for _ in range(abs(n)):
        d += dt.timedelta(days=step)
        while not is_business_day(d):
            d += dt.timedelta(days=step)
    return d


def add_months(d, n: int) -> dt.date:
    """Same day n months later, clamped to the end of the month (31/01 + 1M = 28 or 29/02)."""
    d = as_date(d)
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, _calendar.monthrange(year, month)[1]))


def year_fraction(d1, d2, basis: str = "ACT/365F") -> float:
    d1, d2 = as_date(d1), as_date(d2)
    if basis == "ACT/365F":
        return (d2 - d1).days / 365.0
    if basis == "ACT/360":
        return (d2 - d1).days / 360.0
    if basis == "30/360":  # bond basis
        a, b = min(d1.day, 30), d2.day
        if a == 30 and b == 31:
            b = 30
        return (360 * (d2.year - d1.year) + 30 * (d2.month - d1.month) + b - a) / 360.0
    raise ValueError(f"unknown day count {basis!r}, expected ACT/365F, ACT/360 or 30/360")


def business_days(start, end) -> list[dt.date]:
    """Business days from start to end, both included."""
    d, end, out = as_date(start), as_date(end), []
    while d <= end:
        if is_business_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def schedule(
    start, months: int, freq_months: int, convention: str = "modified_following", pay_lag: int = 5, calendar=None
) -> list[tuple[dt.date, dt.date]]:
    """(observation, payment) dates every freq_months from start up to start + months. Unadjusted dates are
    start + k * freq (no roll drift), then adjusted; payment is pay_lag business days after the observation.
    `calendar` (an object with adjust and add_business_days) defaults to Monday-Friday."""
    if freq_months <= 0 or months <= 0 or months % freq_months:
        raise ValueError(
            f"the maturity ({months} months) must be a positive multiple of the frequency "
            f"({freq_months} months): stubs are not supported"
        )
    out = []
    for k in range(1, months // freq_months + 1):
        if calendar is None:
            obs = adjust(add_months(start, k * freq_months), convention)
            out.append((obs, add_business_days(obs, pay_lag)))
        else:
            obs = calendar.adjust(add_months(start, k * freq_months), convention)
            out.append((obs, calendar.add_business_days(obs, pay_lag)))
    return out


class WeekendCalendar:
    """Business days are Monday to Friday. Holidays are not modelled (known limitation)."""

    def is_business_day(self, d) -> bool:
        return is_business_day(d)

    def business_days(self, start, end) -> list[dt.date]:
        return business_days(start, end)

    def adjust(self, d, convention: str = "modified_following") -> dt.date:
        return adjust(d, convention)

    def add_business_days(self, d, n: int) -> dt.date:
        return add_business_days(d, n)
