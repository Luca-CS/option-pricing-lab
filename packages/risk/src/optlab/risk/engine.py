"""The revaluation engine: a list of shocks, one full revaluation of the book per shock, on the base's random numbers.

The book comes as groups, each on its own context and seed (one per single name, the worst-ofs together), as the desk values
it. Within a group every shock uses the group's seed and number of paths, so a difference of two revaluations is paired path
by path (pair averages of antithetic pairs, the same as `structured.portfolio.Book`), and its standard error is that of the
paired differences. Groups are independent: values add and errors add in quadrature.

Shocks that differ only by spots, forwards (rates, dividends, repo, quanto drift), discount curves or FX share one
simulation: under sticky moneyness the model is simulated in x = ln(S / F), so a spot move multiplies the paths and a forward
move multiplies them by F'(t) / F(t) date by date, exactly; a discount move only changes the present values. Volatility,
correlation and the roll need a simulation of their own, still on the same seed.

`Measure`s are linear combinations of shocks (a central delta is (up - down) / 2), evaluated on the paired paths. Their pairs
are kept only until every shock they read is valued.

A product already struck keeps its fixings; a product struck today keeps today's spot as its initial fixing. The roll moves
the valuation date by business days with the market frozen in remaining maturity, adds the frozen spot as the fixing of every
day it crosses, and counts what is paid on those days as cash: value + cash is what the desk holds after the roll.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from optlab.core.config import DEFAULT_SEED
from optlab.risk.curves import bumped_market, forward_ratio
from optlab.risk.shocks import (
    BASE,
    RiskEnv,
    Shock,
    apply,
    context_currency,
    discount_move,
    fx_factor,
    name_moves,
    shocked_market,
    sim_key,
    simulation_part,
)
from optlab.structured import engine as batch_engine
from optlab.structured.cashflows import FundedSwap, Swap
from optlab.structured.dates import year_fraction
from optlab.structured.engine import Evaluation
from optlab.structured.multiasset import BasketContext
from optlab.structured.portfolio import Book
from optlab.structured.pricer import check_fixing_scale
from optlab.structured.replication import replication_result


@dataclass
class RiskGroup:
    """Positions valued together on one context and one seed."""

    label: str
    book: Book
    ctx: object  # PricingContext or BasketContext
    seed: int = DEFAULT_SEED


@dataclass
class RiskSettings:
    n_paths: int = 100_000
    batch: int = 25_000
    model: str | None = (
        None  # None: the pricing policy (local vol, replication for a European book); "slv" as an option
    )
    smoothing: float = 0.0  # digitals as ramps of this relative width (Greeks); 0 for values
    engine: str | None = None  # numpy, cpu, gpu, auto (structured.engine)
    workers: int | None = None
    slv_particles: int = 100_000
    eval_threads: int | None = (
        None  # threads evaluating the engine's batches (None: the engine's choice; 1 in VaR processes)
    )
    engine_threads: int = 0  # CPU engine threads (0: every hardware thread)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Measure:
    """scale * sum_s weight_s V(s): a Greek, a P&L or any combination of revaluations ("base" is the base)."""

    name: str
    weights: dict
    scale: float = 1.0


class RiskFormat:
    """A product format whose present values use another market's discounts (a discount bump) and, after a roll, add what is
    paid in (since, 0] as cash, undiscounted."""

    def __init__(self, fmt, market=None, since: float | None = None):
        self.fmt, self.market, self.since = fmt, market, since
        self.name = fmt.name

    def __getattr__(self, item):
        return getattr(self.__dict__["fmt"], item)

    def present_values(self, flows, market, notional):
        m = self.market or market
        pv = self.fmt.present_values(flows, m, notional)
        if self.since is None:
            return pv
        lo = self.since
        cash = 0.0
        for (t, _tag), f in flows.flows.items():
            if lo < t <= 0:
                cash = cash + f
        if isinstance(self.fmt, (Swap, FundedSwap)):
            for t, w in flows.redemption_weights.items():
                if lo < t <= 0:
                    cash = cash - notional * w
        if isinstance(self.fmt, FundedSwap):
            window = np.array([lo < p <= 0 for _, _, p, _ in self.fmt.periods])
            if window.any():
                amounts = self.fmt.funding_amounts(m, notional) * window
                cash = cash - self.fmt.survival(flows) @ amounts
        return pv + np.broadcast_to(cash, (flows.n_paths,))


def _stat(x: np.ndarray) -> tuple[float, float]:
    return float(x.mean()), float(x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else 0.0


def rolled_book(book: Book, items, old: dt.date, new: dt.date, spot0) -> Book:
    """The positions with the frozen spot as the fixing of every weekday in [old, new] that they do not already have."""
    days = [old + dt.timedelta(days=i) for i in range((new - old).days + 1)]
    days = [d.isoformat() for d in days if d.weekday() < 5]
    out = []
    for pos, it in zip(book.positions, items):
        fix = {n: dict((pos.fixings or {}).get(n, {})) for n in it["underlyings"]}
        for n in it["underlyings"]:
            level = spot0[n] if isinstance(spot0, dict) else spot0
            for d in days:
                fix[n].setdefault(d, float(level))
        out.append(dataclasses.replace(pos, fixings=fix))
    return Book(out)


def _merged_history(items, basket: bool, ctx):
    history: dict = {}
    for it in items:
        for name, h in it["history"].items():
            merged = history.setdefault(name, {})
            for t, v in h.items():
                if t in merged and merged[t] != v:
                    raise ValueError(f"two positions give different fixings of {name} at t = {t:.6f}")
                merged[t] = v
    for name, h in history.items():
        check_fixing_scale(h, (ctx.markets[name] if basket else ctx.market).spot, name)
    return history


@dataclass
class GroupRun:
    """Raw output of one group: per shock and position the pair values (None under replication) or values."""

    label: str
    method: str
    model: str
    currency: str
    names: list
    quantities: list
    notionals: list
    n_paths: int
    seed: int
    values: dict = field(default_factory=dict)  # shock -> [value per position]
    pnl: dict = field(default_factory=dict)  # shock -> ([mean per position], [se per position], total, total_se)
    measures: dict = field(default_factory=dict)  # measure -> same tuple
    base_se: list = field(default_factory=list)
    base_total_se: float = 0.0
    simulations: int = 0
    seconds: float = 0.0
    timings: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    smoothing_bias: tuple | None = None  # (per position, se, total, total se): smoothed - exact base value
    pairs: list | None = None  # with keep_pairs: the base's values per pair of paths, per position
    cash: dict = field(default_factory=dict)  # base and rolled shocks: per position {payment time: expected cash}

    def to_dict(self) -> dict:
        def block(t):
            return {"positions": t[0], "positions_se": t[1], "total": t[2], "total_se": t[3]}

        return {
            "label": self.label,
            "method": self.method,
            "model": self.model,
            "currency": self.currency,
            "names": self.names,
            "quantities": self.quantities,
            "notionals": self.notionals,
            "n_paths": self.n_paths,
            "seed": self.seed,
            "base": {
                "positions": self.values["base"],
                "positions_se": self.base_se,
                "total": float(sum(self.values["base"])),
                "total_se": self.base_total_se,
            },
            "values": self.values,
            "pnl": {k: block(v) for k, v in self.pnl.items()},
            "measures": {k: block(v) for k, v in self.measures.items()},
            "simulations": self.simulations,
            "seconds": self.seconds,
            "warnings": self.warnings,
            "cash": {k: [{str(t): a for t, a in c.items()} for c in v] for k, v in self.cash.items()},
            "smoothing_bias": block(self.smoothing_bias) if self.smoothing_bias else None,
        }


def _expected_cash(fmt, flows: dict, redemption: dict, notional: float, n: float, scale: float) -> dict:
    """{payment time: expected undiscounted cash} of one position, from summed (or expected, n = 1) flows."""
    out: dict = {}
    for (t, _tag), f in flows.items():
        out[round(float(t), 10)] = out.get(round(float(t), 10), 0.0) + scale * f / n
    if isinstance(fmt, (Swap, FundedSwap)):
        for t, w in redemption.items():
            out[round(float(t), 10)] = out.get(round(float(t), 10), 0.0) - scale * notional * w / n
    return out


def _sim_model(d_model: str, ctx, sim_ctx, horizon: float, settings: RiskSettings, seed: int, cache: dict, names):
    from optlab.core.models import BlackScholesModel, LocalVolModel

    if isinstance(sim_ctx, BasketContext):
        return sim_ctx.model(d_model, horizon, tuple(names))
    if d_model == "bs":
        return BlackScholesModel(float(sim_ctx.surface.iv(0.0, max(horizon, 1 / 365))))
    if d_model == "lv":
        return LocalVolModel(sim_ctx.surface)
    if d_model == "slv":
        if sim_ctx.surface is ctx.surface:
            return ctx.model("slv", horizon)
        key = id(sim_ctx.surface)
        if key not in cache:
            from optlab.structured.greeks import _recalibrated_slv

            cache[key] = (
                sim_ctx.surface,
                _recalibrated_slv(ctx, sim_ctx.surface, horizon, settings.slv_particles, seed),
            )
        return cache[key][1]
    return sim_ctx.model(d_model, horizon)


def _basket_simulation(ctx: BasketContext, sim_ctx: BasketContext, d_model: str) -> BasketContext:
    """Under SLV, a name whose surface moved needs its own leverage: its context is copied and recalibrates on first use."""
    if d_model != "slv":
        return sim_ctx
    contexts = dict(ctx.contexts)
    for n in ctx.names:
        if sim_ctx.vols[n] is not ctx.vols[n] and not np.isscalar(sim_ctx.vols[n]):
            contexts[n] = dataclasses.replace(
                ctx.name_context(n), surface=sim_ctx.vols[n], slv_cache={}, leverage_flat=True
            )
    return dataclasses.replace(sim_ctx, contexts=contexts)


def run_group(
    group: RiskGroup,
    shocks: dict,
    measures: list | None = None,
    settings: RiskSettings | None = None,
    env: RiskEnv | None = None,
    cash_since: float | None = None,
    keep_pairs: bool = False,
) -> GroupRun:
    """Every shock of `shocks` ({name: Shock}; "base" is added) on one group. `cash_since` (< 0, years) counts what is paid in
    (cash_since, 0] as cash in every value, as after a roll (the P&L cascade moves the valuation date itself); `keep_pairs`
    keeps the base's pair values per position in `GroupRun.pairs`."""
    t_start = time.perf_counter()
    settings = settings or RiskSettings()
    env = env or RiskEnv()
    measures = list(measures or [])
    ctx, book = group.ctx, group.book
    basket = isinstance(ctx, BasketContext)
    cur = context_currency(ctx)
    shocks = {"base": BASE, **{k: v for k, v in shocks.items() if k != "base"}}
    for m in measures:
        missing = set(m.weights) - set(shocks)
        if missing:
            raise KeyError(f"measure {m.name} reads unknown shock(s) {', '.join(sorted(missing))}")
    base_items = book._prepare(ctx)
    d = book.decision(ctx, base_items, settings.model)
    used = sorted({n for it in base_items for n in it["underlyings"]})
    names = [n for n in ctx.names if n in used] if basket else used
    spot0 = {n: float(ctx.markets[n].spot) for n in names} if basket else float(ctx.market.spot)
    notionals = [float({**it["program"].param_values, **it["pos"].params}.get("N", 1.0)) for it in base_items]
    quantities = [float(it["pos"].quantity) for it in base_items]
    run = GroupRun(
        group.label,
        d.method,
        d.model,
        cur,
        [it["pos"].name for it in base_items],
        quantities,
        notionals,
        settings.n_paths if d.method != "replication" else 0,
        group.seed,
        warnings=list(d.warnings),
    )
    buckets: OrderedDict = OrderedDict()
    for k, s in shocks.items():
        buckets.setdefault(sim_key(ctx, s, env), []).append(k)
    refs: dict = {}
    for m in measures:
        for s in m.weights:
            refs[s] = refs.get(s, 0) + 1
    kept: dict = {}  # shock -> per-position pairs (or values), while a measure needs them
    done_measures: set = set()
    slv_cache: dict = {}
    smooth = settings.smoothing if d.method != "replication" else 0.0
    base_pairs = None

    def finish(shock_name, per_position):
        nonlocal base_pairs
        run.values[shock_name] = [float(np.mean(p)) for p in per_position]
        if shock_name == "base":
            base_pairs = per_position
            run.base_se = [_stat(p)[1] if np.ndim(p) else 0.0 for p in per_position]
            run.base_total_se = _stat(np.sum(per_position, axis=0))[1] if np.ndim(per_position[0]) else 0.0
        diff = [np.asarray(p) - np.asarray(b) for p, b in zip(per_position, base_pairs)]
        stats = [_stat(x) if np.ndim(x) else (float(x), 0.0) for x in diff]
        tot = np.sum(diff, axis=0)
        ts = _stat(tot) if np.ndim(tot) else (float(tot), 0.0)
        run.pnl[shock_name] = ([s[0] for s in stats], [s[1] for s in stats], ts[0], ts[1])
        if refs.get(shock_name):
            kept[shock_name] = per_position
        for m in measures:
            if m.name in done_measures or not all(s in kept or s == "base" for s in m.weights):
                continue
            combo = [
                m.scale * sum(w * np.asarray((kept[s] if s != "base" else base_pairs)[i]) for s, w in m.weights.items())
                for i in range(len(per_position))
            ]
            st = [_stat(x) if np.ndim(x) else (float(x), 0.0) for x in combo]
            tot = np.sum(combo, axis=0)
            ts = _stat(tot) if np.ndim(tot) else (float(tot), 0.0)
            run.measures[m.name] = ([s[0] for s in st], [s[1] for s in st], ts[0], ts[1])
            done_measures.add(m.name)
            for s in m.weights:
                refs[s] -= 1
                if refs[s] <= 0 and s != "base":
                    kept.pop(s, None)

    ordered = sorted(buckets.items(), key=lambda kv: "base" not in kv[1])  # the base's simulation first
    for key, members in ordered:
        first = shocks[members[0]]
        sim_ctx = simulation_part(ctx, first, env)
        if basket:
            sim_ctx = _basket_simulation(ctx, sim_ctx, d.model)
        items, since = base_items, cash_since
        if first.roll_days:
            rolled = rolled_book(book, base_items, ctx.valuation, sim_ctx.valuation, spot0)
            items = rolled._prepare(sim_ctx)
            since = -year_fraction(ctx.valuation, sim_ctx.valuation)
        fx = {m: fx_factor(shocks[m], cur, env.currency) for m in members}
        if d.method == "replication":
            name = names[0]
            for m in members:
                full = apply(ctx, shocks[m], env)
                vals = []
                for it, q in zip(items, quantities):
                    tl = it["timeline"]
                    T_obs = it["profile"].observation_times[-1] if it["profile"].observation_times else tl.horizon
                    fmt = RiskFormat(it["fmt"], None, since) if since is not None else it["fmt"]
                    r = replication_result(
                        it["program"],
                        tl,
                        full.surface,
                        full.market,
                        T_obs,
                        fmt,
                        it["pos"].params,
                        initial_fixing=spot0,
                        history=it["history"].get(name, {}),
                    )
                    vals.append(q * r.value.price * fx[m])
                    if m == "base" or shocks[m].roll_days:
                        N = float(r.notional)
                        run.cash.setdefault(m, []).append(
                            _expected_cash(it["fmt"], r.expected_flows, r.redemption_probabilities, N, 1.0, q * fx[m])
                        )
                if m == "base" and keep_pairs:
                    run.pairs = list(vals)
                finish(m, vals)
            continue
        horizon = max(it["profile"].horizon for it in items)
        times = np.unique(np.concatenate([it["timeline"].sim_times for it in items]))
        base_markets = dict(ctx.markets) if basket else {names[0]: ctx.market}
        surfaces = ctx.vols if basket else {names[0]: ctx.surface}
        moved_markets = {
            m: {
                n: shocked_market(
                    base_markets[n], surfaces[n], name_moves(shocks[m], n, cur, env), None, with_spot=False
                )
                for n in names
            }
            for m in members
        }
        sim_markets = dict(base_markets)
        if len(members) == 1 and members[0] != "base":  # a shock alone on its simulation: simulate on its forwards (no
            sim_markets.update(moved_markets[members[0]])  # factor per date, so daily windows can still be reduced)
            if basket:
                sim_ctx = dataclasses.replace(sim_ctx, markets={**sim_ctx.markets, **sim_markets})
            else:
                sim_ctx = dataclasses.replace(sim_ctx, market=sim_markets[names[0]])
        model = _sim_model(d.model, ctx, sim_ctx, horizon, settings, group.seed, slv_cache, names)
        history = _merged_history(items, basket, ctx)
        hist = history if basket else history.get(names[0], {})
        market = sim_ctx.market
        evaluations, layout = [], []
        for m in members:
            s = shocks[m]
            disc = discount_move(s, cur)
            pv_market = bumped_market(market, disc) if disc else None
            factor = {}
            for n in names:
                moved, on = moved_markets[m][n], sim_markets[n]
                ratio = 1.0 if moved is on else forward_ratio(on, moved, times)
                factor[n] = (1.0 + name_moves(s, n, cur, env).spot) * ratio
            if not basket:
                factor = factor[names[0]]
            for it, N in zip(items, notionals):
                fmt = it["fmt"] if pv_market is None and since is None else RiskFormat(it["fmt"], pv_market, since)
                evaluations.append(
                    Evaluation(it["program"], it["timeline"], it["pos"].params, fmt, N, smooth, True, factor)
                )
            layout.append(m)
            if m == "base" and smooth:
                for it, N in zip(items, notionals):
                    evaluations.append(
                        Evaluation(it["program"], it["timeline"], it["pos"].params, it["fmt"], N, 0.0, True, 1.0)
                    )
                layout.append("base@exact")
        totals, timings = batch_engine.run(
            model,
            market,
            names[0],
            times,
            settings.n_paths,
            group.seed,
            settings.batch,
            spot0,
            hist,
            evaluations,
            settings.workers,
            engine=settings.engine,
            threads=settings.engine_threads,
            **({"eval_threads": settings.eval_threads} if settings.eval_threads else {}),
        )
        run.simulations += 1
        run.timings.append({k: v for k, v in timings.items() if isinstance(v, (int, float, str))})
        if timings.get("engine_note") and timings["engine_note"] not in run.warnings:
            run.warnings.append(timings["engine_note"])
        n_pos = len(items)
        for b, m in enumerate(layout):
            chunk = totals[b * n_pos : (b + 1) * n_pos]
            mult = fx.get(m, 1.0)
            per = [q * mult * np.concatenate(t.pairs) for q, t in zip(quantities, chunk)]
            if m == "base@exact":
                exact = per
                continue
            if m == "base" and keep_pairs:
                run.pairs = per
            if m == "base" or shocks[m].roll_days:
                run.cash[m] = [
                    _expected_cash(it["fmt"], t.flows, t.redemption, N, settings.n_paths, q * mult)
                    for it, t, N, q in zip(items, chunk, notionals, quantities)
                ]
            finish(m, per)
        if smooth and "base" in members:
            bias = [run.values["base"][i] - float(np.mean(exact[i])) for i in range(n_pos)]
            diffs = [base_pairs[i] - exact[i] for i in range(n_pos)]
            tot = np.sum(diffs, axis=0)
            run.smoothing_bias = (bias, [_stat(x)[1] for x in diffs], float(tot.mean()), _stat(tot)[1])
    unfinished = [m.name for m in measures if m.name not in done_measures]
    if unfinished:
        raise RuntimeError(f"measures not evaluated: {', '.join(unfinished)}")
    run.seconds = time.perf_counter() - t_start
    return run


