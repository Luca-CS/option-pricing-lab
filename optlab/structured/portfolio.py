"""Book of structured products, priced on a single shared simulation (one underlying, or a basket context).

All positions are evaluated on the same paths (the union of their simulation dates), so the book value per path
is the sum of the position values and its standard error accounts for the correlation between positions: a
hedged book has a much smaller error than its legs. Book Greeks bump the shared simulation.

Model: the pricing policy applied to the book. If every position is European-class, each is replicated on the
smile (no simulation); otherwise the whole book goes to the most demanding class's model (SLV), so that the
positions and their hedges are priced consistently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..config import DEFAULT_SEED
from ..models import BlackScholesModel, LocalVolModel
from ..products import MCResult
from .cashflows import Note, Swap, pair_averages
from .compiler import compile_sheet
from .context import PricingContext
from .greeks import ShiftedSurface, _recalibrated_slv
from .policy import RULES, decide
from .multiasset import BasketContext, basket_scenarios
from .pricer import basket_history
from .profile import ProductProfile, profile
from .replication import replication_result
from .scenario import simulated_scenarios
from .schema import TermSheet
from .script.evaluator import Evaluator
from .script.parser import parse
from .script.timeline import Clock, build_timeline
from .script.validate import validate

CLASS_ORDER = ("european", "path_dependent", "callable")


@dataclass
class Position:
    """`quantity` units of a product (a term sheet, a script or a Quote), signed: positive when the book holds
    the investor side. One unit pays the script's flows on its notional N."""
    name: str
    product: object
    quantity: float = 1.0
    params: dict = field(default_factory=dict)
    fixings: dict | None = None
    fmt: str | None = None  # note or swap; a term sheet carries its own

    def compiled(self, valuation):
        product = self.product
        if hasattr(product, "script") and hasattr(product, "product"):  # a Quote: its solved script
            sheet = product.product.sheet
            program = validate(parse(product.script))
        elif isinstance(product, TermSheet):
            sheet = product
            program = compile_sheet(product, valuation).program
        else:
            sheet = None
            program = validate(parse(product) if isinstance(product, str) else product)
        if sheet is not None and self.fmt is None:
            from .quote import wrapper
            return program, wrapper(sheet, compile_sheet(sheet, valuation))
        return program, (Swap() if self.fmt == "swap" else Note())


@dataclass
class PositionResult:
    name: str
    quantity: float
    unit_price: float  # % of the unit notional
    unit_stderr: float
    value: float  # currency: quantity x present value of one unit
    stderr: float
    product_class: str


@dataclass
class BookResult:
    positions: list
    value: float
    stderr: float
    method: str
    model: str
    reason: str
    n_paths: int
    seconds: float
    warnings: list = field(default_factory=list)
    pairs: np.ndarray | None = field(default=None, repr=False)
    position_pairs: list | None = field(default=None, repr=False)

    def table(self) -> str:
        lines = [f"book: {len(self.positions)} positions, {self.method} ({self.model}), {self.reason}",
                 f"{'position':28s} {'class':15s} {'qty':>9s} {'unit price %':>18s} {'value':>14s} {'+/- SE':>9s}"]
        for p in self.positions:
            lines.append(f"{p.name:28s} {p.product_class:15s} {p.quantity:9.2f} {p.unit_price:9.4f} +/- "
                         f"{p.unit_stderr:6.4f} {p.value:14.4f} {p.stderr:9.4f}")
        legs_se = float(np.sqrt(sum(p.stderr**2 for p in self.positions)))
        lines.append(f"{'total':28s} {'':15s} {'':9s} {'':18s} {self.value:14.4f} {self.stderr:9.4f}"
                     f"   (SE if the legs were independent: {legs_se:.4f})")
        lines.append(f"time {self.seconds:.2f} s" + "".join(f"\nwarning: {w}" for w in self.warnings))
        return "\n".join(lines)


