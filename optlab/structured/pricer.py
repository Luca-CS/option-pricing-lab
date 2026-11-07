"""Monte Carlo pricing of a script: parse, validate, build the timeline, simulate by batches, evaluate, discount."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..log import get_logger
from ..market import Market
from ..models import Model
from ..products import MCResult
from . import engine
from .cashflows import Note
from .engine import Evaluation
from .multiasset import Basket
from .script.evaluator import Evaluator
from .script.nodes import DateLit, Program
from .script.parser import parse
from .script.timeline import Clock, Timeline, build_timeline
from .script.validate import validate

LOG = get_logger(__name__)


@dataclass
class StructuredResult:
    value: MCResult  # present value in currency units
    price: float  # % of notional
    stderr: float  # % of notional
    fmt: str
    notional: float
    n_paths: int
    seconds: float
    timings: dict
    event_times: np.ndarray
    termination: np.ndarray  # probability that the product ends at each event
    expected_flows: dict  # (payment time, tag) → undiscounted expectation
    expected_life: float
    redemption_probabilities: dict  # payment time → probability that the redemption is paid then
    warnings: list = field(default_factory=list)
    method: str = "monte_carlo"
    numerical_error: float = 0.0  # replication: change of the price (in %) when the grid is halved
    pairs: np.ndarray | None = field(default=None, repr=False)  # antithetic pair averages of the present value

    def __iter__(self):
        return iter((self.price, self.stderr))


def _program(script) -> Program:
    return validate(parse(script) if isinstance(script, str) else script)


def price_many(
    script,
    model: Model,
    market: Market,
    param_sets,
    fmt=None,
    n_paths: int = 100_000,
    seed=0,
    batch: int | None = None,
    smoothing: float = 0.0,
    valuation=None,
    calendar=None,
    notional=None,
    check_redemption: bool = True,
    initial_fixing: float | dict | None = None,
    fixings: dict | None = None,
    workers: int | None = None,
    spot_factors: list | None = None,
    smoothings: list | None = None,
    control_forwards: bool = False,
    ramps: list | None = None,
    **sim_kwargs,
) -> list[StructuredResult]:
    """Price one script for several parameter sets on the same paths (common random numbers). `fixings` gives the
    past fixings of a running product as {underlying: {date: level}}; flows paid up to the valuation date are
    excluded from the value. `workers` runs the batches on a process pool (same numbers bit for bit);
    `spot_factors` evaluates each parameter set on the paths scaled by its factor (a sticky-moneyness spot bump,
    the product keeping its initial fixing); `smoothings` gives each parameter set its own smoothing; `ramps` its digitals read as call
    spreads ({(event index, line): Ramp}, see digitals.py).
    `control_forwards` uses S(t) / F(t) - 1 at the script's observation dates as control variates (exact mean
    zero in every model): the coefficients are fitted on the same paths, per parameter set, so the corrected price
    stays affine in the coupon; the variance ratio is reported in `timings`."""
    t_start = time.perf_counter()
    fmt = fmt or Note()
    program = _program(script)
    names = program.underlying_names
    basket = isinstance(model, Basket)
    if basket and set(model.names) != set(names):
        raise ValueError(f"the basket simulates {', '.join(model.names)}, the script observes {', '.join(names)}")
    if not basket and len(names) != 1:
        raise ValueError(f"the script observes {len(names)} underlyings: price it with a Basket model")
    name = names[0]
    clock = Clock(valuation, calendar)
    timeline: Timeline = build_timeline(program, clock)
    history = basket_history(fixings, names, clock) if basket else history_times(fixings, name, clock)
    if basket:
        for n, h in history.items():
            check_fixing_scale(h, model.markets[n].spot, n)
    else:
        check_fixing_scale(history, market.spot, name)
    merged = [{**program.param_values, **(p or {})} for p in param_sets]
    for p in param_sets:  # unknown parameters fail here, before any simulation
        Evaluator(program, timeline, p, smoothing, check_redemption)
    notionals = [float(notional if notional is not None else m.get("N", 1.0)) for m in merged]
    if basket:
        if initial_fixing is not None and not isinstance(initial_fixing, dict):
            raise ValueError("initial_fixing of a basket is a {name: level} dict")
        spot0 = {n: (initial_fixing or {}).get(n, model.markets[n].spot) for n in model.names}
    else:
        spot0 = market.spot if initial_fixing is None else initial_fixing
    n_sets = len(param_sets)
    controls = _forward_controls(program, timeline, model, market) if control_forwards else ()
    evaluations = [
        Evaluation(program, timeline, p, fmt, N, eps, check_redemption, f, controls, r)
        for p, N, f, eps, r in zip(
            param_sets,
            notionals,
            spot_factors or [1.0] * n_sets,
            smoothings or [smoothing] * n_sets,
            ramps or [None] * n_sets,
        )
    ]
    totals, timings = engine.run(
        model,
        market,
        name,
        timeline.sim_times,
        n_paths,
        seed,
        batch,
        spot0,
        history,
        evaluations,
        workers,
        **sim_kwargs,
    )
    t_sim, t_eval = timings["simulation"], timings["evaluation"]
    seconds = time.perf_counter() - t_start
    warnings = []
    if any(t <= 0 for t in timeline.pay_times if t is not None):
        warnings.append("some flows are already paid (on or before the valuation date): they are not in the value")
    if smoothing or any(smoothings or []):
        eps = max([smoothing, *(smoothings or [])])
        warnings.append(f"digital smoothing active (eps = {eps:.2%}): prices are biased, see the solver notes")
    LOG.debug(
        "priced %s param set(s), %s paths, model %s, %s dates: simulation %.3f s, evaluation %.3f s",
        len(evaluations),
        n_paths,
        type(model).__name__,
        len(timeline.sim_times),
        t_sim,
        t_eval,
    )
    results = []
    for i in range(len(evaluations)):
        tot = totals[i]
        d = np.concatenate(tot.pairs)
        own_timings = dict(timings)
        if control_forwards and tot.controls:
            d, own_timings["variance_ratio"] = _controlled(d, np.concatenate(tot.controls))
        own = warnings + tot.warnings
        if not np.all(np.isfinite(d)):
            own = own + [f"non-finite present values on {np.mean(~np.isfinite(d)):.2%} of the pairs"]
            LOG.warning(own[-1])
        value = MCResult(float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d))), n_paths)
        term = tot.terminated / n_paths
        alive_end = 1.0 - term.sum()
        remaining = np.maximum(timeline.event_times, 0.0)  # from the valuation date, for a running product
        life = float(np.dot(term, remaining) + alive_end * remaining[-1])
        N = notionals[i]
        results.append(
            StructuredResult(
                value=value,
                price=100 * value.price / N,
                stderr=100 * value.stderr / N,
                fmt=fmt.name,
                notional=N,
                n_paths=n_paths,
                seconds=seconds,
                timings=own_timings,
                event_times=timeline.event_times,
                termination=term,
                expected_flows={k: v / n_paths for k, v in tot.flows.items()},
                expected_life=life,
                redemption_probabilities={t: v / n_paths for t, v in tot.redemption.items()},
                warnings=own,
                pairs=d,
            )
        )
    return results


def _forward_controls(program, timeline, model, market) -> tuple:
    from .profile import profile

    times = profile(program, timeline).observation_times
    names = program.underlying_names
    markets = model.markets if isinstance(model, Basket) else {names[0]: market}
    return tuple((n, t, float(markets[n].F(t))) for n in names for t in times)


def _controlled(d: np.ndarray, X: np.ndarray):
    """d - X beta with beta fitted by least squares on centred data; returns it and var(d) / var(corrected)."""
    Xc, dc = X - X.mean(axis=0), d - d.mean()
    beta = np.linalg.lstsq(Xc, dc, rcond=None)[0]
    corrected = d - X @ beta
    return corrected, float(np.var(d) / np.var(corrected))


def check_fixing_scale(history: dict, spot: float, name: str, band=(0.2, 5.0)):
    """Past fixings far from the model's spot are almost always real index levels against a market rescaled to
    100 (or the reverse): refuse them instead of pricing a product struck at the wrong level."""
    for t, level in history.items():
        if not band[0] * spot <= level <= band[1] * spot:
            raise ValueError(
                f"fixing {level:g} of {name} (t = {t:.4f}) is {level / spot:.1f} times the model spot "
                f"{spot:g}: real index levels against a market rescaled to 100? convert them with "
                "optlab.marketdata.to_model_scale"
            )


def basket_history(fixings: dict | None, names, clock: Clock) -> dict:
    """Past fixings of several underlyings as {name: {time: level}}."""
    unknown = set(fixings or {}) - set(names)
    if unknown:
        raise KeyError(f"fixings given for {', '.join(sorted(unknown))}, the script observes {', '.join(names)}")
    return {n: history_times({n: fixings[n]}, n, clock) for n in names if fixings and n in fixings}


def history_times(fixings: dict | None, name: str, clock: Clock) -> dict:
    """Past fixings of one underlying as {time: level}."""
    if not fixings:
        return {}
    unknown = set(fixings) - {name}
    if unknown:
        raise KeyError(f"fixings given for {', '.join(sorted(unknown))}, the script observes {name}")
    out = {}
    for d, level in fixings.get(name, {}).items():
        t = clock.time(DateLit(str(d)))
        if t > 0:
            raise ValueError(f"fixing on {d} is after the valuation date {clock.valuation}")
        out[t] = float(level)
    return out


def price(
    script,
    model: Model,
    market: Market,
    fmt=None,
    n_paths: int = 100_000,
    seed=0,
    batch: int | None = None,
    params: dict | None = None,
    **kwargs,
) -> StructuredResult:
    """Price a script (text or parsed Program) under a model. The price is in % of the notional (param N)."""
    return price_many(script, model, market, [params or {}], fmt, n_paths, seed, batch, **kwargs)[0]
