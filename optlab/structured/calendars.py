"""Exchange calendars. NYSE: the ten regular holidays with the exchange's weekend rules (a Saturday holiday is
observed on the Friday before, a Sunday one on the Monday after, except New Year's Day on a Saturday, which is not
observed). Special closures (national days of mourning, weather) are not modelled."""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

from .dates import CONVENTIONS, WeekendCalendar, as_date


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th given weekday (0 = Monday) of a month; n = -1 for the last one."""
    if n > 0:
        first = dt.date(year, month, 1)
        return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: dt.date) -> dt.date:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


class NYSECalendar(WeekendCalendar):
    """New York Stock Exchange: Monday to Friday minus the regular holidays."""

    @staticmethod
    @lru_cache(maxsize=None)
    def holidays(year: int) -> tuple:
        days = []
        new_year = dt.date(year, 1, 1)
        if new_year.weekday() != 5:  # a Saturday New Year's Day is not observed on Friday 31 December
            days.append(_observed(new_year))
        days += [_nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
                 _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
                 easter(year) - dt.timedelta(days=2),  # Good Friday
                 _nth_weekday(year, 5, 0, -1)]  # Memorial Day
        if year >= 2022:
            days.append(_observed(dt.date(year, 6, 19)))  # Juneteenth
        days += [_observed(dt.date(year, 7, 4)),
                 _nth_weekday(year, 9, 0, 1),  # Labor Day
                 _nth_weekday(year, 11, 3, 4),  # Thanksgiving
                 _observed(dt.date(year, 12, 25))]
        return tuple(sorted(days))

    def is_business_day(self, d) -> bool:
        d = as_date(d)
        return d.weekday() < 5 and d not in self.holidays(d.year)

    def business_days(self, start, end) -> list[dt.date]:
        d, end, out = as_date(start), as_date(end), []
        while d <= end:
            if self.is_business_day(d):
                out.append(d)
            d += dt.timedelta(days=1)
        return out

    def adjust(self, d, convention: str = "modified_following") -> dt.date:
        d = as_date(d)
        if convention == "unadjusted" or self.is_business_day(d):
            return d
        if convention in ("following", "preceding"):
            step = dt.timedelta(days=1 if convention == "following" else -1)
            while not self.is_business_day(d):
                d += step
            return d
        if convention == "modified_following":
            f = self.adjust(d, "following")
            return f if f.month == d.month else self.adjust(d, "preceding")
        raise ValueError(f"unknown convention {convention!r}, expected one of {', '.join(CONVENTIONS)}")

    def add_business_days(self, d, n: int) -> dt.date:
        d, step = as_date(d), (1 if n >= 0 else -1)
        for _ in range(abs(n)):
            d += dt.timedelta(days=step)
            while not self.is_business_day(d):
                d += dt.timedelta(days=step)
        return d


CALENDARS = {"weekend": WeekendCalendar(), "nyse": NYSECalendar()}


def calendar(name_or_calendar=None):
    """A calendar object from its name ('weekend', 'nyse'), an object, or None (weekend)."""
    if name_or_calendar is None:
        return CALENDARS["weekend"]
    if isinstance(name_or_calendar, str):
        try:
            return CALENDARS[name_or_calendar]
        except KeyError:
            raise ValueError(f"unknown calendar {name_or_calendar!r}, expected one of {', '.join(CALENDARS)}")
    return name_or_calendar
