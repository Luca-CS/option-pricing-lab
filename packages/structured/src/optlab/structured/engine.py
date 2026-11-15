"""Batch engine: simulate each batch once, evaluate a list of evaluations on it, in sequence or on a process pool.

An evaluation is a script with its parameters, smoothing, wrapper and notional, optionally on paths scaled by a
spot factor (a sticky-moneyness spot bump multiplies the paths exactly, so delta needs no new simulation) or by one
factor per simulated date (a bump of the forward, likewise). Batches
carry their own seeds (`default_rng(seed)` for one batch, `SeedSequence(seed).spawn(n)` for several), so a pool
of processes returns the same batches as the sequential loop, and the summaries are added in the same order: the
results are identical bit for bit whatever the number of workers.

With `engine="cpu"`, `"gpu"` or `"auto"` (or $OPTLAB_ENGINE, or `optlab.core.engines.set_default`), the paths come from the compiled
engine (`optlab.native`, reached by name through `optlab.core.engines`) instead of NumPy: one process, every CPU thread or the GPU, and the next batch is simulated
while the current one is evaluated. Its draws depend on (seed, pair) only, so the batches are sized by memory and the
prices depend on (seed, n_paths) alone. A model the engine does not cover goes to NumPy, and the timings say so.

The pool uses the "spawn" start method (the only one on Windows): the calling program must be importable, so run
it from a file with the usual `if __name__ == "__main__":` guard (not from code piped to the interpreter).
"""

from __future__ import annotations

import atexit
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from optlab.core import engines, progress
from optlab.core.models import simulate
from optlab.structured.cashflows import pair_averages
from optlab.structured.multiasset import Basket, BasketScenario, simulate_basket
from optlab.structured.scenario import Scenario, batch_sizes
from optlab.structured.script.evaluator import Evaluator

if TYPE_CHECKING:
    from optlab.structured.script.nodes import Program
    from optlab.structured.script.timeline import Timeline


@dataclass
class Evaluation:
    program: Program
    timeline: Timeline
    params: dict
    fmt: Any  # Note, Swap, FundedSwap or risk.engine.RiskFormat: present_values(flows, market, notional)
    notional: float
    smoothing: float = 0.0
    check_redemption: bool = True
    spot_factor: float | np.ndarray | dict = 1.0  # one factor or one per simulated date; {name: factor} for a basket
    controls: tuple = ()  # (name, time, forward): S(t) / F(t) - 1 has mean zero, a control variate
    ramps: dict | None = None  # (event index, line) -> Ramp: digitals read as call spreads


@dataclass
class Totals:
    """Aggregated results of one evaluation over all batches."""

    pairs: list = field(default_factory=list)
    terminated: np.ndarray | None = None
    flows: dict = field(default_factory=dict)
    redemption: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    controls: list = field(default_factory=list)

    def add(self, summary):
        pairs, terminated, flows, red, warnings, controls = summary
        self.pairs.append(pairs)
        if controls is not None:
            self.controls.append(controls)
        self.terminated = terminated.copy() if self.terminated is None else self.terminated + terminated
        for key, v in flows.items():
            self.flows[key] = self.flows.get(key, 0.0) + v
        for t, v in red.items():
            self.redemption[t] = self.redemption.get(t, 0.0) + v
        self.warnings += [w for w in warnings if w not in self.warnings]


def _scenario(model, market, name, times, size, seed, spot0, history, sim_kwargs):
    rng = np.random.default_rng(seed)
    if isinstance(model, Basket):
        paths = (
            simulate_basket(model, times, size, rng, **sim_kwargs)
            if len(times)
            else {n: np.empty((size, 0)) for n in model.names}
        )
        return BasketScenario(times, paths, spot0, history)
    paths = simulate(model, market, times, size, rng, **sim_kwargs) if len(times) else np.empty((size, 0))
    return Scenario(name, times, paths, spot0, history)


def _no_change(factor) -> bool:
    return np.ndim(factor) == 0 and factor == 1.0


def _scaled(scenario, factor):
    if isinstance(scenario, BasketScenario):
        if not isinstance(factor, dict):
            factor = {n: factor for n in scenario.paths}
        if all(_no_change(f) for f in factor.values()):
            return scenario
        paths = {n: p if _no_change(factor.get(n, 1.0)) else p * factor[n] for n, p in scenario.paths.items()}
        windows = {
            k: {n: (lo * factor.get(n, 1.0), hi * factor.get(n, 1.0)) for n, (lo, hi) in w.items()}
            for k, w in scenario.windows.items()
        }  # reduced windows only come with one factor per name
        return BasketScenario(scenario.times, paths, scenario.spot0, scenario.history, windows)
    if _no_change(factor):
        return scenario
    windows = {k: (lo * factor, hi * factor) for k, (lo, hi) in scenario.windows.items()}
    return Scenario(scenario.name, scenario.times, scenario.paths * factor, scenario.spot0, scenario.history, windows)


