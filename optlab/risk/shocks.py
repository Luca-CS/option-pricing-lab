"""Shocks: a market move as data, and its application to a pricing context.

A `Shock` holds moves by factor. Spots are relative (0.01 = +1 %), volatilities absolute (0.01 = one vol point) or relative
(0.01 = +1 % of the vol), skew in vol points of the 90-110 % gap at one year, rates, dividend yields and repo in basis
points, dividends also relative to the flows (-0.3 = cut by 30 %), correlations absolute, FX relative, the roll in business
days. Dicts are keyed by name (or currency, or pair); "*" applies to every one. Shocks add up (`a + b`) and round-trip
through JSON (`to_dict`, `from_dict`).

The rule of the volatility when a spot moves:
- sticky_moneyness: the surface moves with the forward (the model's own coordinate; a spot move only scales the paths);
- sticky_strike: implied vols stay at absolute strikes, the surface is read at k + ln(1 + h);
- realistic: sticky moneyness plus an at-the-money move of beta_T ln(1 + h) per tenor, with the betas of `RiskEnv`.

`apply(ctx, shock, env)` returns the shocked context and never touches the original. `resolve` splits a shock on a context
into what needs a new simulation (surfaces, correlation, the roll) and what only rescales the paths or the discounting
(spots, forwards, discount curves, FX): the engine groups the shocks that share a simulation.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field

import numpy as np

from optlab.risk import grid
from optlab.risk.curves import CurveMove, bumped_market
from optlab.risk.surfaces import bumped_surface
from optlab.structured.multiasset import BasketContext

RULES = ("sticky_moneyness", "sticky_strike", "realistic")


def _get(d: dict, key, default=None, combine=None):
    """The move of `key`: its own entry combined with the "*" entry (both apply), the default when neither is there."""
    star, own = d.get("*"), d.get(key)
    if star is None:
        return default if own is None else own
    if own is None or key == "*":
        return star
    if combine == "mult":
        return (1 + star) * (1 + own) - 1
    return star + own


def _merge(a: dict, b: dict, add):
    out = dict(a)
    for k, v in b.items():
        out[k] = add(out[k], v) if k in out else v
    return out


def _nodes_dict(d) -> dict:
    return {tuple(int(x) for x in str(k).split(",")): float(v) for k, v in (d or {}).items()}


@dataclass
class VolMove:
    """Moves of one name's implied volatilities."""

    parallel: float = 0.0
    relative: float = 0.0
    skew: float = 0.0
    nodes: dict = field(default_factory=dict)  # {(strike index, tenor index): absolute bump}
    relative_nodes: dict = field(default_factory=dict)  # {(i, j): relative bump}
    term: dict = field(default_factory=dict)  # {tenor index: absolute at-the-money bump, every strike}

    def __bool__(self):
        return bool(
            self.parallel
            or self.relative
            or self.skew
            or any(self.nodes.values())
            or any(self.relative_nodes.values())
            or any(self.term.values())
        )

    def __add__(self, o: "VolMove") -> "VolMove":
        f = lambda a, b: a + b  # noqa: E731
        return VolMove(
            self.parallel + o.parallel,
            self.relative + o.relative,
            self.skew + o.skew,
            _merge(self.nodes, o.nodes, f),
            _merge(self.relative_nodes, o.relative_nodes, f),
            _merge(self.term, o.term, f),
        )

    def matrices(self):
        shape = (len(grid.STRIKES), len(grid.TENORS))
        H, R, t = np.zeros(shape), np.zeros(shape), np.zeros(shape[1])
        for (i, j), v in self.nodes.items():
            H[i, j] += v
        for (i, j), v in self.relative_nodes.items():
            R[i, j] += v
        for j, v in self.term.items():
            t[int(j)] += v
        return H, R, t

    def apply(self, surface):
        H, R, t = self.matrices()
        return bumped_surface(surface, self.parallel, self.relative, self.skew, H, R, t)

    def to_dict(self) -> dict:
        return {
            "parallel": self.parallel,
            "relative": self.relative,
            "skew": self.skew,
            "nodes": {f"{i},{j}": v for (i, j), v in self.nodes.items()},
            "relative_nodes": {f"{i},{j}": v for (i, j), v in self.relative_nodes.items()},
            "term": {str(int(j)): v for j, v in self.term.items()},
        }

    @classmethod
    def from_dict(cls, d) -> "VolMove":
        if isinstance(d, (int, float)):
            return cls(float(d))
        d = d or {}
        return cls(
            float(d.get("parallel", 0.0)),
            float(d.get("relative", 0.0)),
            float(d.get("skew", 0.0)),
            _nodes_dict(d.get("nodes")),
            _nodes_dict(d.get("relative_nodes")),
            {int(j): float(v) for j, v in (d.get("term") or {}).items()},
        )

    def key(self):
        return (
            round(self.parallel, 12),
            round(self.relative, 12),
            round(self.skew, 12),
            tuple(sorted((k, round(v, 12)) for k, v in self.nodes.items() if v)),
            tuple(sorted((k, round(v, 12)) for k, v in self.relative_nodes.items() if v)),
            tuple(sorted((int(k), round(v, 12)) for k, v in self.term.items() if v)),
        )


