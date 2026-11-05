"""A desk price: a base price, and what is added to it line by line, each with its cost and standard error.

    Base            the policy's model: local vol, or replication for a European product
    + Provisions    max(0, gap against the desk) per model of the product's family
    + Bumps         one cost per market parameter, signed by the trader and applied as given
    + Digitals      the cost of reading each digital as a call spread (digitals.py)
    = Client price  the coupon is solved on this price and the margin

Every line runs on the random numbers of the base (same seed, same batches), so a small cost is not lost in the noise: its standard
error comes from the differences of the antithetic pairs. A bump of the forward (dividends, repo, spot) keeps the path in
x = ln(S / F): the surface stays in moneyness, so these lines are new evaluations of the same simulation. A bump of the surface or of
the correlation needs a simulation of its own. The price uses all the bumps together; the interaction line is that price less the sum
of the single bumps. A model of provision runs on the spot draws of the base (`SLVModel.spot_driven`). Each price is affine in the coupon
on these paths, but a provision is floored at zero, so the solve iterates on which provisions are on: a provision at a gap of zero has a small
positive bias, about 0.4 of the gap's standard error, which is not removed by a threshold. The width of a coupon's digital depends on the
coupon, so the digitals take a second solve when their widths have moved.

Prices are the investor's value in % of notional. The cost of a line is seen from the desk: what it adds to the price of what the
desk sells, or takes from the price of what it buys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import numpy as np

from ..market import Market
from ..models import LocalVolModel
from .digitals import KINDS
from .multiasset import BasketContext
from .pricer import StructuredResult, history_times, price_many
from .replication import replication_result
from .script.timeline import Clock, build_timeline
from .solve import combine
from .surfaces import BumpedSurface, surface_arbitrage

# Fractions, like every level of the library: vol 0.005 is half a vol point, corr 0.01 is one correlation point, dividend 0.1 is 10 % of
# the dividends, repo 0.001 is 10 bp.
BUMPS = {"vol": "Volatility, parallel", "skew": "Volatility skew (90-110 % at 1 year)", "corr": "Correlation", "dividend": "Dividends",
         "repo": "Repo", "spot": "Spot, delta bid-ask"}
FORWARD_BUMPS = ("dividend", "repo", "spot")
SIMULATION_BUMPS = ("vol", "skew", "corr")
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
    """What to add to a base price. `bumps` holds the values used, signed; `defaults` the ones they replace, for the summary.
    `candidates` holds two values for a parameter whose groups disagree in sign: the one against the desk on this product is kept, measured."""
    bumps: dict = field(default_factory=dict)
    side: str = "sell"
    defaults: dict = field(default_factory=dict)
    candidates: dict = field(default_factory=dict)
    mix: float | None = None  # mixing fraction of the SLV provision; None keeps the market's
    min_spread: float | None = None  # digitals as call spreads when given, with `delta_max` by kind of digital
    delta_max: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.side not in SIDES:
            raise ValueError(f"unknown side {self.side!r}, expected sell or buy")
        unknown = (set(self.bumps) | set(self.candidates)) - set(BUMPS)
        if unknown:
            raise ValueError(f"unknown bump(s): {', '.join(sorted(unknown))}")

    @property
    def active(self) -> dict:
        return {k: float(v) for k, v in self.bumps.items() if v}

    @property
    def overridden_mix(self) -> bool:
        default = self.defaults.get("slv_mix")
        return default is not None and self.mix is not None and default != self.mix

    @property
    def sign(self) -> float:
        return SIDES[self.side]


@dataclass
class CostLine:
    key: str
    label: str
    kind: str  # model, bump, digital or interaction
    cost: float  # % of notional, seen from the desk: positive is against it
    se: float
    coupon_bp: float | None = None  # effect on the solved coupon, in bp per annum
    used: float | None = None
    default: float | None = None
    overridden: bool = False
    note: str = ""
    gap: float | None = None  # a model: the gap before the floor, seen from the desk
    detail: list = field(default_factory=list)  # digitals: date, level, jump, width, side, alone

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
    passes: int = 0  # solves until the provisions in force stopped changing
    model_results: dict = field(default_factory=dict, repr=False)  # model -> its StructuredResult at the coupon
    digital_passes: int = 0  # simulations until the widths of the digitals stopped moving

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


def _se(pairs: np.ndarray | None) -> float:
    return 0.0 if pairs is None else float(pairs.std(ddof=1) / np.sqrt(len(pairs)))


class _Lines:
    """Runs a product under bumped markets on common random numbers: a request is (bumps of the simulation, bumps of the forward)."""

    def __init__(self, product, ctx, fmt, decision, profile, sets, fixings=None, workers=None, fast=False):
        self.product, self.ctx, self.fmt, self.decision, self.sets = product, ctx, fmt, decision, sets
        self.fixings, self.workers, self.fast = fixings, workers, fast
        self.program = product.program
        self.clock = Clock(product.valuation, product.calendar)
        self.timeline = build_timeline(self.program, self.clock)
        self.T_obs = (profile.observation_times[-1] if profile.observation_times else self.timeline.horizon)
        self.horizon = max(product.horizon, 1 / 365)
        self.names = self.program.underlying_names
        self.basket = isinstance(ctx, BasketContext)
        self.initial = {n: ctx.markets[n].spot for n in self.names} if self.basket else ctx.market.spot

    def _model(self, bumps: dict):
        if self.decision.model != "lv":
            raise ValueError(f"bumps run under local vol or replication, not {self.decision.model}")
        ctx = self.ctx
        if self.basket:
            return (ctx.with_bumps(**bumps) if bumps else ctx).model("lv", self.horizon, self.names)
        if not bumps:
            return ctx.model("lv", self.horizon, fast=True) if self.fast else ctx.model("lv", self.horizon)
        surface = BumpedSurface(ctx.surface, bumps.get("vol", 0.0), bumps.get("skew", 0.0))
        if self.fast:
            from .fastmodels import TabulatedLocalVol
            return TabulatedLocalVol(surface)
        return LocalVolModel(surface)

    def _factor(self, forward: dict):
        """What the paths are multiplied by, per simulated date: one array, or one per name."""
        if not forward:
            return 1.0
        times = self.timeline.sim_times
        if self.basket:
            return {n: forward_bumped(self.ctx.markets[n], **forward).F(times) / self.ctx.markets[n].F(times) for n in self.names}
        return forward_bumped(self.ctx.market, **forward).F(times) / self.ctx.market.F(times)

    def _simulation(self, bumps: dict, forwards: list) -> list[_Run]:
        d = self.decision
        if d.method == "replication":
            name = self.names[0]
            history = history_times(self.fixings, name, self.clock) or None
            surface = BumpedSurface(self.ctx.surface, bumps.get("vol", 0.0), bumps.get("skew", 0.0)) if bumps else self.ctx.surface
            return [_Run([replication_result(self.program, self.timeline, surface, forward_bumped(self.ctx.market, **f) if f else self.ctx.market,
                                             self.T_obs, self.fmt, p, initial_fixing=self.initial, history=history) for p in self.sets])
                    for f in forwards]
        factors = [self._factor(f) for f in forwards]
        expanded = [(p, f) for f in factors for p in self.sets]
        out = price_many(self.program, self._model(bumps), self.ctx.market, [p for p, _ in expanded], self.fmt, d.n_paths, d.seed, d.batch,
                         valuation=self.product.valuation, fixings=self.fixings, calendar=self.product.calendar, workers=self.workers,
                         spot_factors=[f for _, f in expanded], initial_fixing=self.initial)
        n = len(self.sets)
        return [_Run(out[i * n:(i + 1) * n]) for i in range(len(forwards))]

    def model_run(self, name: str, mix: float | None) -> _Run:
        """A model of provision on the spot draws of the base, for the same sets of parameters."""
        if name != "slv" or self.basket:
            raise ValueError(f"no provision model {name!r} on this context")
        model = replace(self.ctx.slv_model(self.horizon, mix), spot_driven=True)
        if self.fast:
            from .fastmodels import fast_slv
            model = fast_slv(model)
        d = self.decision
        return _Run(price_many(self.program, model, self.ctx.market, self.sets, self.fmt, d.n_paths, d.seed, d.batch, valuation=self.product.valuation,
                               fixings=self.fixings, calendar=self.product.calendar, workers=self.workers, initial_fixing=self.initial))

    def ramp_run(self, requests: list, params: list | None = None) -> list[_Run]:
        """One line per set of ramps, on one simulation of the base: for each set of parameters, or at the `params` given."""
        sets = params if params is not None else self.sets
        d = self.decision
        if d.method == "replication":
            name = self.names[0]
            history = history_times(self.fixings, name, self.clock) or None
            return [_Run([replication_result(self.program, self.timeline, self.ctx.surface, self.ctx.market, self.T_obs, self.fmt, p, initial_fixing=self.initial,
                                             history=history, ramps=r) for p in sets]) for r in requests]
        expanded = [(p, r) for r in requests for p in sets]
        out = price_many(self.program, self._model({}), self.ctx.market, [p for p, _ in expanded], self.fmt, d.n_paths, d.seed, d.batch,
                         valuation=self.product.valuation, fixings=self.fixings, calendar=self.product.calendar, workers=self.workers,
                         initial_fixing=self.initial, ramps=[r for _, r in expanded])
        n = len(sets)
        return [_Run(out[i * n:(i + 1) * n]) for i in range(len(requests))]

    def run(self, requests: dict) -> dict:
        """{key: run} for {key: bumps}, one simulation per distinct set of simulation bumps."""
        groups: dict = {}
        for key, bumps in requests.items():
            active = {k: float(v) for k, v in bumps.items() if v}
            sim = {k: v for k, v in active.items() if k in SIMULATION_BUMPS}
            fwd = {k: v for k, v in active.items() if k in FORWARD_BUMPS}
            groups.setdefault(tuple(sorted(sim.items())), (sim, []))[1].append((key, fwd))
        out = {}
        for sim, items in groups.values():
            for (key, _), run in zip(items, self._simulation(sim, [f for _, f in items])):
                out[key] = run
        return out


def _solved(base: _Run, effect, provisions: dict, sign: float, target: float) -> tuple[float, int]:
    """The coupon parameter x with base(x) + effect(x) + the provisions in force at x = target, and the passes it took. Each price is affine
    in x on common paths; which provisions are in force is what the passes settle."""
    b0, b1 = base.price(0.0), base.price(1.0)
    e0, e1 = effect(0.0), effect(1.0)
    gaps = {k: (r.price(0.0) - b0, r.price(1.0) - b1) for k, r in provisions.items()}
    on = dict.fromkeys(gaps, True)
    x, passes = 0.0, 0
    for passes in range(1, 9):
        at0 = b0 + e0 + sum(g[0] for k, g in gaps.items() if on[k])
        at1 = b1 + e1 + sum(g[1] for k, g in gaps.items() if on[k])
        if abs(at1 - at0) < 1e-14:
            raise ValueError("the price does not depend on the coupon")
        x = (target - at0) / (at1 - at0)
        now = {k: sign * (g[0] + x * (g[1] - g[0])) > 0 for k, g in gaps.items()}
        if now == on:
            break
        on = now
    return x, passes


def _event_index(lines: _Lines, digital) -> int:
    return next(k for k, ev in enumerate(lines.program.events) if str(getattr(ev.when, "value", "")) == digital.date)


class _DigitalRun:
    """The digitals of one product as call spreads: the side of each one measured alone, against the desk, then the widths at the solved coupon."""

    def __init__(self, inputs: CostInputs, lines: _Lines, coupon_param: str | None, base: _Run):
        self.inputs, self.lines, self.param, self.base = inputs, lines, coupon_param, base
        self.sides: dict = {}
        self.alone: dict = {}

    def found(self, x: float | None) -> list:
        from .digitals import find_digitals
        return find_digitals(self.lines.product, {self.param: x} if x is not None and self.param else None)

    def _ramp(self, d, side):
        return d.ramp(side, self.inputs.min_spread, self.inputs.delta_max)

    def _key(self, d):
        return (_event_index(self.lines, d), d.line)

    def settle_sides(self, x: float | None) -> None:
        """Each digital alone on both sides at the coupon x: the side against the desk stays."""
        digitals = self.found(x)
        requests = [{self._key(d): self._ramp(d, side)} for d in digitals for side in ("up", "down")]
        if not requests:
            return
        params = [{self.param: x}] if x is not None else [{}]
        runs = self.lines.ramp_run(requests, params)
        s = self.inputs.sign
        base_price = self.base.price(x)
        for i, d in enumerate(digitals):
            up, down = runs[2 * i].results[0], runs[2 * i + 1].results[0]
            against_up = s * (up.price - base_price) >= s * (down.price - base_price)
            self.sides[self._key(d)] = "up" if against_up else "down"
            self.alone[self._key(d)] = s * ((up if against_up else down).price - base_price)

    def ramps(self, digitals) -> dict:
        return {self._key(d): self._ramp(d, self.sides[self._key(d)]) for d in digitals if self._key(d) in self.sides}

    def by_kind(self, digitals) -> dict:
        kinds: dict = {}
        for d in digitals:
            kinds.setdefault(d.kind, []).append(d)
        return kinds


def _settled(inputs: CostInputs, lines: _Lines, base_requests: dict, target, sets_solving: bool) -> tuple[dict, dict]:
    """Run the base, the single bumps and the two sides of each conflict, then keep the side against the desk. Returns ({key: value used},
    {key: run of that bump alone, "base": run})."""
    values = dict(inputs.active)
    requests = {"base": {}, **{k: {k: v} for k, v in values.items()}}
    for k, (a, b) in inputs.candidates.items():
        requests[f"{k}:a"], requests[f"{k}:b"] = {k: a}, {k: b}
    runs = lines.run(requests)
    base = runs.pop("base")
    x = None
    if sets_solving:
        b0, b1 = base.price(0.0), base.price(1.0)
        x = (target - b0) / (b1 - b0)
    for k, (a, b) in inputs.candidates.items():
        ra, rb = runs.pop(f"{k}:a"), runs.pop(f"{k}:b")
        keep_a = inputs.sign * ra.price(x) >= inputs.sign * rb.price(x)
        values[k], runs[k] = (a, ra) if keep_a else (b, rb)
    return values, {"base": base, **runs}


def desk_price(product, ctx, fmt, decision, profile, inputs: CostInputs, target: float | None, coupon_param: str | None, coupon_scale: float,
               fixings=None, workers=None, fast=False) -> tuple[DeskPrice, _Run, float | None, float | None]:
    """The lines of one product. With a `target`, the coupon is solved so that the client price is the target. Returns (the desk price,
    the base run, the solved coupon parameter, its standard error)."""
    t0 = time.perf_counter()
    solving = target is not None
    sets = [{coupon_param: 0.0}, {coupon_param: 1.0}] if solving else [{}]
    lines = _Lines(product, ctx, fmt, decision, profile, sets, fixings, workers, fast)
    values, runs = _settled(inputs, lines, {}, target, solving)
    base = runs.pop("base")
    single = runs
    everything = lines.run({"all": values})["all"] if len(values) > 1 else None
    moved = everything if everything is not None else next(iter(single.values()), None)
    models = {name: lines.model_run(name, inputs.mix) for name in decision.provisions}

    def effect(x):
        return 0.0 if moved is None else moved.price(x) - base.price(x)

    def in_force(x):
        return {k: r for k, r in models.items() if inputs.sign * (r.price(x) - base.price(x)) > 0}

    def provided(x):
        return sum((r.price(x) - base.price(x) for r in in_force(x).values()), 0.0)

    x, passes = _solved(base, effect, models, inputs.sign, target) if solving else (None, 0)
    digital_run = digital_all = None
    digital_passes = 0
    if inputs.min_spread is not None:
        digital_run = _DigitalRun(inputs, lines, coupon_param, base)
        digital_run.settle_sides(x)
        widths = None
        for _ in range(3):
            found = digital_run.found(x)
            now = [d.width(inputs.min_spread, inputs.delta_max) for d in found]
            if widths is not None and np.allclose(now, widths, rtol=1e-2, atol=1e-6):
                break
            widths = now
            ramps = digital_run.ramps(found)
            if not ramps:
                break
            digital_passes += 1
            digital_all = lines.ramp_run([ramps])[0]
            if not solving:
                break
            moved_with_digitals = lambda y, r=digital_all: effect(y) + r.price(y) - base.price(y)  # noqa: E731
            x, passes = _solved(base, moved_with_digitals, models, inputs.sign, target)
    at_base = base.at(x)
    base_pairs = _pct_pairs(at_base)
    s = inputs.sign
    slope_rate = (base.price(1.0) - base.price(0.0)) * coupon_scale if solving else 0.0

    def line(key, label, kind, pairs, price_effect, **kw):
        bp = -1e4 * price_effect / slope_rate if solving else None
        return CostLine(key, label, kind, s * price_effect, _se(pairs), bp, **kw)

    out, total_single = [], 0.0
    final_extra = 0.0 if base_pairs is None else np.zeros_like(base_pairs)
    for name, run in models.items():
        r = run.at(x)
        pairs = _pct_pairs(r) - base_pairs
        gap = float(pairs.mean())
        on = inputs.sign * gap > 0
        if on:
            final_extra = final_extra + pairs
        mix = lines.ctx.slv_model(lines.horizon, inputs.mix).mix if name == "slv" else None
        note = "" if on else "the gap is in the desk's favour: no provision"
        c = line(name, f"Model: {name.upper()}" + (f" (mix {mix:g})" if mix is not None else ""), "model", pairs, gap if on else 0.0, note=note, gap=inputs.sign * gap,
                 used=mix, default=inputs.defaults.get("slv_mix"), overridden=inputs.overridden_mix)
        out.append(c)
    for key, label in BUMPS.items():
        if key not in single:
            continue
        r = single[key].at(x)
        pairs = None if base_pairs is None else _pct_pairs(r) - base_pairs
        d = float(pairs.mean()) if pairs is not None else r.price - at_base.price
        total_single += d
        default = inputs.defaults.get(key)
        used = values[key]
        note = ""
        if key in inputs.candidates:
            a, b = inputs.candidates[key]
            note = f"the groups disagree ({a:+.4f}, {b:+.4f}): the one against the desk on this product"
        out.append(line(key, label, "bump", pairs, d, used=used, default=default,
                        overridden=default is not None and key not in inputs.candidates and default != used, note=note))
    final_pairs = base_pairs
    digital_effect = 0.0
    digital_pairs = None if base_pairs is None else np.zeros_like(base_pairs)
    if digital_all is not None:
        r = digital_all.at(x)
        digital_effect = r.price - at_base.price
        digital_pairs = None if base_pairs is None else _pct_pairs(r) - base_pairs
        found = digital_run.found(x)
        kinds = digital_run.by_kind([d for d in found if digital_run._key(d) in digital_run.sides])
        runs = lines.ramp_run([digital_run.ramps(ds) for ds in kinds.values()])
        total_kinds = 0.0
        for (kind, ds), run in zip(kinds.items(), runs):
            rk = run.at(x)
            pairs = None if base_pairs is None else _pct_pairs(rk) - base_pairs
            d = float(pairs.mean()) if pairs is not None else rk.price - at_base.price
            total_kinds += d
            detail = [{"date": g.date, "level": g.level, "jump": g.jump, "width": g.width(inputs.min_spread, inputs.delta_max),
                       "side": digital_run.sides[digital_run._key(g)], "alone": digital_run.alone[digital_run._key(g)], "shift": g.kind == "daily"} for g in ds]
            widths_seen = sorted({round(c["width"], 6) for c in detail})
            what = "moved by" if kind == "daily" else "width"
            note = f"{len(ds)} date{'s' if len(ds) > 1 else ''}, {what} " + (f"{100 * widths_seen[0]:.2f} pt" if len(widths_seen) == 1 else f"{100 * widths_seen[0]:.2f} to {100 * widths_seen[-1]:.2f} pt")
            out.append(line(kind, KINDS[kind], "digital", pairs, d, note=note, detail=detail))
        if len(kinds) > 1:
            singles = None if base_pairs is None else sum(_pct_pairs(run.at(x)) - base_pairs for run in runs)
            out.append(line("digital_interaction", "Interaction of the digitals", "interaction", None if base_pairs is None else digital_pairs - singles, digital_effect - total_kinds))
    if moved is not None:
        r = moved.at(x)
        final_pairs = _pct_pairs(r)
        if everything is not None:
            pairs = None if base_pairs is None else final_pairs - base_pairs
            d = (float(pairs.mean()) if pairs is not None else r.price - at_base.price) - total_single
            singles = None if pairs is None else sum(_pct_pairs(single[k].at(x)) - base_pairs for k in single)
            out.append(line("interaction", "Interaction of the bumps", "interaction", None if pairs is None else pairs - singles, d))
    final_price = at_base.price + effect(x) + provided(x) + digital_effect
    final_pairs = None if final_pairs is None else final_pairs + final_extra + digital_pairs
    final_se = _se(final_pairs)
    warnings = _arbitrage_warnings(ctx, values, lines.horizon, lines.names) if "vol" in values or "skew" in values else []
    rate_se = None
    if solving:
        on = in_force(x)
        slope_x = (base.price(1.0) + effect(1.0) + sum(r.price(1.0) - base.price(1.0) for r in on.values())) - (
            base.price(0.0) + effect(0.0) + sum(r.price(0.0) - base.price(0.0) for r in on.values()))
        if digital_all is not None:
            slope_x += (digital_all.price(1.0) - base.price(1.0)) - (digital_all.price(0.0) - base.price(0.0))
        rate_se = final_se / abs(slope_x)
    desk = DeskPrice(inputs.side, at_base.price, at_base.stderr, final_price, final_se, out,
                     (target - base.price(0.0)) / (base.price(1.0) - base.price(0.0)) / coupon_scale if solving else None,
                     x / coupon_scale if solving else None, time.perf_counter() - t0, warnings, passes, {k: r.at(x) for k, r in models.items()}, digital_passes)
    return desk, base, x, rate_se


def _arbitrage_warnings(ctx, values: dict, horizon: float, names) -> list:
    """A bump must not add arbitrage: what the unbumped surface already holds is not the bump's doing."""
    out = []
    surfaces = {n: ctx.vols[n] for n in names} if isinstance(ctx, BasketContext) else {names[0]: ctx.surface}
    for name, surface in surfaces.items():
        if np.isscalar(surface):
            if values.get("skew"):
                out.append(f"{name} has a flat volatility: the skew bump does not reach it")
            continue
        bumped = BumpedSurface(surface, values.get("vol", 0.0), values.get("skew", 0.0))
        got, base = surface_arbitrage(bumped, horizon), surface_arbitrage(surface, horizon)
        if not got["butterfly_free"] and got["min_g"] < min(base["min_g"], 0.0) - 1e-6:
            out.append(f"{name}: the bumped surface has butterfly arbitrage (density g down to {got['min_g']:.3f}): reduce the vol or skew bump")
        if not got["calendar_free"] and got["min_wT"] < min(base["min_wT"], 0.0) - 1e-6:
            out.append(f"{name}: the bumped surface has calendar arbitrage (dw/dT down to {got['min_wT']:.4f}): reduce the vol or skew bump")
    return out