def _controls(ev: Evaluation, scenario):
    """(pairs, controls) matrix of pair-averaged S(t) / (f F(t)) - 1 at the control dates."""
    cols = []
    for name, t, fwd in ev.controls:
        f = ev.spot_factor.get(name, 1.0) if isinstance(ev.spot_factor, dict) else ev.spot_factor
        cols.append(pair_averages(scenario.spot(name, t) / (f * fwd) - 1.0))
    return np.stack(cols, axis=1)


def _summary(ev: Evaluation, scenario, market):
    scenario = _scaled(scenario, ev.spot_factor)
    out = Evaluator(ev.program, ev.timeline, ev.params, ev.smoothing, ev.check_redemption, ev.ramps).run(scenario)
    pairs = pair_averages(ev.fmt.present_values(out, market, ev.notional))
    terminated = np.array([np.sum(d) for d in out.terminated])
    flows = {key: float(np.sum(f)) for key, f in out.flows.items()}
    red = {t: float(np.sum(w)) for t, w in out.redemption_weights.items()}
    controls = _controls(ev, scenario) if ev.controls else None
    return pairs, terminated, flows, red, list(out.warnings), controls


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


NATIVE_BATCH_BYTES = 256 * 2**20  # paths of one engine batch: memory, not numbers, sets the size


