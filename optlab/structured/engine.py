"""Batch engine: simulate each batch once, evaluate a list of evaluations on it, in sequence or on a process pool.

An evaluation is a script with its parameters, smoothing, wrapper and notional, optionally on paths scaled by a
spot factor (a sticky-moneyness spot bump multiplies the paths exactly, so delta needs no new simulation). Batches
carry their own seeds (`default_rng(seed)` for one batch, `SeedSequence(seed).spawn(n)` for several), so a pool
of processes returns the same batches as the sequential loop, and the summaries are added in the same order: the
results are identical bit for bit whatever the number of workers.

The pool uses the "spawn" start method (the only one on Windows): the calling program must be importable, so run
it from a file with the usual `if __name__ == "__main__":` guard (not from code piped to the interpreter).
"""

from __future__ import annotations

import atexit
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from ..models import simulate
from .cashflows import pair_averages
from .multiasset import Basket, BasketScenario, simulate_basket
from .scenario import Scenario, batch_sizes
from .script.evaluator import Evaluator


@dataclass
class Evaluation:
    program: object
    timeline: object
    params: dict
    fmt: object
    notional: float
    smoothing: float = 0.0
    check_redemption: bool = True
    spot_factor: float | dict = 1.0  # float for one name, {name: factor} for a basket


@dataclass
class Totals:
    """Aggregated results of one evaluation over all batches."""
    pairs: list = field(default_factory=list)
    terminated: np.ndarray | None = None
    flows: dict = field(default_factory=dict)
    redemption: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def add(self, summary):
        pairs, terminated, flows, red, warnings = summary
        self.pairs.append(pairs)
        self.terminated = terminated.copy() if self.terminated is None else self.terminated + terminated
        for key, v in flows.items():
            self.flows[key] = self.flows.get(key, 0.0) + v
        for t, v in red.items():
            self.redemption[t] = self.redemption.get(t, 0.0) + v
        self.warnings += [w for w in warnings if w not in self.warnings]


def _scenario(model, market, name, times, size, seed, spot0, history, sim_kwargs):
    rng = np.random.default_rng(seed)
    if isinstance(model, Basket):
        paths = simulate_basket(model, times, size, rng, **sim_kwargs) if len(times) else \
            {n: np.empty((size, 0)) for n in model.names}
        return BasketScenario(times, paths, spot0, history)
    paths = simulate(model, market, times, size, rng, **sim_kwargs) if len(times) else np.empty((size, 0))
    return Scenario(name, times, paths, spot0, history)


def _scaled(scenario, factor):
    if isinstance(scenario, BasketScenario):
        if not isinstance(factor, dict):
            factor = {n: float(factor) for n in scenario.paths}
        if all(f == 1.0 for f in factor.values()):
            return scenario
        paths = {n: p * factor.get(n, 1.0) if factor.get(n, 1.0) != 1.0 else p for n, p in scenario.paths.items()}
        return BasketScenario(scenario.times, paths, scenario.spot0, scenario.history)
    if factor == 1.0:
        return scenario
    return Scenario(scenario.name, scenario.times, scenario.paths * factor, scenario.spot0, scenario.history)


def _summary(ev: Evaluation, scenario, market):
    out = Evaluator(ev.program, ev.timeline, ev.params, ev.smoothing, ev.check_redemption).run(
        _scaled(scenario, ev.spot_factor))
    pairs = pair_averages(ev.fmt.present_values(out, market, ev.notional))
    terminated = np.array([np.sum(d) for d in out.terminated])
    flows = {key: float(np.sum(f)) for key, f in out.flows.items()}
    red = {t: float(np.sum(w)) for t, w in out.redemption_weights.items()}
    return pairs, terminated, flows, red, list(out.warnings)


def run_batch(job):
    """One batch: simulate, then summarise every evaluation (module-level so that a process pool can pickle it)."""
    model, market, name, times, size, seed, spot0, history, evaluations, sim_kwargs = job
    t0 = time.perf_counter()
    scenario = _scenario(model, market, name, times, size, seed, spot0, history, sim_kwargs)
    t1 = time.perf_counter()
    summaries = [_summary(ev, scenario, market) for ev in evaluations]
    return summaries, t1 - t0, time.perf_counter() - t1


_POOL: ProcessPoolExecutor | None = None
_POOL_WORKERS = 0


def process_pool(workers: int) -> ProcessPoolExecutor:
    """A persistent pool (starting processes costs about 2 s on Windows), created on first use, one BLAS thread
    per worker."""
    global _POOL, _POOL_WORKERS
    if _POOL is None or _POOL_WORKERS != workers:
        shutdown_pool()
        old = os.environ.get("OPENBLAS_NUM_THREADS")
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        try:
            _POOL = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"))
            _POOL_WORKERS = workers
        finally:
            if old is None:
                os.environ.pop("OPENBLAS_NUM_THREADS", None)
            else:
                os.environ["OPENBLAS_NUM_THREADS"] = old
    return _POOL


def shutdown_pool():
    global _POOL, _POOL_WORKERS
    if _POOL is not None:
        _POOL.shutdown()
    _POOL, _POOL_WORKERS = None, 0


atexit.register(shutdown_pool)


def run(model, market, name, times, n_paths: int, seed, batch, spot0, history, evaluations: list,
        workers: int | None = None, **sim_kwargs):
    """Totals per evaluation and timings {"simulation", "evaluation", "wall", "workers"}."""
    sizes = batch_sizes(n_paths, batch)
    seeds = [seed] if len(sizes) == 1 else list(np.random.SeedSequence(seed).spawn(len(sizes)))
    jobs = [(model, market, name, times, size, s, spot0, history, evaluations, sim_kwargs)
            for size, s in zip(sizes, seeds)]
    t0 = time.perf_counter()
    if workers and workers > 1 and len(jobs) > 1:
        results = list(process_pool(workers).map(run_batch, jobs))
    else:
        workers = 1
        results = [run_batch(job) for job in jobs]
    totals = [Totals() for _ in evaluations]
    t_sim = t_eval = 0.0
    for summaries, ts, te in results:
        t_sim, t_eval = t_sim + ts, t_eval + te
        for tot, summary in zip(totals, summaries):
            tot.add(summary)
    return totals, {"simulation": t_sim, "evaluation": t_eval, "wall": time.perf_counter() - t0, "workers": workers}
