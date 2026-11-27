"""Linear hedging instruments and their closed forms.

Every instrument is valued per unit of the trade's quantity, in the payment currency, on a `Valuation`: a context's market
in the model's scale (spot 100, deterministic discounts D(t) and forwards F(t)), the valuation date, the real spot over the
model's (`scale`), the issuer's funding curve and the swap spread over the government curve. The value is that of the
flows still to come; the price paid or received at the fill is cash, and day one's P&L is the mark less the fill.

The quantity is signed, positive when the desk is long:
- `future`: lots, long the index. Its value is the variation margin to date and to come, multiplier x (F(T) - price),
  undiscounted (the desk holds no margin account in the store).
- `cash`: units of the index or its ETF, long. The value is the real spot; its funding is a loan booked beside it.
- `irs`: notional, receiving the fixed rate against the floating rate, single curve: the government curve plus the swap
  spread, labelled `government proxy` (no swap curve in the lake).
- `deposit`: notional, the desk lending at a fixed rate until maturity (negative: borrowing), discounted on the issuer's
  funding curve (the government curve times the bank's spread curve).
- `funding_swap`: notional, the desk receiving a fixed rate against the floating rate plus the bank's forward spread, on
  the government curve. Its value moves with the bank's spread curve: the hedge of the funding of issued notes.
- `dividend_swap`: currency per index point, receiving the dividends of a calendar year against a strike, paid after it.
- `dividend_future`: lots of a listed dividend future on the same year, undiscounted.
- `vanilla`: a European call or put, listed (lots of the exchange's contract, filled at the chain's bid or ask) or OTC
  (any strike and expiry, one unit per index point, filled at the surface's mid plus or minus the cost table's vol
  spread). Not linear: it is a payoff script (`vanilla_script`, its strike and size in the model's scale), simulated with
  the notes of its group or replicated on the smile in a group of Europeans.

Dividends are read from the market's forward: what it carries between two dates, ln(S / (F D)), is dividends (the market
holds dividends and repo together), and the forward dividend points over [a, b] are the integral of F(t) dc(t).
Floating legs read their rates from the curve; the current period of a seasoned swap takes `current_rate` when the desk
filled it from the fixings, else the curve's rate to its end (its accrued part is then the curve's view).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from optlab.core.market import Market
from optlab.structured.dates import add_months, as_date, year_fraction
from optlab.structured.hedges import contracts

LINEAR_TYPES = ("future", "cash", "irs", "deposit", "funding_swap", "dividend_swap", "dividend_future")
TYPES = (*LINEAR_TYPES, "vanilla")
CURRENCIES = ("USD", "EUR", "JPY", "GBP")
BANK = "BANQUE-FICTIVE"
MAX_YEARS = 50.0


class HedgeError(ValueError):
    """An instrument that cannot be booked or valued as given."""


@dataclass(frozen=True)
class SpreadCurve:
    """A credit or funding spread term structure: P_s(T) = exp(-s(T) T), ln P_s linear in T between the pillars, the
    spread flat before the first and beyond the last. `bump` adds b(T) (a rate, fractions) to the spread at T."""

    entity: str
    tenors: tuple
    spreads: tuple
    origin: str = "synthetic"
    bump: Callable | None = field(default=None, compare=False)

    def __post_init__(self):
        t = tuple(float(x) for x in self.tenors)
        s = tuple(float(x) for x in self.spreads)
        if not t or len(t) != len(s) or any(b <= a for a, b in zip(t, t[1:])) or t[0] <= 0:
            raise HedgeError("a spread curve needs increasing positive tenors, one spread each")
        object.__setattr__(self, "tenors", t)
        object.__setattr__(self, "spreads", s)

    @classmethod
    def flat(cls, spread: float, entity: str = BANK, origin: str = "assumption") -> SpreadCurve:
        return cls(entity, (1.0,), (float(spread),), origin)

    def log_p(self, T) -> np.ndarray:
        T = np.maximum(np.asarray(T, dtype=float), 0.0)
        t, s = np.asarray(self.tenors, dtype=float), np.asarray(self.spreads, dtype=float)
        inside = np.interp(T, t, -s * t)
        out = np.asarray(np.where(T < t[0], -s[0] * T, np.where(T > t[-1], -s[-1] * T, inside)), dtype=float)
        if self.bump is not None:
            out = out - np.asarray(self.bump(T), dtype=float) * T
        return out

    def P(self, T) -> np.ndarray:
        return np.exp(self.log_p(T))

    def spread(self, T) -> np.ndarray:
        """The zero spread s(T) (at T = 0: the first pillar's)."""
        T = np.asarray(T, dtype=float)
        safe = np.where(T > 1e-12, T, 1.0)
        return np.where(T > 1e-12, -self.log_p(T) / safe, self.spreads[0])

    def bumped(self, b: Callable) -> SpreadCurve:
        """The curve with b(T) added to the spread (on top of an existing bump)."""
        old = self.bump
        new = b if old is None else (lambda T, o=old, n=b: np.asarray(o(T)) + np.asarray(n(T)))
        return dataclasses.replace(self, bump=new)

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "tenors": list(self.tenors),
            "spreads": list(self.spreads),
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SpreadCurve:
        return cls(d["entity"], tuple(d["tenors"]), tuple(d["spreads"]), d.get("origin", "synthetic"))


@dataclass(frozen=True)
class Valuation:
    """What a linear instrument is valued on. `since`: flows dated in (since, valuation] are counted as cash, undiscounted,
    as after a roll."""

    market: Market
    valuation: dt.date
    scale: float = 1.0
    funding: SpreadCurve | None = None
    swap_spread: float = 0.0
    since: dt.date | None = None

    def t(self, d) -> float:
        return year_fraction(self.valuation, d)

    def D(self, d) -> float:
        return float(self.market.D(max(self.t(d), 0.0)))

    def D_swap(self, d) -> float:
        t = max(self.t(d), 0.0)
        return float(self.market.D(t)) * math.exp(-self.swap_spread * t)

    def D_bank(self, d) -> float:
        t = max(self.t(d), 0.0)
        return float(self.market.D(t)) * float(self.bank.P(t))

    @property
    def bank(self) -> SpreadCurve:
        if self.funding is None:
            raise HedgeError("this instrument is discounted on the issuer's funding curve: the valuation has none")
        return self.funding

    def paid(self, d) -> bool:
        """Dated in (since, valuation]: paid during the roll."""
        d = as_date(d)
        return self.since is not None and self.since < d <= self.valuation

    def future(self, d) -> bool:
        return as_date(d) > self.valuation


@dataclass(frozen=True)
class LinearEnv:
    """What a book's linear instruments need beyond their market: each name's real spot over the model's (`scales`), the
    funding curves by entity, the swap spread over the government curve by currency (`swap_spreads`, fractions)."""

    scales: dict = field(default_factory=dict)
    funding: dict = field(default_factory=dict)
    swap_spreads: dict = field(default_factory=dict)

    def valuation(
        self, instrument: Instrument, market: Market, valuation: dt.date, since=None, funding_move=None
    ) -> Valuation:
        """The instrument's Valuation on `market`; `funding_move(entity)` gives a bump b(T) of that entity's curve."""
        name = instrument.underlying
        entity = getattr(instrument, "entity", BANK)
        curve = self.funding.get(entity)
        if curve is not None and funding_move is not None:
            b = funding_move(entity)
            if b is not None:
                curve = curve.bumped(b)
        return Valuation(
            market,
            valuation,
            float(self.scales.get(name, 1.0)) if name else 1.0,
            curve,
            float(self.swap_spreads.get(instrument.currency, 0.0)),
            since,
        )


def forward_dividends(market: Market, a: float, b: float) -> float:
    """Forward dividend points (model scale) over [a, b] years: the integral of F(t) dc(t), c(t) = ln(S / (F(t) D(t))),
    by the trapezoid rule on daily steps."""
    a, b = max(a, 0.0), max(b, 0.0)
    if b <= a:
        return 0.0
    n = max(8, math.ceil(365 * (b - a)))
    t = np.linspace(a, b, n + 1)
    F, D = np.asarray(market.F(t), dtype=float), np.asarray(market.D(t), dtype=float)
    c = np.log(market.spot / (F * D))
    return float(np.sum(0.5 * (F[1:] + F[:-1]) * np.diff(c)))


def _date(d, what: str) -> dt.date:
    try:
        return as_date(d)
    except (TypeError, ValueError) as exc:
        raise HedgeError(f"{what}: not a date ({d!r})") from exc


def _positive(x, what: str) -> float:
    x = float(x)
    if not math.isfinite(x) or x <= 0:
        raise HedgeError(f"{what} must be a positive number")
    return x


def _finite(x, what: str) -> float:
    x = float(x)
    if not math.isfinite(x):
        raise HedgeError(f"{what} must be a finite number")
    return x


def _rate(x, what: str) -> float:
    x = _finite(x, what)
    if abs(x) > 1.0:
        raise HedgeError(f"{what} is a fraction (0.04 is 4 %): {x} is out of range")
    return x


def _name(name: str) -> str:
    from optlab.core.instruments import instrument

    try:
        inst = instrument(str(name))
    except KeyError as exc:
        raise HedgeError(str(exc.args[0])) from exc
    if inst.asset_class == "fx":
        raise HedgeError(f"{name} is an FX pair, not an equity underlying")
    return inst.id


def _currency(c: str) -> str:
    if c not in CURRENCIES:
        raise HedgeError(f"unknown currency {c!r}, expected one of {', '.join(CURRENCIES)}")
    return c


def _periods(start: dt.date, end: dt.date, months: int) -> list[tuple[dt.date, dt.date]]:
    """(start, end) of each period, rolled back from the end every `months` (a short first period), unadjusted dates."""
    if end <= start:
        raise HedgeError("the maturity must come after the start")
    dates, k = [end], 1
    while True:
        d = add_months(end, -months * k)
        if d <= start:
            break
        dates.append(d)
        k += 1
    dates.append(start)
    dates = sorted(dates)
    return list(zip(dates[:-1], dates[1:]))


class Instrument:
    """The interface of every instrument: its type, what it hedges, its maturity, its value per unit."""

    type: str = ""
    fields: tuple = ()
    linear: bool = True  # valued in closed form; False: a payoff script, simulated or replicated

    @property
    def underlying(self) -> str | None:
        return getattr(self, "name", None)

    @property
    def currency(self) -> str:
        raise NotImplementedError

    @property
    def maturity(self) -> dt.date:
        raise NotImplementedError

    @property
    def label(self) -> str:
        raise NotImplementedError

    def value(self, v: Valuation) -> float:
        raise NotImplementedError

    def fair(self, v: Valuation) -> float:
        """The fair level on the market: a future's or a dividend's points, a swap's or a deposit's rate, the spot."""
        raise NotImplementedError

    def dates(self) -> list[tuple[dt.date, str]]:
        """The instrument's dated events: (date, what), in order."""
        return [(self.maturity, "maturity")]

    def to_dict(self) -> dict:
        out: dict = {"type": self.type}
        for f in dataclasses.fields(self):  # pyright: ignore[reportArgumentType] (every instrument is a dataclass)
            x = getattr(self, f.name)
            if x is None:
                continue
            out[f.name] = x.isoformat() if isinstance(x, dt.date) else x
        return out


@dataclass(frozen=True)
class Future(Instrument):
    name: str
    expiry: dt.date
    price: float  # index points, real
    multiplier: float | None = None
    type = "future"

    def __post_init__(self):
        spec = contracts.index_future(_name(self.name))
        object.__setattr__(self, "expiry", _date(self.expiry, "expiry"))
        object.__setattr__(self, "price", _positive(self.price, "the future's price"))
        object.__setattr__(self, "multiplier", _positive(self.multiplier or spec.multiplier, "the multiplier"))

    @property
    def currency(self) -> str:
        return contracts.index_future(self.name).currency

    @property
    def maturity(self) -> dt.date:
        return self.expiry

    @property
    def label(self) -> str:
        return f"{contracts.index_future(self.name).code} {self.expiry:%b%y} future"

    def forward(self, v: Valuation) -> float:
        """The future's level in real points: the forward at expiry (the spot once expired)."""
        return v.scale * float(v.market.F(max(v.t(self.expiry), 0.0)))

    def value(self, v: Valuation) -> float:
        assert self.multiplier is not None  # noqa: S101 (narrows the type for pyright: set in __post_init__)
        return self.multiplier * (self.forward(v) - self.price)

    def fair(self, v: Valuation) -> float:
        return self.forward(v)

    def dates(self) -> list[tuple[dt.date, str]]:
        return [(self.expiry, "expiry")]


@dataclass(frozen=True)
class Cash(Instrument):
    name: str
    price: float  # real level paid per unit
    type = "cash"

    def __post_init__(self):
        object.__setattr__(self, "name", _name(self.name))
        object.__setattr__(self, "price", _positive(self.price, "the price"))

    @property
    def currency(self) -> str:
        from optlab.core.instruments import instrument

        return instrument(self.name).currency

    @property
    def maturity(self) -> dt.date:
        return dt.date.max

    @property
    def label(self) -> str:
        return f"{self.name} cash"

    def value(self, v: Valuation) -> float:
        return v.scale * float(v.market.spot)

    def fair(self, v: Valuation) -> float:
        return self.value(v)

    def dates(self) -> list[tuple[dt.date, str]]:
        return []


@dataclass(frozen=True)
class Irs(Instrument):
    ccy: str
    start: dt.date
    end: dt.date
    rate: float
    fixed_months: int = 12
    float_months: int = 3
    current_rate: float | None = None
    type = "irs"

    def __post_init__(self):
        _currency(self.ccy)
        object.__setattr__(self, "start", _date(self.start, "start"))
        object.__setattr__(self, "end", _date(self.end, "end"))
        object.__setattr__(self, "rate", _rate(self.rate, "the fixed rate"))
        if self.current_rate is not None:
            object.__setattr__(self, "current_rate", _rate(self.current_rate, "the current floating rate"))
        for m in (self.fixed_months, self.float_months):
            if int(m) not in (1, 3, 6, 12):
                raise HedgeError("a leg pays every 1, 3, 6 or 12 months")
        if year_fraction(self.start, self.end) > MAX_YEARS or self.end <= self.start:
            raise HedgeError(f"a swap runs from its start to at most {MAX_YEARS:g} years after it")

    @property
    def currency(self) -> str:
        return self.ccy

    @property
    def maturity(self) -> dt.date:
        return self.end

    @property
    def label(self) -> str:
        years = round(year_fraction(self.start, self.end))
        return f"IRS {self.ccy} {years}y fixed {100 * self.rate:.3f} %"

    def value(self, v: Valuation) -> float:
        return _fixed_leg(self.rate, self.start, self.end, self.fixed_months, v, v.D_swap) - _floating_leg(
            self, v, v.D_swap
        )

    def annuity(self, v: Valuation) -> float:
        """The fixed leg's PV of one unit of rate per unit notional (its PV01 times 10,000)."""
        return _fixed_leg(1.0, self.start, self.end, self.fixed_months, v, v.D_swap, cash=False)

    def fair(self, v: Valuation) -> float:
        a = self.annuity(v)
        return _floating_leg(self, v, v.D_swap, cash=False) / a if a else 0.0

    def dates(self) -> list[tuple[dt.date, str]]:
        fixed = {e for _, e in _periods(self.start, self.end, self.fixed_months)}
        floating = {e for _, e in _periods(self.start, self.end, self.float_months)}
        both = [(d, "fixed and floating" if d in floating else "fixed") for d in fixed]
        return sorted(both + [(d, "floating") for d in floating - fixed])


def _fixed_leg(rate: float, start, end, months: int, v: Valuation, discount, cash: bool = True) -> float:
    pv = 0.0
    for s, e in _periods(start, end, months):
        if v.future(e):
            pv += rate * year_fraction(s, e) * discount(e)
        elif cash and v.paid(e):
            pv += rate * year_fraction(s, e)
    return pv


def _floating_leg(swap, v: Valuation, discount, spread: SpreadCurve | None = None, cash: bool = True) -> float:
    """PV of the floating leg (plus the forward spread of `spread`) per unit notional: each period's forward rate from the
    curve, the current one at `current_rate` (else the curve's rate to its end), the ones paid in the roll as cash."""
    pv = 0.0
    for s, e in _periods(swap.start, swap.end, swap.float_months):
        tau = year_fraction(s, e)
        if v.future(e):
            lo = s if v.future(s) else v.valuation
            if v.future(s) or swap.current_rate is None:
                span = year_fraction(lo, e)
                rate = (discount(lo) / discount(e) - 1.0) / span if span > 0 else 0.0
            else:
                rate = swap.current_rate
            extra = 0.0
            if spread is not None:
                a, b = max(v.t(lo), 0.0), max(v.t(e), 0.0)
                span = b - a
                extra = (float(spread.P(a)) / float(spread.P(b)) - 1.0) / span if span > 0 else 0.0
            pv += tau * (rate + extra) * discount(e)
        elif cash and v.paid(e):
            rate = swap.current_rate if swap.current_rate is not None else float(v.market.zero_rate(1 / 12))
            extra = float(spread.spread(0.0)) if spread is not None else 0.0
            pv += tau * (rate + extra)
    return pv


@dataclass(frozen=True)
class Deposit(Instrument):
    ccy: str
    start: dt.date
    end: dt.date
    rate: float
    type = "deposit"

    def __post_init__(self):
        _currency(self.ccy)
        object.__setattr__(self, "start", _date(self.start, "start"))
        object.__setattr__(self, "end", _date(self.end, "end"))
        object.__setattr__(self, "rate", _rate(self.rate, "the rate"))
        if self.end <= self.start or year_fraction(self.start, self.end) > MAX_YEARS:
            raise HedgeError(f"a deposit ends after its start, at most {MAX_YEARS:g} years later")

    @property
    def currency(self) -> str:
        return self.ccy

    @property
    def maturity(self) -> dt.date:
        return self.end

    @property
    def label(self) -> str:
        return f"deposit {self.ccy} to {self.end.isoformat()} at {100 * self.rate:.3f} %"

    def annuity(self, v: Valuation) -> float:
        """The PV of one unit of rate per unit notional: the period times the discount to maturity."""
        return year_fraction(self.start, self.end) * v.D_bank(self.end) if v.future(self.end) else 0.0

    def fair(self, v: Valuation) -> float:
        """The rate that makes the deposit worth its principal, from its start (or from today once it has started)."""
        if not v.future(self.end):
            return 0.0
        lo = self.start if v.future(self.start) else v.valuation
        tau = year_fraction(self.start, self.end)
        return (v.D_bank(lo) / v.D_bank(self.end) - 1.0) / tau if tau else 0.0

    def dates(self) -> list[tuple[dt.date, str]]:
        return [(self.start, "start"), (self.end, "maturity")]

    def value(self, v: Valuation) -> float:
        back = 1.0 + self.rate * year_fraction(self.start, self.end)
        pv = 0.0
        if v.future(self.end):
            pv += back * v.D_bank(self.end)
        elif v.paid(self.end):
            pv += back
        if v.future(self.start):
            pv -= v.D_bank(self.start)
        elif v.paid(self.start):
            pv -= 1.0
        return pv


@dataclass(frozen=True)
class FundingSwap(Instrument):
    ccy: str
    start: dt.date
    end: dt.date
    rate: float
    float_months: int = 3
    entity: str = BANK
    current_rate: float | None = None
    type = "funding_swap"

    def __post_init__(self):
        _currency(self.ccy)
        object.__setattr__(self, "start", _date(self.start, "start"))
        object.__setattr__(self, "end", _date(self.end, "end"))
        object.__setattr__(self, "rate", _rate(self.rate, "the fixed rate"))
        if self.current_rate is not None:
            object.__setattr__(self, "current_rate", _rate(self.current_rate, "the current floating rate"))
        if int(self.float_months) not in (1, 3, 6, 12):
            raise HedgeError("a leg pays every 1, 3, 6 or 12 months")
        if self.end <= self.start or year_fraction(self.start, self.end) > MAX_YEARS:
            raise HedgeError(f"a swap ends after its start, at most {MAX_YEARS:g} years later")

    @property
    def currency(self) -> str:
        return self.ccy

    @property
    def maturity(self) -> dt.date:
        return self.end

    @property
    def label(self) -> str:
        years = round(year_fraction(self.start, self.end))
        return f"funding swap {self.ccy} {years}y fixed {100 * self.rate:.3f} %"

    def value(self, v: Valuation) -> float:
        fixed = _fixed_leg(self.rate, self.start, self.end, self.float_months, v, v.D)
        return fixed - _floating_leg(self, v, v.D, v.bank)

    def annuity(self, v: Valuation) -> float:
        return _fixed_leg(1.0, self.start, self.end, self.float_months, v, v.D, cash=False)

    def fair(self, v: Valuation) -> float:
        a = self.annuity(v)
        return _floating_leg(self, v, v.D, v.bank, cash=False) / a if a else 0.0

    def dates(self) -> list[tuple[dt.date, str]]:
        return [(e, "period") for _, e in _periods(self.start, self.end, self.float_months)]


def _year(y) -> int:
    y = int(y)
    if not 1990 <= y <= 2100:
        raise HedgeError(f"a dividend year between 1990 and 2100, not {y}")
    return y


@dataclass(frozen=True)
class DividendSwap(Instrument):
    name: str
    year: int
    strike: float  # real dividend points
    payment: dt.date | None = None
    realised: float = 0.0  # real points already paid in the year, filled from the fixings
    type = "dividend_swap"

    def __post_init__(self):
        object.__setattr__(self, "name", _name(self.name))
        object.__setattr__(self, "year", _year(self.year))
        object.__setattr__(self, "strike", _finite(self.strike, "the strike"))
        object.__setattr__(self, "realised", _finite(self.realised, "the realised dividends"))
        pay = _date(self.payment, "payment") if self.payment else dt.date(self.year + 1, 1, 31)
        if pay < dt.date(self.year, 12, 31):
            raise HedgeError("a dividend swap pays after the end of its year")
        object.__setattr__(self, "payment", pay)

    @property
    def currency(self) -> str:
        from optlab.core.instruments import instrument

        return instrument(self.name).currency

    @property
    def maturity(self) -> dt.date:
        assert self.payment is not None  # noqa: S101 (narrows the type for pyright: set in __post_init__)
        return self.payment

    @property
    def label(self) -> str:
        return f"{self.name} {self.year} dividend swap at {self.strike:g}"

    def points(self, v: Valuation) -> float:
        """The year's dividends in real points: realised, plus the forward's to come."""
        a, b = v.t(dt.date(self.year, 1, 1)), v.t(dt.date(self.year + 1, 1, 1))
        return self.realised + v.scale * forward_dividends(v.market, max(a, 0.0), b)

    def fair(self, v: Valuation) -> float:
        return self.points(v)

    def value(self, v: Valuation) -> float:
        amount = self.points(v) - self.strike
        if v.future(self.maturity):
            return amount * v.D(self.maturity)
        return amount if v.paid(self.maturity) else 0.0


@dataclass(frozen=True)
class DividendFutureContract(Instrument):
    name: str
    year: int
    price: float  # real dividend points
    multiplier: float | None = None
    realised: float = 0.0
    type = "dividend_future"

    def __post_init__(self):
        spec = contracts.dividend_future(_name(self.name))
        object.__setattr__(self, "year", _year(self.year))
        object.__setattr__(self, "price", _finite(self.price, "the price"))
        object.__setattr__(self, "realised", _finite(self.realised, "the realised dividends"))
        object.__setattr__(self, "multiplier", _positive(self.multiplier or spec.multiplier, "the multiplier"))

    @property
    def currency(self) -> str:
        return contracts.dividend_future(self.name).currency

    @property
    def maturity(self) -> dt.date:
        return dt.date(self.year, 12, 31)

    @property
    def label(self) -> str:
        return f"{contracts.dividend_future(self.name).code} {self.year} dividend future"

    def points(self, v: Valuation) -> float:
        a, b = v.t(dt.date(self.year, 1, 1)), v.t(dt.date(self.year + 1, 1, 1))
        return self.realised + v.scale * forward_dividends(v.market, max(a, 0.0), b)

    def fair(self, v: Valuation) -> float:
        return self.points(v)

    def value(self, v: Valuation) -> float:
        assert self.multiplier is not None  # noqa: S101 (narrows the type for pyright: set in __post_init__)
        return self.multiplier * (self.points(v) - self.price)


# The payoff as a coupon and the notional as the redemption, which the swap format nets: a leg discounted on the
# government curve, never on the issuer's.
_VANILLA = (
    "underlying {name}\nparam N = 1\nparam K = 100\nevent {expiry} pay {expiry}\n"
    "  pay N * max({payoff}, 0) as coupon\n  pay N as redemption\nend\n"
)
VANILLA_SCRIPT = {
    "call": _VANILLA.replace("{payoff}", "{name} - K"),
    "put": _VANILLA.replace("{payoff}", "K - {name}"),
}


@dataclass(frozen=True)
class Vanilla(Instrument):
    name: str
    expiry: dt.date
    strike: float  # real level
    kind: str  # call or put
    price: float = 0.0  # premium per index point, real: the fill
    multiplier: float | None = None  # listed: the contract's; OTC: 1 (one unit per index point)
    listed: bool = False
    type = "vanilla"
    linear = False

    def __post_init__(self):
        object.__setattr__(self, "name", _name(self.name))
        object.__setattr__(self, "expiry", _date(self.expiry, "expiry"))
        object.__setattr__(self, "strike", _positive(self.strike, "the strike"))
        if self.kind not in ("call", "put"):
            raise HedgeError("a vanilla is a call or a put")
        object.__setattr__(self, "price", _finite(self.price, "the premium"))
        if self.price < 0:
            raise HedgeError("the premium per index point is not negative")
        default = contracts.listed_option(self.name).multiplier if self.listed else 1.0
        object.__setattr__(self, "multiplier", _positive(self.multiplier or default, "the multiplier"))
        object.__setattr__(self, "listed", bool(self.listed))

    @property
    def currency(self) -> str:
        from optlab.core.instruments import instrument

        return instrument(self.name).currency

    @property
    def maturity(self) -> dt.date:
        return self.expiry

    @property
    def label(self) -> str:
        where = "listed" if self.listed else "OTC"
        return f"{self.name} {self.expiry:%d%b%y} {self.strike:g} {self.kind} ({where})"

    def script(self) -> str:
        """The payoff as a script with its size N and strike K as parameters (`params` gives them on a scale)."""
        return VANILLA_SCRIPT[self.kind].format(name=self.name, expiry=self.expiry.isoformat())

    def params(self, scale: float) -> dict:
        """N and K in the model's scale: K is the strike over the scale, N the multiplier times the scale, so one unit
        pays multiplier x (S - K) in real points."""
        assert self.multiplier is not None  # noqa: S101 (narrows the type for pyright: set in __post_init__)
        return {"N": self.multiplier * scale, "K": self.strike / scale}

    def value(self, v: Valuation) -> float:
        raise HedgeError("a vanilla is priced on the surface (a script), not in closed form")

    def fair(self, v: Valuation) -> float:
        raise HedgeError("a vanilla's fair premium comes from the surface (its mark)")

    def dates(self) -> list[tuple[dt.date, str]]:
        return [(self.expiry, "expiry")]


CLASSES: dict = {
    "future": Future,
    "cash": Cash,
    "irs": Irs,
    "deposit": Deposit,
    "funding_swap": FundingSwap,
    "dividend_swap": DividendSwap,
    "dividend_future": DividendFutureContract,
    "vanilla": Vanilla,
}


REFERENCE = {"USD": "SPX", "EUR": "SX5E"}  # the index whose market values a currency's rates instruments


def home(inst: Instrument) -> tuple[str, str]:
    """(name, payment currency) of the group an instrument joins: its name's, or its currency's reference index."""
    name = inst.underlying or REFERENCE.get(inst.currency)
    if name is None:
        raise HedgeError(f"no market values {inst.currency} rates instruments: {', '.join(REFERENCE)} have one")
    return name, inst.currency


def is_instrument(product) -> bool:
    return isinstance(product, Instrument)


def parse(d: object) -> Instrument:
    """An instrument from its JSON form ({"type": ..., fields}); unknown types or fields are refused."""
    if not isinstance(d, dict):
        raise HedgeError("an instrument is an object with a type")
    kind = d.get("type")
    cls = CLASSES.get(str(kind))
    if cls is None:
        raise HedgeError(f"unknown instrument type {kind!r}, expected one of {', '.join(TYPES)}")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(d) - known - {"type"}
    if unknown:
        raise HedgeError(f"unknown field(s) of a {kind}: {', '.join(sorted(unknown))}")
    missing = [f.name for f in dataclasses.fields(cls) if f.default is dataclasses.MISSING and f.name not in d]
    if missing:
        raise HedgeError(f"a {kind} needs {', '.join(missing)}")
    try:
        return cls(**{k: v for k, v in d.items() if k != "type"})
    except (TypeError, KeyError) as exc:
        raise HedgeError(f"a {kind}: {exc}") from exc