def native_pair_batches(n_pairs: int, n_columns: int, parts: int = 1) -> list[int]:
    """Pairs per engine batch: at most what fits in NATIVE_BATCH_BYTES of double spots, and at most n_pairs / parts
    so that `parts` evaluations can run at once; at least 4096, or 16384 when split for parallel evaluation (the
    script's interpretation costs about the same per batch whatever its size)."""
    floor = 4096 if parts <= 1 else 16384
    per = max(floor, min(NATIVE_BATCH_BYTES // max(1, 2 * 8 * n_columns), -(-n_pairs // max(1, parts))))
    return [per] * (n_pairs // per) + ([n_pairs % per] if n_pairs % per else [])


def evaluation_threads(backend: str) -> int:
    """Threads evaluating batches at once. On the GPU the NumPy evaluation is the bottleneck, and NumPy releases the
    GIL inside its array operations: one thread per physical core (half the hardware threads), at most 8. On the CPU
    backend the engine already uses every core: one."""
    return max(1, min(8, engines.require().cpu_threads() // 2)) if backend == "gpu" else 1


def reduction(times, evaluations) -> tuple:
    """(kept observation indices, windows as (first, last) indices, their keys) when the scripts read the daily dates
    of at most the engine's MAX_WINDOWS windows only through their lowest and highest spots (the only way the language
    reads a window): the engine then returns the event dates and two columns per window instead of one per day.
    (None, (), ()) when there is nothing to gain or a window is not a contiguous run of simulated dates."""
    from optlab.structured.scenario import window_key

    for ev in evaluations:  # a factor per date (a forward bump) would scale a window's days unequally
        factors = ev.spot_factor.values() if isinstance(ev.spot_factor, dict) else [ev.spot_factor]
        if any(np.ndim(f) > 0 for f in factors):
            return None, (), ()
    times = np.asarray(times, dtype=float)
    index = {round(float(t), 12): j for j, t in enumerate(times)}
    found = {}
    for ev in evaluations:
        for wtimes in ev.timeline.windows.values():
            future = np.asarray(wtimes, dtype=float)
            future = future[future > 0]
            if len(future) < 3:
                continue
            j = [index.get(round(float(t), 12)) for t in future]
            first = j[0]
            if None in j or first is None or j != list(range(first, first + len(j))):
                return None, (), ()
            found[window_key(future)] = (j[0], j[-1])
    if not found or len(found) > engines.require().MAX_WINDOWS:
        return None, (), ()
    needed = set()
    for ev in evaluations:
        needed |= {round(float(t), 12) for t in ev.timeline.event_times if t > 0}
        needed |= {round(float(t), 12) for _, t, _ in ev.controls if t > 0}
    keep = sorted(index[t] for t in needed if t in index)
    if len(keep) + 2 * len(found) >= len(times):
        return None, (), ()
    return keep, tuple(found.values()), tuple(found)


def _run_native(backend, model, market, name, times, n_paths, seed, spot0, history, evaluations, sim_kwargs):
    from optlab.structured.multiasset import Basket

    t_start = time.perf_counter()
    eng = engines.require()
    plan = eng.plan(model, market, times, sim_kwargs.get("dt_max", 1 / 252), sim_kwargs.get("dt_min", 1 / 2000))
    key = engines.seed64(seed)
    threads = int(sim_kwargs.get("eval_threads") or evaluation_threads(backend))
    keep, windows, wkeys = reduction(times, evaluations) if sim_kwargs.get("reduce", True) else (None, (), ())
    kept_times = np.asarray(times, dtype=float) if keep is None else np.asarray(times, dtype=float)[keep]
    columns = len(kept_times) + 2 * len(windows)
    sizes = native_pair_batches(n_paths // 2, len(plan.names) * columns, 2 * threads if threads > 1 else 1)
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]]).astype(int)
    basket = isinstance(model, Basket)
    n = len(sizes)

    def produce(k):
        t = time.perf_counter()
        spots, extremes, timing = eng.run(
            plan, sizes[k], key, int(starts[k]), backend, int(sim_kwargs.get("threads", 0)), keep, windows
        )
        if basket:
            reduced = {wk: {nm: extremes[a][w] for a, nm in enumerate(plan.names)} for w, wk in enumerate(wkeys)}
            scenario = BasketScenario(kept_times, dict(zip(plan.names, spots)), spot0, history, reduced)
        else:
            reduced = {wk: extremes[0][w] for w, wk in enumerate(wkeys)}
            scenario = Scenario(name, kept_times, spots[0], spot0, history, reduced)
        return scenario, time.perf_counter() - t, timing["kernel_ms"]

    def evaluate(scenario):
        t = time.perf_counter()
        return [_summary(ev, scenario, market) for ev in evaluations], time.perf_counter() - t

    # The engine produces batches in order on one thread (ctypes releases the GIL) while up to `threads` batches are
    # evaluated; at most threads + 1 batches wait in memory. Summaries are added in batch order, so the numbers do not
    # depend on the number of threads.
    totals = [Totals() for _ in evaluations]
    t_sim = t_eval = kernel = 0.0
    with ThreadPoolExecutor(max_workers=1) as producer, ThreadPoolExecutor(max_workers=threads) as pool:
        sims = {k: producer.submit(produce, k) for k in range(min(n, threads + 1))}
        evals = {}

        def collect(j):
            nonlocal t_eval
            summaries, te = evals.pop(j).result()
            t_eval += te
            for tot, summary in zip(totals, summaries):
                tot.add(summary)
            progress.tick(j + 1, n, "batches")  # a reporter may stop here: the pools finish their few batches first

        for k in range(n):
            scenario, ts, km = sims.pop(k).result()
            t_sim, kernel = t_sim + ts, kernel + km
            evals[k] = pool.submit(evaluate, scenario)
            if k >= threads:
                collect(k - threads)
            if k + threads + 1 < n:
                sims[k + threads + 1] = producer.submit(produce, k + threads + 1)
        for j in sorted(evals):
            collect(j)
    return totals, {
        "simulation": t_sim,
        "evaluation": t_eval,
        "wall": time.perf_counter() - t_start,
        "workers": threads,
        "engine": backend,
        "kernel_ms": kernel,
        "batches": n,
        "columns": columns,
        "windows": len(windows),
    }


def run(
    model,
    market,
    name,
    times,
    n_paths: int,
    seed,
    batch,
    spot0,
    history,
    evaluations: list,
    workers: int | None = None,
    **sim_kwargs,
):
    """Totals per evaluation and timings {"simulation", "evaluation", "wall", "workers", "engine"}. `engine` in
    sim_kwargs picks the source of the paths (numpy, cpu, gpu, auto)."""
    backend, why = engines.choose(sim_kwargs.pop("engine", None), model)
    if backend != "numpy" and (not len(times) or not sim_kwargs.get("antithetic", True) or n_paths % 2):
        backend, why = "numpy", "no simulated date, or no antithetic pairs"
    if backend != "numpy":
        return _run_native(backend, model, market, name, times, n_paths, seed, spot0, history, evaluations, sim_kwargs)
    sim_kwargs.pop("threads", None)
    sim_kwargs.pop("eval_threads", None)
    sizes = batch_sizes(n_paths, batch)
    seeds = [seed] if len(sizes) == 1 else list(np.random.SeedSequence(seed).spawn(len(sizes)))
    jobs = [
        (model, market, name, times, size, s, spot0, history, evaluations, sim_kwargs) for size, s in zip(sizes, seeds)
    ]
    t0 = time.perf_counter()
    results = []
    if workers and workers > 1 and len(jobs) > 1:
        futures = [process_pool(workers).submit(run_batch, job) for job in jobs]  # in order, as map would
        try:
            for k, f in enumerate(futures):
                results.append(f.result())
                progress.tick(k + 1, len(jobs), "batches")
        except BaseException:  # a failed batch or a cancelled job: the batches not started are dropped
            for f in futures:
                f.cancel()
            raise
    else:
        workers = 1
        for k, job in enumerate(jobs):
            results.append(run_batch(job))
            progress.tick(k + 1, len(jobs), "batches")
    totals = [Totals() for _ in evaluations]
    t_sim = t_eval = 0.0
    for summaries, ts, te in results:
        t_sim, t_eval = t_sim + ts, t_eval + te
        for tot, summary in zip(totals, summaries):
            tot.add(summary)
    return totals, {
        "simulation": t_sim,
        "evaluation": t_eval,
        "wall": time.perf_counter() - t0,
        "workers": workers,
        "engine": "numpy",
        **({"engine_note": why} if why else {}),
    }
