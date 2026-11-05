"""A desk price: a base price, and what is added to it line by line, each with its cost and standard error.

    Base            the policy's model
    + Bumps         one cost per market parameter, signed by the trader and applied as given
    = Client price  the coupon is solved on this price and the margin

Every line runs on the random numbers of the base (same seed, same batches), so a small cost is not lost in the noise: its standard
error comes from the differences of the antithetic pairs. A bump of the forward (dividends, repo, spot) keeps the path in
x = ln(S / F): the surface stays in moneyness, so these lines are new evaluations of the same simulation. A bump of the surface needs
a simulation of its own. The price uses all the bumps together; the interaction line is that price less the sum of the single bumps.

Prices are the investor's value in % of notional. The cost of a line is seen from the desk: what it adds to the price of what the
desk sells, or takes from the price of what it buys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..market import Market
from ..models import LocalVolModel
from .pricer import StructuredResult, history_times, price_many
from .replication import replication_result
from .script.timeline import Clock, build_timeline
from .solve import combine
from .surfaces import BumpedSurface, surface_arbitrage

# Fractions, like every level of the library: vol 0.005 is half a vol point, dividend 0.1 is 10 % of the dividends, repo 0.001 is 10 bp.
BUMPS = {"vol": "Volatility, parallel", "skew": "Volatility skew (90-110 % at 1 year)", "dividend": "Dividends", "repo": "Repo", "spot": "Spot, delta bid-ask"}
FORWARD_BUMPS = ("dividend", "repo", "spot")
SURFACE_BUMPS = ("vol", "skew")
SIDES = {"sell": 1.0, "buy": -1.0}


def forward_bumped(market: Market, dividend: float = 0.0, repo: float = 0.0, spot: float = 0.0) -> Market:
    """The market with its forward moved, the discounts unchanged. `dividend` scales what the forward has lost to dividends and repo up
    to each date, ln(S / (F D)), so steps and season stay where they are; `repo` is a rate paid until each date; `spot` scales the spot."""
    T = market._t[1:]
    F, D = market.F(T), market.D(T)
    carried = np.log(market.spot / (F * D))
    return Market(market.spot * (1.0 + spot), T, D, F * (1.0 + spot) * np.exp(-dividend * carried - repo * T))


@dataclass(frozen=True)
class CostInputs:
    """What to add to a base price. `bumps` holds the values used, signed; `defaults` the ones they replace, for the summary."""
    bumps: dict = field(default_factory=dict)
    side: str = "sell"
    defaults: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.side not in SIDES:
            raise ValueError(f"unknown side {self.side!r}, expected sell or buy")
        unknown = set(self.bumps) - set(BUMPS)
        if unknown:
            raise ValueError(f"unknown bump(s): {', '.join(sorted(unknown))}")

    @property
    def active(self) -> dict:
        return {k: float(v) for k, v in self.bumps.items() if v}

    @property
    def sign(self) -> float:
        return SIDES[self.side]


@dataclass
class CostLine:
    key: str
    label: str
    kind: str  # bump or interaction
    cost: float  # % of notional, seen from the desk: positive is against it
    se: float
    coupon_bp: float | None = None  # effect on the solved coupon, in bp per annum
    used: float | None = None
    default: float | None = None
    overridden: bool = False

    @property
    def in_price(self) -> bool:
        return self.kind != "interaction"


@dataclass
class DeskPrice:
    side: str
    base_price: float  # % of notional, at the solved or given coupon
    base_se: float
    final_price: float  # what the coupon is solved on
    final_se: float
    lines: list
    coupon_base: float | None = None  # per annum: the coupon with no cost at all
    coupon_final: float | None = None
    seconds: float = 0.0
    warnings: list = field(default_factory=list)

    def line(self, key: str) -> CostLine:
        return next(x for x in self.lines if x.key == key)

    def table(self) -> str:
        rows = [f"{'base':<38} {self.base_price:10.4f} +/- {self.base_se:.4f} %"]
        for x in self.lines:
            bp = f"{x.coupon_bp:+8.1f} bp" if x.coupon_bp is not None else ""
            used = "" if x.used is None else f"used {x.used:+.4f}" + (" (overridden)" if x.overridden else "")
            rows.append(f"{x.label:<38} {x.cost:+10.4f} +/- {x.se:.4f} % {bp} {used}".rstrip())
        rows.append(f"{'client price (' + self.side + ')':<38} {self.final_price:10.4f} +/- {self.final_se:.4f} %")
        return "\n".join(rows)


class _Run:
    """Results of one line: one per parameter set (coupon 0 and 1 when solving, else the coupon of the sheet), on the base's paths."""

    def __init__(self, results: list[StructuredResult]):
        self.results = results

    def at(self, x: float | None) -> StructuredResult:
        return self.results[0] if x is None else combine(self.results[0], self.results[1], x)

    def price(self, x: float | None) -> float:
        return self.at(x).price