@dataclass
class Shock:
    spot: dict = field(default_factory=dict)  # {name: relative move}
    vol: dict = field(default_factory=dict)  # {name: VolMove}
    rule: str = "sticky_moneyness"
    rates: dict = field(default_factory=dict)  # {currency: CurveMove}: discount and forward together
    discount: dict = field(default_factory=dict)  # {currency: CurveMove}: discounts only
    forward_rate: dict = field(default_factory=dict)  # {currency: CurveMove}: the rate in the forward only
    dividend: dict = field(default_factory=dict)  # {name: CurveMove} on the dividend yield
    dividend_relative: dict = field(default_factory=dict)  # {name: relative move of the flows}
    repo: dict = field(default_factory=dict)  # {name: CurveMove}
    corr: float = 0.0  # every pair
    corr_pairs: dict = field(default_factory=dict)  # {"A|B": move}
    fx: dict = field(default_factory=dict)  # {currency: relative move of its value in the reporting currency}
    quanto_corr: dict = field(default_factory=dict)  # {name: move of the correlation with its FX}
    fx_vol: dict = field(default_factory=dict)  # {name: absolute move of the FX vol of its quanto link}
    roll_days: int = 0  # business days
    rule_spot: dict | None = None  # the part of the spot move the vol rule applies to (None: all of it)

    def __post_init__(self):
        if self.rule not in RULES:
            raise ValueError(f"unknown vol rule {self.rule!r}, expected one of {', '.join(RULES)}")
        self.vol = {n: v if isinstance(v, VolMove) else VolMove.from_dict(v) for n, v in self.vol.items()}
        for name in ("rates", "discount", "forward_rate", "dividend", "repo"):
            setattr(
                self,
                name,
                {k: v if isinstance(v, CurveMove) else CurveMove.from_dict(v) for k, v in getattr(self, name).items()},
            )

    def __add__(self, o: "Shock") -> "Shock":
        if self.rule != o.rule and "sticky_moneyness" not in (self.rule, o.rule):
            raise ValueError(f"cannot add shocks under {self.rule} and {o.rule}")
        rule = o.rule if self.rule == "sticky_moneyness" else self.rule
        add = lambda a, b: a + b  # noqa: E731
        mult = lambda a, b: (1 + a) * (1 + b) - 1  # noqa: E731
        rule_spot = None  # what the rule applies to: the spot moves of the shocks that carry it
        if rule != "sticky_moneyness":
            rule_spot = {}
            for x in (self, o):
                if x.rule != "sticky_moneyness":
                    rule_spot = _merge(rule_spot, x.spot if x.rule_spot is None else x.rule_spot, mult)
        return Shock(
            _merge(self.spot, o.spot, lambda a, b: (1 + a) * (1 + b) - 1),
            _merge(self.vol, o.vol, add),
            rule,
            _merge(self.rates, o.rates, add),
            _merge(self.discount, o.discount, add),
            _merge(self.forward_rate, o.forward_rate, add),
            _merge(self.dividend, o.dividend, add),
            _merge(self.dividend_relative, o.dividend_relative, lambda a, b: (1 + a) * (1 + b) - 1),
            _merge(self.repo, o.repo, add),
            self.corr + o.corr,
            _merge(self.corr_pairs, o.corr_pairs, add),
            _merge(self.fx, o.fx, lambda a, b: (1 + a) * (1 + b) - 1),
            _merge(self.quanto_corr, o.quanto_corr, add),
            _merge(self.fx_vol, o.fx_vol, add),
            self.roll_days + o.roll_days,
            rule_spot,
        )

    def to_dict(self) -> dict:
        d = {
            "spot": dict(self.spot),
            "vol": {n: v.to_dict() for n, v in self.vol.items()},
            "rule": self.rule,
            **{
                k: {c: m.to_dict() for c, m in getattr(self, k).items()}
                for k in ("rates", "discount", "forward_rate", "dividend", "repo")
            },
            "dividend_relative": dict(self.dividend_relative),
            "corr": self.corr,
            "corr_pairs": dict(self.corr_pairs),
            "fx": dict(self.fx),
            "quanto_corr": dict(self.quanto_corr),
            "fx_vol": dict(self.fx_vol),
            "roll_days": self.roll_days,
            "rule_spot": None if self.rule_spot is None else dict(self.rule_spot),
        }
        return {k: v for k, v in d.items() if v or k == "rule"}

    @classmethod
    def from_dict(cls, d: dict) -> "Shock":
        d = dict(d or {})
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown shock field(s): {', '.join(sorted(unknown))}")
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