class Book:
    def __init__(self, positions: list[Position]):
        if not positions:
            raise ValueError("the book is empty")
        names = [p.name for p in positions]
        if len(set(names)) != len(names):
            raise ValueError("position names must be unique")
        self.positions = positions

    def _prepare(self, ctx):
        items = []
        for pos in self.positions:
            program, fmt = pos.compiled(ctx.valuation)
            clock = Clock(ctx.valuation)
            timeline = build_timeline(program, clock)
            names = program.underlying_names
            items.append({"pos": pos, "program": program, "fmt": fmt, "timeline": timeline, "underlyings": names,
                          "profile": profile(program, timeline),
                          "history": basket_history(pos.fixings, names, clock)})
        und = sorted({n for it in items for n in it["underlyings"]})
        if len(und) > 1 and not isinstance(ctx, BasketContext):
            raise ValueError(f"the book observes {', '.join(und)}: price it with a BasketContext")
        return items

    def decision(self, ctx, items, model=None):
        worst = max((it["profile"] for it in items), key=lambda p: CLASS_ORDER.index(p.product_class))
        names = tuple(sorted({n for it in items for n in it["underlyings"]}))
        prof = ProductProfile(worst.product_class, worst.observation_times, worst.has_windows,
                              worst.early_termination, max(it["profile"].horizon for it in items), names)
        return decide(prof, ctx.t_max, model, control=None, has_surfaces=getattr(ctx, "has_surfaces", True))

    def price(self, ctx: PricingContext | None = None, model: str | None = None, n_paths: int = 200_000,
              seed: int = DEFAULT_SEED, batch: int | None = 25_000, smoothing: float = 0.0, market=None, surface=None,
              initial_fixing: float | dict | None = None, sim_model=None) -> BookResult:
        """Value of the book. `model` forces one model for every position ('bs', 'lv', 'heston', 'slv', or
        'surface' for an all-European book); `market`/`surface` override the context (bumps)."""
        t0 = time.perf_counter()
        ctx = ctx or PricingContext.from_calibration()
        basket = isinstance(ctx, BasketContext)
        if basket and (market is not None or surface is not None):
            raise ValueError("bump a basket book through BasketContext.bumped")
        market = market or ctx.market
        surface = surface or getattr(ctx, "surface", None)
        items = self._prepare(ctx)
        d = self.decision(ctx, items, model)
        rule = "basket" if len(d_names := {n for it in items for n in it["underlyings"]}) > 1 else d.product_class
        reason = RULES[rule].reason.split(":")[0] if not d.forced else d.reason
        warnings = list(d.warnings)
        if d.method == "replication":
            results, values = [], []
            for it in items:
                tl = it["timeline"]
                T_obs = it["profile"].observation_times[-1] if it["profile"].observation_times else tl.horizon
                r = replication_result(it["program"], tl, surface, market, T_obs, it["fmt"], it["pos"].params,
                                       initial_fixing=initial_fixing,
                                       history=it["history"].get(it["underlyings"][0], {}))
                v = it["pos"].quantity * r.value.price
                values.append(v)
                results.append(PositionResult(it["pos"].name, it["pos"].quantity, r.price, 0.0, v, 0.0,
                                              it["profile"].product_class))
            return BookResult(results, float(sum(values)), 0.0, "replication", "surface", reason, 0,
                              time.perf_counter() - t0, warnings)
        horizon = max(it["profile"].horizon for it in items)
        times = np.unique(np.concatenate([it["timeline"].sim_times for it in items]))
        history: dict = {}
        for it in items:
            for name, h in it["history"].items():
                merged = history.setdefault(name, {})
                for t, v in h.items():
                    if t in merged and merged[t] != v:
                        raise ValueError(f"two positions give different fixings of {name} at t = {t:.6f}")
                    merged[t] = v
        if basket:
            names = tuple(n for n in ctx.names if n in d_names)
            scenarios = basket_scenarios(ctx.model(d.model, horizon, names), times, n_paths, seed, batch, history,
                                         initial_fixing)
        else:
            (name,) = d_names
            sim = sim_model or self._model(d.model, ctx, surface, horizon)
            scenarios = simulated_scenarios(sim, market, name, times, n_paths, seed, batch, initial_fixing,
                                            history.get(name, {}))
        evaluators = [Evaluator(it["program"], it["timeline"], it["pos"].params, smoothing) for it in items]
        pos_pairs = [[] for _ in items]
        for scenario in scenarios:
            for i, (it, ev) in enumerate(zip(items, evaluators)):
                N = float(ev.params.get("N", 1.0))
                pv = it["fmt"].present_values(ev.run(scenario), market, N)
                pos_pairs[i].append(it["pos"].quantity * pair_averages(pv))
        pos_pairs = [np.concatenate(p) for p in pos_pairs]
        book = np.sum(pos_pairs, axis=0)
        results = []
        for it, ev, p in zip(items, evaluators, pos_pairs):
            q, N = it["pos"].quantity, float(ev.params.get("N", 1.0))
            v, se = float(p.mean()), float(p.std(ddof=1) / np.sqrt(len(p)))
            unit = 100 * v / (q * N) if q else 0.0
            unit_se = 100 * se / abs(q * N) if q else 0.0
            results.append(PositionResult(it["pos"].name, q, unit, unit_se, v, se, it["profile"].product_class))
        if smoothing:
            warnings.append(f"digital smoothing {smoothing:.2%}: prices are slightly biased")
        res = MCResult(float(book.mean()), float(book.std(ddof=1) / np.sqrt(len(book))), n_paths)
        return BookResult(results, res.price, res.stderr, "monte_carlo", d.model, reason, n_paths,
                          time.perf_counter() - t0, warnings, book, pos_pairs)

    @staticmethod
    def _model(name, ctx, surface, horizon):
        if name == "bs":
            return BlackScholesModel(float(surface.iv(0.0, max(horizon, 1 / 365))))
        if name == "lv":
            return LocalVolModel(surface)
        return ctx.model(name, horizon)

    def greeks(self, ctx: PricingContext | None = None, model: str | None = None, n_paths: int = 200_000,
               seed: int = DEFAULT_SEED, batch: int | None = 25_000, smoothing: float = 0.005, h_spot: float = 0.01,
               h_vol: float = 0.01, slv_vega: bool = False, n_particles: int = 200_000) -> dict:
        """Book delta (currency per +1 % spot) and vega (per +1 vol point) by central bumps of the shared
        simulation, per position and in total, with standard errors. Products keep their initial fixing."""
        ctx = ctx or PricingContext.from_calibration()
        if isinstance(ctx, BasketContext):
            return self._basket_greeks(ctx, model, n_paths, seed, batch, smoothing, h_spot, h_vol)
        items = self._prepare(ctx)
        d = self.decision(ctx, items, model)
        eps = 0.0 if d.method == "replication" else smoothing
        S0 = ctx.market.spot
        kw = dict(ctx=ctx, model=model, n_paths=n_paths, seed=seed, batch=batch, smoothing=eps, initial_fixing=S0)
        out = {"method": d.method, "model": d.model, "smoothing": eps}

        bump = _bump
        up = self.price(market=ctx.market.scaled(S0 * (1 + h_spot)), **kw)
        down = self.price(market=ctx.market.scaled(S0 * (1 - h_spot)), **kw)
        out["delta"] = dict(zip(("positions", "positions_se", "total", "total_se"),
                                bump(up, down, 0.01 / (2 * h_spot))))
        if d.model == "heston" or (d.model == "slv" and not slv_vega):
            out["vega"] = None
            out["note"] = f"no vega under {d.model.upper()}: a vol bump needs a recalibration of the model"
        else:
            su, sd = ShiftedSurface(ctx.surface, h_vol), ShiftedSurface(ctx.surface, -h_vol)
            mu = md = None
            if d.model == "slv":
                horizon = max(it["profile"].horizon for it in items)
                mu = _recalibrated_slv(ctx, su, horizon, n_particles, seed)
                md = _recalibrated_slv(ctx, sd, horizon, n_particles, seed)
            vu = self.price(surface=su, sim_model=mu, **kw)
            vd = self.price(surface=sd, sim_model=md, **kw)
            out["vega"] = dict(zip(("positions", "positions_se", "total", "total_se"),
                                   bump(vu, vd, 0.01 / (2 * h_vol))))
        out["names"] = [p.name for p in self.positions]
        return out

    def _basket_greeks(self, ctx, model, n_paths, seed, batch, smoothing, h_spot, h_vol) -> dict:
        """Delta and vega per underlying name (each name bumped alone, the others fixed), on common paths."""
        items = self._prepare(ctx)
        d = self.decision(ctx, items, model)
        names = [n for n in ctx.names if any(n in it["underlyings"] for it in items)]
        fixing = {n: ctx.markets[n].spot for n in names}
        kw = dict(model=model, n_paths=n_paths, seed=seed, batch=batch, smoothing=smoothing, initial_fixing=fixing)
        out = {"method": d.method, "model": d.model, "smoothing": smoothing, "names": [p.name for p in self.positions],
               "delta": {}, "vega": {}}
        for n in names:
            up = self.price(ctx.bumped(n, spot_factor=1 + h_spot), **kw)
            down = self.price(ctx.bumped(n, spot_factor=1 - h_spot), **kw)
            out["delta"][n] = dict(zip(("positions", "positions_se", "total", "total_se"),
                                       _bump(up, down, 0.01 / (2 * h_spot))))
            vu = self.price(ctx.bumped(n, vol_shift=h_vol), **kw)
            vd = self.price(ctx.bumped(n, vol_shift=-h_vol), **kw)
            out["vega"][n] = dict(zip(("positions", "positions_se", "total", "total_se"),
                                      _bump(vu, vd, 0.01 / (2 * h_vol))))
        return out


def _bump(a: BookResult, b: BookResult, scale: float):
    """Per-position and total differences of two book prices on the same paths, with standard errors."""
    if a.pairs is None:
        per = [scale * (x.value - y.value) for x, y in zip(a.positions, b.positions)]
        return per, [0.0] * len(per), scale * (a.value - b.value), 0.0
    per = [scale * (x - y) for x, y in zip(a.position_pairs, b.position_pairs)]
    tot = scale * (a.pairs - b.pairs)
    se = [float(p.std(ddof=1) / np.sqrt(len(p))) for p in per]
    return [float(p.mean()) for p in per], se, float(tot.mean()), float(tot.std(ddof=1) / np.sqrt(len(tot)))
