"""Book of structured products on one underlying, priced on a single shared simulation.

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

from ..models import BlackScholesModel, LocalVolModel
from ..products import MCResult
from .cashflows import Note, Swap, pair_averages
from .compiler import compile_sheet
from .context import PricingContext
from .greeks import ShiftedSurface
from .policy import RULES, decide
from .pricer import history_times
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
        fmt = self.fmt or (sheet.get("wrapper.format") if sheet is not None else "note")
        spread = sheet.get("wrapper.issuer_spread", 0.0) if sheet is not None else 0.0
        return program, (Note(spread or 0.0) if fmt == "note" else Swap())


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

    def _prepare(self, ctx: PricingContext):
        items = []
        for pos in self.positions:
            program, fmt = pos.compiled(ctx.valuation)
            clock = Clock(ctx.valuation)
            timeline = build_timeline(program, clock)
            name = program.underlying_names[0]
            items.append({"pos": pos, "program": program, "fmt": fmt, "timeline": timeline, "underlying": name,
                          "profile": profile(program, timeline),
                          "history": history_times(pos.fixings, name, clock)})
        und = {it["underlying"] for it in items}
        if len(und) > 1:
            raise ValueError(f"a book on one underlying only for now, found {', '.join(sorted(und))}")
        return items

    def decision(self, ctx: PricingContext, items, model=None):
        worst = max((it["profile"] for it in items), key=lambda p: CLASS_ORDER.index(p.product_class))
        prof = ProductProfile(worst.product_class, worst.observation_times, worst.has_windows,
                              worst.early_termination, max(it["profile"].horizon for it in items))
        return decide(prof, ctx.t_max, model, control=None)

    def price(self, ctx: PricingContext | None = None, model: str | None = None, n_paths: int = 200_000,
              seed: int = 2026, batch: int | None = 25_000, smoothing: float = 0.0, market=None, surface=None,
              initial_fixing: float | None = None) -> BookResult:
        """Value of the book. `model` forces one model for every position ('bs', 'lv', 'heston', 'slv', or
        'surface' for an all-European book); `market`/`surface` override the context (bumps)."""
        t0 = time.perf_counter()
        ctx = ctx or PricingContext.from_calibration()
        market = market or ctx.market
        surface = surface or ctx.surface
        items = self._prepare(ctx)
        d = self.decision(ctx, items, model)
        reason = RULES[d.product_class].reason.split(":")[0] if not d.forced else d.reason
        warnings = list(d.warnings)
        if d.method == "replication":
            results, values = [], []
            for it in items:
                tl = it["timeline"]
                T_obs = it["profile"].observation_times[-1] if it["profile"].observation_times else tl.horizon
                r = replication_result(it["program"], tl, surface, market, T_obs, it["fmt"], it["pos"].params,
                                       initial_fixing=initial_fixing, history=it["history"])
                v = it["pos"].quantity * r.value.price
                values.append(v)
                results.append(PositionResult(it["pos"].name, it["pos"].quantity, r.price, 0.0, v, 0.0,
                                              it["profile"].product_class))
            return BookResult(results, float(sum(values)), 0.0, "replication", "surface", reason, 0,
                              time.perf_counter() - t0, warnings)
        sim_model = self._model(d.model, ctx, surface, max(it["profile"].horizon for it in items))
        times = np.unique(np.concatenate([it["timeline"].sim_times for it in items]))
        history = {}
        for it in items:
            for t, v in it["history"].items():
                if t in history and history[t] != v:
                    raise ValueError(f"two positions give different fixings at t = {t:.6f}")
                history[t] = v
        evaluators = [Evaluator(it["program"], it["timeline"], it["pos"].params, smoothing) for it in items]
        pos_pairs = [[] for _ in items]
        for scenario in simulated_scenarios(sim_model, market, items[0]["underlying"], times, n_paths, seed, batch,
                                            initial_fixing, history):
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
               seed: int = 2026, batch: int | None = 25_000, smoothing: float = 0.005, h_spot: float = 0.01,
               h_vol: float = 0.01) -> dict:
        """Book delta (currency per +1 % spot) and vega (per +1 vol point) by central bumps of the shared
        simulation, per position and in total, with standard errors. Products keep their initial fixing."""
        ctx = ctx or PricingContext.from_calibration()
        items = self._prepare(ctx)
        d = self.decision(ctx, items, model)
        eps = 0.0 if d.method == "replication" else smoothing
        S0 = ctx.market.spot
        kw = dict(ctx=ctx, model=model, n_paths=n_paths, seed=seed, batch=batch, smoothing=eps, initial_fixing=S0)
        out = {"method": d.method, "model": d.model, "smoothing": eps}

        def bump(a, b, scale):
            if a.pairs is None:
                per = [scale * (x.value - y.value) for x, y in zip(a.positions, b.positions)]
                return per, [0.0] * len(per), scale * (a.value - b.value), 0.0
            per = [scale * (x - y) for x, y in zip(a.position_pairs, b.position_pairs)]
            tot = scale * (a.pairs - b.pairs)
            se = [float(p.std(ddof=1) / np.sqrt(len(p))) for p in per]
            return [float(p.mean()) for p in per], se, float(tot.mean()), float(tot.std(ddof=1) / np.sqrt(len(tot)))

        up = self.price(market=ctx.market.scaled(S0 * (1 + h_spot)), **kw)
        down = self.price(market=ctx.market.scaled(S0 * (1 - h_spot)), **kw)
        out["delta"] = dict(zip(("positions", "positions_se", "total", "total_se"),
                                bump(up, down, 0.01 / (2 * h_spot))))
        if d.model in ("slv", "heston"):
            out["vega"] = None
            out["note"] = f"no vega under {d.model.upper()}: a vol bump needs a recalibration of the model"
        else:
            vu = self.price(surface=ShiftedSurface(ctx.surface, h_vol), **kw)
            vd = self.price(surface=ShiftedSurface(ctx.surface, -h_vol), **kw)
            out["vega"] = dict(zip(("positions", "positions_se", "total", "total_se"),
                                   bump(vu, vd, 0.01 / (2 * h_vol))))
        out["names"] = [p.name for p in self.positions]
        return out