def run_group_safe(
    group: RiskGroup,
    shocks: dict,
    measures: list | None = None,
    settings: RiskSettings | None = None,
    env: RiskEnv | None = None,
) -> GroupRun:
    """`run_group`, except that a replicated group whose shocked surface is in butterfly arbitrage (replication refuses it) is
    valued by local vol Monte Carlo instead, with a warning."""
    settings = settings or RiskSettings()
    try:
        return run_group(group, shocks, measures, settings, env)
    except ValueError as exc:
        if "arbitrage" not in str(exc) or settings.model is not None:
            raise
        run = run_group(group, shocks, measures, dataclasses.replace(settings, model="lv"), env)
        run.warnings.append(
            f"replication refused a shocked surface ({exc}): the group is valued by local vol Monte Carlo"
        )
        return run


@dataclass
class RiskRun:
    """Every group of a book under the same shocks and measures, and the book totals (groups independent)."""

    groups: list
    shocks: dict
    settings: RiskSettings
    seconds: float

    def total(self, kind: str, name: str) -> tuple[float, float]:
        """(value, se) of a P&L ("pnl"), a measure ("measures") or the base value ("base") over the book."""
        v = var = 0.0
        for g in self.groups:
            if kind == "base":
                v += float(sum(g.values["base"]))
                var += g.base_total_se**2
                continue
            got = getattr(g, kind).get(name)
            if got is None:
                continue
            v += got[2]
            var += got[3] ** 2
        return v, float(np.sqrt(var))

    def position(self, kind: str, name: str) -> dict:
        """{position: (value, se)} over every group."""
        out = {}
        for g in self.groups:
            got = getattr(g, kind).get(name)
            if got is None:
                continue
            for p, v, s in zip(g.names, got[0], got[1]):
                out[p] = (v, s)
        return out

    def to_dict(self) -> dict:
        names = sorted({k for g in self.groups for k in g.measures})
        return {
            "groups": [g.to_dict() for g in self.groups],
            "shocks": {k: v.to_dict() for k, v in self.shocks.items()},
            "settings": self.settings.to_dict(),
            "seconds": self.seconds,
            "book": {
                "base": self.total("base", "base"),
                "pnl": {k: self.total("pnl", k) for k in self.shocks},
                "measures": {k: self.total("measures", k) for k in names},
            },
        }


def revalue(
    groups: list,
    shocks: dict,
    measures: list | None = None,
    settings: RiskSettings | None = None,
    env: RiskEnv | None = None,
) -> RiskRun:
    t0 = time.perf_counter()
    settings = settings or RiskSettings()
    out = [run_group(g, shocks, measures, settings, env) for g in groups]
    return RiskRun(out, {"base": BASE, **shocks}, settings, time.perf_counter() - t0)


def single_group(book: Book, ctx, seed: int = DEFAULT_SEED, label: str | None = None) -> list:
    return [RiskGroup(label or (ctx.underlying if not isinstance(ctx, BasketContext) else "basket"), book, ctx, seed)]


__all__ = [
    "RiskGroup",
    "RiskSettings",
    "Measure",
    "RiskRun",
    "GroupRun",
    "RiskFormat",
    "revalue",
    "run_group",
    "rolled_book",
    "single_group",
    "Shock",
]