def _pct_pairs(result: StructuredResult) -> np.ndarray | None:
    return None if result.pairs is None else 100.0 * result.pairs / result.notional


def _mean_se(pairs: np.ndarray | None, price: float) -> tuple[float, float]:
    if pairs is None:
        return price, 0.0
    return float(pairs.mean()), float(pairs.std(ddof=1) / np.sqrt(len(pairs)))


class _Lines:
    """Runs the base and the bumped lines of one product on common random numbers."""

    def __init__(self, product, ctx, fmt, decision, profile, sets, inputs: CostInputs, fixings=None, workers=None, fast=False):
        self.product, self.ctx, self.fmt, self.decision, self.sets = product, ctx, fmt, decision, sets
        self.inputs, self.fixings, self.workers, self.fast = inputs, fixings, workers, fast
        self.program = product.program
        self.clock = Clock(product.valuation, product.calendar)
        self.timeline = build_timeline(self.program, self.clock)
        self.T_obs = (profile.observation_times[-1] if profile.observation_times else self.timeline.horizon)
        self.horizon = max(product.horizon, 1 / 365)
        self.name = self.program.underlying_names[0]
        self.S0 = ctx.market.spot

    def _model(self, surface):
        if self.decision.model != "lv":
            raise ValueError(f"bumps run under local vol or replication, not {self.decision.model}")
        if surface is self.ctx.surface:
            return self.ctx.model("lv", self.horizon, fast=True) if self.fast else self.ctx.model("lv", self.horizon)
        if self.fast:
            from .fastmodels import TabulatedLocalVol
            return TabulatedLocalVol(surface)
        return LocalVolModel(surface)

    def _simulation(self, surface, markets: list) -> list[_Run]:
        """One simulation on `surface`, one line per bumped market of `markets`."""
        if self.decision.method == "replication":
            history = history_times(self.fixings, self.name, self.clock) or None
            return [_Run([replication_result(self.program, self.timeline, surface, m, self.T_obs, self.fmt, p, initial_fixing=self.S0, history=history)
                          for p in self.sets]) for m in markets]
        times = self.timeline.sim_times
        base_F = self.ctx.market.F(times)
        factors = [m.F(times) / base_F if m is not self.ctx.market else 1.0 for m in markets]
        expanded = [(p, f) for f in factors for p in self.sets]
        d = self.decision
        out = price_many(self.program, self._model(surface), self.ctx.market, [p for p, _ in expanded], self.fmt, d.n_paths, d.seed, d.batch,
                         valuation=self.product.valuation, fixings=self.fixings, calendar=self.product.calendar, workers=self.workers,
                         spot_factors=[f for _, f in expanded], initial_fixing=self.S0)
        n = len(self.sets)
        return [_Run(out[i * n:(i + 1) * n]) for i in range(len(markets))]

    def run(self) -> tuple[_Run, dict, _Run | None]:
        """(base, {key: run of that bump alone}, run of all the bumps together, or None with fewer than two)."""
        active = self.inputs.active
        ctx = self.ctx
        forward_keys = [k for k in FORWARD_BUMPS if k in active]
        markets = [ctx.market] + [forward_bumped(ctx.market, **{k: active[k]}) for k in forward_keys]
        together = len(active) > 1
        surface_all = None
        if SURFACE_BUMPS[0] in active or SURFACE_BUMPS[1] in active:
            surface_all = BumpedSurface(ctx.surface, active.get("vol", 0.0), active.get("skew", 0.0))
        market_all = forward_bumped(ctx.market, **{k: active[k] for k in forward_keys}) if forward_keys else ctx.market
        if together and surface_all is None:  # only forward bumps: the same simulation serves them all
            markets.append(market_all)
        runs = self._simulation(ctx.surface, markets)
        base, single = runs[0], dict(zip(forward_keys, runs[1:]))
        everything = runs[-1] if together and surface_all is None else None
        for k in SURFACE_BUMPS:
            if k in active:
                surface = BumpedSurface(ctx.surface, active[k] if k == "vol" else 0.0, active[k] if k == "skew" else 0.0)
                if not together or len([b for b in active if b in SURFACE_BUMPS]) > 1:
                    single[k] = self._simulation(surface, [ctx.market])[0]
                else:  # one surface bump and forward bumps: the bump alone and all together share a simulation
                    alone, everything = self._simulation(surface, [ctx.market, market_all])
                    single[k] = alone
        if together and everything is None:
            everything = self._simulation(surface_all, [market_all])[0]
        return base, single, everything