BASE = Shock()


@dataclass
class RiskEnv:
    """What a shock needs beyond the context: spot-vol betas per name ({name: list per tenor}), quanto links
    ({name: (rho, vol_fx)}), the reporting currency, and SLV settings when the base model is SLV."""

    betas: dict = field(default_factory=dict)
    quanto: dict = field(default_factory=dict)
    currency: str = "USD"
    slv_particles: int = 100_000


@dataclass
class NameMove:
    """A shock read for one name of a context."""

    spot: float = 0.0
    vol: VolMove = field(default_factory=VolMove)
    strike_shift: float = 0.0  # sticky strike: the surface is read at k + ln(1 + strike_shift)
    carry: CurveMove = field(default_factory=CurveMove)  # bp on the forward's carry (rates up, dividends and repo down)
    dividend_relative: float = 0.0
    quanto_drift: float = 0.0  # change of rho * vol_fx in the quanto forward F exp(-rho vol_fx int sigma)


def name_moves(shock: Shock, name: str, currency: str, env: RiskEnv | None = None) -> NameMove:
    env = env or RiskEnv()
    h = float(_get(shock.spot, name, 0.0, "mult"))
    hr = h if shock.rule_spot is None else float(_get(shock.rule_spot, name, 0.0, "mult"))
    vol = _get(shock.vol, name, VolMove()) or VolMove()
    strike_shift = 0.0
    if hr and shock.rule == "sticky_strike":
        strike_shift = hr
    elif hr and shock.rule == "realistic":
        betas = env.betas.get(name)
        if betas is None:
            raise KeyError(f"no spot-vol beta of {name}: the realistic rule needs RiskEnv.betas")
        vol = vol + VolMove(term={j: float(b) * float(np.log1p(hr)) for j, b in enumerate(betas)})
    carry = CurveMove()
    for d, sign in ((shock.rates, 1.0), (shock.forward_rate, 1.0)):
        m = _get(d, currency)
        if m:
            carry = carry + m.scaled(sign)
    for d in (shock.dividend, shock.repo):
        m = _get(d, name)
        if m:
            carry = carry + m.scaled(-1.0)
    drift = 0.0
    dc, dv = float(_get(shock.quanto_corr, name, 0.0)), float(_get(shock.fx_vol, name, 0.0))
    if (dc or dv) and name in env.quanto:
        rho, vfx = env.quanto[name]
        drift = (rho + dc) * (vfx + dv) - rho * vfx
    return NameMove(h, vol, strike_shift, carry, float(_get(shock.dividend_relative, name, 0.0, "mult")), drift)


def discount_move(shock: Shock, currency: str) -> CurveMove:
    m = CurveMove()
    for d in (shock.rates, shock.discount):
        got = _get(d, currency)
        if got:
            m = m + got
    return m


def context_currency(ctx) -> str:
    cur = getattr(ctx, "payment_currency", None)
    if cur:
        return cur
    from optlab.instruments import _BY_ID

    name = ctx.names[0] if isinstance(ctx, BasketContext) else ctx.underlying
    inst = _BY_ID.get(name)
    return inst.currency if inst is not None else "USD"


def moved_corr(corr, shock: Shock, names) -> np.ndarray:
    """The correlation matrix with every pair moved by shock.corr and the listed pairs by their own move, repaired."""
    from optlab.correlation import nearest_correlation

    c = np.array(corr, dtype=float)
    if not shock.corr and not shock.corr_pairs:
        return c
    n = len(names)
    move = np.full((n, n), shock.corr)
    for key, v in shock.corr_pairs.items():
        a, b = key.split("|")
        if a in names and b in names:
            i, j = names.index(a), names.index(b)
            move[i, j] += v
            move[j, i] += v
    np.fill_diagonal(move, 0.0)
    c = np.clip(c + move, -0.999, 0.999)
    np.fill_diagonal(c, 1.0)
    try:
        np.linalg.cholesky(c)
        return c
    except np.linalg.LinAlgError:
        return nearest_correlation(c)


def _quanto_forward(market, surface, drift: float):
    """Forwards moved by exp(-drift int_0^T sigma): the quanto adjustment for a change of rho * vol_fx."""
    if not drift:
        return market
    from optlab.market import Market
    from optlab.structured.quanto import integrated_vol

    t = np.asarray(market.to_dict()["pillars"], dtype=float)
    return Market(
        market.spot, t, np.asarray(market.D(t)), np.asarray(market.F(t)) * np.exp(-drift * integrated_vol(t, surface))
    )


