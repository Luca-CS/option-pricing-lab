"""Monte Carlo pricing of a script: parse, validate, build the timeline, simulate by batches, evaluate, discount."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..market import Market
from ..models import Model
from ..products import MCResult
from .cashflows import Note, pair_averages
from .multiasset import Basket, basket_scenarios
from .scenario import simulated_scenarios
from .script.evaluator import Evaluator
from .script.nodes import DateLit, Program
from .script.parser import parse
from .script.timeline import Clock, Timeline, build_timeline
from .script.validate import validate


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


def price_many(script, model: Model, market: Market, param_sets, fmt=None, n_paths: int = 100_000, seed=0,
               batch: int | None = None, smoothing: float = 0.0, valuation=None, calendar=None, notional=None,
               check_redemption: bool = True, initial_fixing: float | None = None, fixings: dict | None = None,
               **sim_kwargs) -> list[StructuredResult]:
    """Price one script for several parameter sets on the same paths (common random numbers). `fixings` gives the
    past fixings of a running product as {underlying: {date: level}}; flows paid up to the valuation date are
    excluded from the value."""
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
    evaluators = [Evaluator(program, timeline, p, smoothing, check_redemption) for p in param_sets]
    notionals = [float(notional if notional is not None else ev.params.get("N", 1.0)) for ev in evaluators]
    pairs = [[] for _ in evaluators]
    terminated = [np.zeros(len(program.events)) for _ in evaluators]
    flows = [{} for _ in evaluators]
    red = [{} for _ in evaluators]
    batch_warnings = [[] for _ in evaluators]
    t_sim = t_eval = 0.0
    if basket:
        if initial_fixing is not None:
            raise ValueError("initial_fixing is for single-underlying scripts")
        scenarios = basket_scenarios(model, timeline.sim_times, n_paths, seed, batch, history, **sim_kwargs)
    else:
        scenarios = simulated_scenarios(model, market, name, timeline.sim_times, n_paths, seed, batch,
                                        initial_fixing, history, **sim_kwargs)
    while True:
        t0 = time.perf_counter()
        scenario = next(scenarios, None)
        t_sim += time.perf_counter() - t0
        if scenario is None:
            break
        t0 = time.perf_counter()
        for i, ev in enumerate(evaluators):
            out = ev.run(scenario)
            pairs[i].append(pair_averages(fmt.present_values(out, market, notionals[i])))
            terminated[i] += np.array([np.sum(d) for d in out.terminated])
            for key, f in out.flows.items():
                flows[i][key] = flows[i].get(key, 0.0) + float(np.sum(f))
            for t, w in out.redemption_weights.items():
                red[i][t] = red[i].get(t, 0.0) + float(np.sum(w))
            batch_warnings[i] += [w for w in out.warnings if w not in batch_warnings[i]]
        t_eval += time.perf_counter() - t0
    seconds = time.perf_counter() - t_start
    warnings = []
    if any(t <= 0 for t in timeline.pay_times if t is not None):
        warnings.append("some flows are already paid (on or before the valuation date): they are not in the value")
    if smoothing:
        warnings.append(f"digital smoothing active (eps = {smoothing:.2%}): prices are biased, see the solver notes")
    results = []
    for i in range(len(evaluators)):
        d = np.concatenate(pairs[i])
        own = warnings + batch_warnings[i]
        if not np.all(np.isfinite(d)):
            own = own + [f"non-finite present values on {np.mean(~np.isfinite(d)):.2%} of the pairs"]
        value = MCResult(float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d))), n_paths)
        term = terminated[i] / n_paths
        alive_end = 1.0 - term.sum()
        remaining = np.maximum(timeline.event_times, 0.0)  # from the valuation date, for a running product
        life = float(np.dot(term, remaining) + alive_end * remaining[-1])
        N = notionals[i]
        results.append(StructuredResult(
            value=value, price=100 * value.price / N, stderr=100 * value.stderr / N, fmt=fmt.name, notional=N,
            n_paths=n_paths, seconds=seconds, timings={"simulation": t_sim, "evaluation": t_eval},
            event_times=timeline.event_times, termination=term,
            expected_flows={k: v / n_paths for k, v in flows[i].items()}, expected_life=life,
            redemption_probabilities={t: v / n_paths for t, v in red[i].items()}, warnings=own,
            pairs=d))
    return results


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


def price(script, model: Model, market: Market, fmt=None, n_paths: int = 100_000, seed=0, batch: int | None = None,
          params: dict | None = None, **kwargs) -> StructuredResult:
    """Price a script (text or parsed Program) under a model. The price is in % of the notional (param N)."""
    return price_many(script, model, market, [params or {}], fmt, n_paths, seed, batch, **kwargs)[0]