def _solved(base: _Run, effect, target: float) -> float:
    """The coupon parameter x with base(x) + effect(x) = target, both affine in x on common paths."""
    b0, b1 = base.price(0.0), base.price(1.0)
    e0, e1 = effect(0.0), effect(1.0)
    slope = (b1 + e1) - (b0 + e0)
    if abs(slope) < 1e-14:
        raise ValueError("the price does not depend on the coupon")
    return (target - b0 - e0) / slope


def desk_price(product, ctx, fmt, decision, profile, inputs: CostInputs, target: float | None, coupon_param: str | None, coupon_scale: float,
               fixings=None, workers=None, fast=False) -> tuple[DeskPrice, _Run, float | None, float | None]:
    """The lines of one product. With a `target`, the coupon is solved so that the client price is the target. Returns (the desk price,
    the base run, the solved coupon parameter, its standard error)."""
    t0 = time.perf_counter()
    solving = target is not None
    sets = [{coupon_param: 0.0}, {coupon_param: 1.0}] if solving else [{}]
    lines = _Lines(product, ctx, fmt, decision, profile, sets, inputs, fixings, workers, fast)
    base, single, everything = lines.run()
    moved = everything if everything is not None else next(iter(single.values()), None)

    def effect(x):
        return 0.0 if moved is None else moved.price(x) - base.price(x)

    x = _solved(base, effect, target) if solving else None
    at_base = base.at(x)
    base_pairs = _pct_pairs(at_base)
    s = inputs.sign
    scale = coupon_scale if solving else 0.0
    slope_rate = (base.price(1.0) - base.price(0.0)) * scale if solving else 0.0

    def line(key, label, kind, pairs, price_effect, **kw):
        _, se = _mean_se(pairs, price_effect)
        bp = -1e4 * price_effect / slope_rate if solving else None
        return CostLine(key, label, kind, s * price_effect, se, bp, **kw)

    out, total_single = [], 0.0
    for key, label in BUMPS.items():
        if key not in single:
            continue
        r = single[key].at(x)
        pairs = None if base_pairs is None else _pct_pairs(r) - base_pairs
        d = float(pairs.mean()) if pairs is not None else r.price - at_base.price
        total_single += d
        default = inputs.defaults.get(key)
        used = inputs.active[key]
        out.append(line(key, label, "bump", pairs, d, used=used, default=default, overridden=default is not None and default != used))
    final_pairs = base_pairs
    if moved is not None:
        r = moved.at(x)
        final_pairs = _pct_pairs(r)
        if everything is not None:
            pairs = None if base_pairs is None else final_pairs - base_pairs
            d = (float(pairs.mean()) if pairs is not None else r.price - at_base.price) - total_single
            singles = None if pairs is None else sum(_pct_pairs(single[k].at(x)) - base_pairs for k in single)
            out.append(line("interaction", "Interaction of the bumps", "interaction", None if pairs is None else pairs - singles, d))
    final_price = at_base.price + effect(x)
    _, final_se = _mean_se(final_pairs, final_price)
    warnings = []
    if "vol" in inputs.active or "skew" in inputs.active:
        warnings += _arbitrage_warnings(ctx.surface, inputs, lines.horizon)
    rate_se = None
    if solving:
        slope_x = (base.price(1.0) + effect(1.0)) - (base.price(0.0) + effect(0.0))
        rate_se = final_se / abs(slope_x)
    desk = DeskPrice(inputs.side, at_base.price, at_base.stderr, final_price, final_se, out,
                     (target - base.price(0.0)) / (base.price(1.0) - base.price(0.0)) / coupon_scale if solving else None,
                     x / coupon_scale if solving else None, time.perf_counter() - t0, warnings)
    return desk, base, x, rate_se


def _arbitrage_warnings(surface, inputs: CostInputs, horizon: float) -> list:
    """A bump must not add arbitrage: what the unbumped surface already holds is not the bump's doing."""
    bumped = BumpedSurface(surface, inputs.active.get("vol", 0.0), inputs.active.get("skew", 0.0))
    got, base = surface_arbitrage(bumped, horizon), surface_arbitrage(surface, horizon)
    out = []
    if not got["butterfly_free"] and got["min_g"] < min(base["min_g"], 0.0) - 1e-6:
        out.append(f"the bumped surface has butterfly arbitrage (density g down to {got['min_g']:.3f}): reduce the vol or skew bump")
    if not got["calendar_free"] and got["min_wT"] < min(base["min_wT"], 0.0) - 1e-6:
        out.append(f"the bumped surface has calendar arbitrage (dw/dT down to {got['min_wT']:.4f}): reduce the vol or skew bump")
    return out