def shocked_market(market, surface, move: NameMove, discount: CurveMove | None = None, with_spot: bool = True):
    m = _quanto_forward(market, surface, move.quanto_drift)
    m = bumped_market(m, discount, move.carry, move.dividend_relative)
    if with_spot and move.spot:
        m = m.scaled(m.spot * (1 + move.spot))
    return m


def shocked_surface(surface, move: NameMove):
    """Sticky strike reindexing first (the base smile at absolute strikes), then the vol moves."""
    from optlab.structured.greeks import StickyStrikeSurface

    s = surface
    if move.strike_shift and not np.isscalar(s):
        s = StickyStrikeSurface(s, move.strike_shift)
    return move.vol.apply(s) if move.vol else s


def roll_date(valuation, days: int, calendar: str = "nyse"):
    from optlab.structured.calendars import calendar as get_calendar

    if not days:
        return valuation
    return get_calendar(calendar).add_business_days(valuation, int(days))


def apply(ctx, shock: Shock, env: RiskEnv | None = None):
    """The context with the whole shock applied (spots included: sticky moneyness scales the market). The roll moves the
    valuation date only: curves and surface are read in remaining maturity, so they stay as they are."""
    env = env or RiskEnv()
    cur = context_currency(ctx)
    disc = discount_move(shock, cur)
    if isinstance(ctx, BasketContext):
        markets, vols = dict(ctx.markets), dict(ctx.vols)
        for n in ctx.names:
            mv = name_moves(shock, n, cur, env)
            markets[n] = shocked_market(ctx.markets[n], ctx.vols[n], mv, disc)
            vols[n] = shocked_surface(ctx.vols[n], mv)
        out = BasketContext(
            ctx.names,
            markets,
            vols,
            moved_corr(ctx.corr, shock, list(ctx.names)),
            ctx.valuation,
            ctx.contexts,
            ctx.correlation_cache,
            ctx.payment_currency,
        )
    else:
        mv = name_moves(shock, ctx.underlying, cur, env)
        out = dataclasses.replace(
            ctx,
            market=shocked_market(ctx.market, ctx.surface, mv, disc),
            surface=shocked_surface(ctx.surface, mv),
            slv_cache={},
        )
    if shock.roll_days:
        out = dataclasses.replace(out, valuation=roll_date(ctx.valuation, shock.roll_days))
    return out


def sim_key(ctx, shock: Shock, env: RiskEnv | None = None):
    """What needs a simulation of its own: surfaces (vol moves, sticky strike, realistic betas), correlation, the roll."""
    cur = context_currency(ctx)
    names = ctx.names if isinstance(ctx, BasketContext) else (ctx.underlying,)
    parts = []
    for n in names:
        mv = name_moves(shock, n, cur, env)
        parts.append((n, round(mv.strike_shift, 12), mv.vol.key()))
    corr = ()
    if isinstance(ctx, BasketContext) and (shock.corr or shock.corr_pairs):
        corr = (round(shock.corr, 12), tuple(sorted((k, round(v, 12)) for k, v in shock.corr_pairs.items())))
    return (tuple(parts), corr, int(shock.roll_days))


def simulation_part(ctx, shock: Shock, env: RiskEnv | None = None):
    """The context to simulate on: surfaces and correlation moved, the roll, no spot, forward or discount move."""
    cur = context_currency(ctx)
    if isinstance(ctx, BasketContext):
        vols = {n: shocked_surface(ctx.vols[n], name_moves(shock, n, cur, env)) for n in ctx.names}
        out = BasketContext(
            ctx.names,
            dict(ctx.markets),
            vols,
            moved_corr(ctx.corr, shock, list(ctx.names)),
            ctx.valuation,
            ctx.contexts,
            ctx.correlation_cache,
            ctx.payment_currency,
        )
    else:
        mv = name_moves(shock, ctx.underlying, cur, env)
        surface = shocked_surface(ctx.surface, mv)
        out = ctx if surface is ctx.surface else dataclasses.replace(ctx, surface=surface, slv_cache={})
    if shock.roll_days:
        out = dataclasses.replace(out, valuation=roll_date(ctx.valuation, shock.roll_days))
    return out


def fx_factor(shock: Shock, currency: str, report: str) -> float:
    """Value in the reporting currency of one unit of `currency` after the shock, relative to before."""
    if currency == report:
        return 1.0
    return 1.0 + float(_get(shock.fx, currency, 0.0, "mult"))
