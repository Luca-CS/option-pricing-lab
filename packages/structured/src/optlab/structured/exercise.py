"""The issuer's call: an exercise policy fitted by regression (Longstaff-Schwartz) on a pilot simulation, then applied
out of sample.

A script calls with `call pay <amount>` at a call date: the issuer may redeem there. The evaluator runs the script
forward as if the issuer never called and keeps, per event, the flows paid before and after the call statement, the
call's mask and amount, and the declared state (`state` in the script; the spots by default). Calling is paying the
amount and terminating, so every flow of the called paths after the call is the uncalled one times the survival of the
calls: `apply` rebuilds the flows of any policy exactly from that record.

The policy is fitted backward on a pilot batch of its own seed: at each call date, the note's continuation (the flows
after the call, discounted as the note discounts them, seen from the issuer) is regressed on polynomials of the state,
over the paths the call reaches; the issuer calls where the fitted continuation is above the call amount, and the
realised flows of the pilot follow that decision to the date before. Applied out of sample on the pricing batches, a
policy is one the issuer could follow, never better than the best: the note's value is an upper bound of the value
under the issuer's optimal call, and the issuer's option a lower bound. A risk run keeps the base's policy for every
shock (smooth Greeks on common random numbers, the envelope theorem).
"""

from __future__ import annotations

import dataclasses
import itertools
from dataclasses import dataclass, field

import numpy as np

PILOT_KEY = 0x5EED_CA11  # the pilot's seed is SeedSequence(seed, spawn_key=(PILOT_KEY,)): no pricing batch shares it
MAX_TERMS = 25  # terms of the regression, the constant included
MAX_KNOTS = 9  # hinges per state variable, at its deciles
MIN_PATHS_PER_TERM = 20  # fewer paths reaching a call date than this per term: fewer hinges, a lower degree


@dataclass
class CallRecord:
    """What a call statement saw on a batch: its event, payment time, mask, the alive mass and the amount there, and
    the state (paths x variables)."""

    event: int
    t_pay: float
    mask: np.ndarray
    alive: np.ndarray
    amount: np.ndarray
    state: np.ndarray


@dataclass
class EventFlows:
    """One event's undiscounted flows by tag and redemption weights, before and after its call statement (an event
    without a call has everything before)."""

    t_pay: float | None
    pre: dict = field(default_factory=dict)
    post: dict = field(default_factory=dict)
    red_pre: np.ndarray | float = 0.0
    red_post: np.ndarray | float = 0.0


@dataclass
class Exercise:
    """The record of one uncalled run: the events' flows, the calls, the alive mass at each event's start and at
    the end."""

    events: list
    calls: dict  # event index -> CallRecord
    alive_before: list
    alive_end: np.ndarray | float


def survival_and_flows(out, decisions: dict):
    """The flows, redemption weights and terminated masses of `out` (an evaluator's BatchFlows with its `exercise`
    record) when the issuer calls where `decisions[k]` is 1, written back into `out`."""
    ex: Exercise = out.exercise
    n = out.n_paths
    surv = np.ones(n)
    flows: dict = {}
    red: dict = {}
    terminated = []
    for k, ev in enumerate(ex.events):
        call = ex.calls.get(k)
        called = call.mask * decisions[k] if call is not None else 0.0
        kept = surv * (1.0 - called)
        t = ev.t_pay
        for tag in set(ev.pre) | set(ev.post):
            f = surv * ev.pre.get(tag, 0.0) + kept * ev.post.get(tag, 0.0)
            flows[(t, tag)] = flows.get((t, tag), 0.0) + f
        w = surv * ev.red_pre + kept * ev.red_post
        if call is not None:
            paid = surv * called * call.alive
            if np.any(paid != 0):  # a call no path takes adds no flow: the uncalled run's flows come back as they were
                amount = np.where(paid != 0, paid * call.amount, 0.0)
                flows[(t, "redemption")] = flows.get((t, "redemption"), 0.0) + amount
                w = w + paid
        if t is not None and (np.any(w != 0) or t in out.redemption_weights):
            red[t] = red.get(t, 0.0) + w
        after = ex.alive_before[k + 1] if k + 1 < len(ex.alive_before) else ex.alive_end
        terminated.append(surv * ex.alive_before[k] - kept * after)
        surv = kept
    out.flows, out.redemption_weights, out.terminated = flows, red, terminated
    out.alive = surv * ex.alive_end
    return out


def _powers(d: int, degree: int) -> list[tuple[int, ...]]:
    """Exponents of the monomials of total degree <= degree in d variables, constant first, lowest degrees first."""
    out = []
    for total in range(degree + 1):
        for combo in itertools.combinations_with_replacement(range(d), total):
            p = [0] * d
            for i in combo:
                p[i] += 1
            out.append(tuple(p))
    return out


def _basis(z: np.ndarray) -> tuple[list, list]:
    """The regression's terms on the normalised state z (paths x variables): polynomials (degree 3 in one variable,
    2 in up to four, 1 beyond) and hinges max(z_i - k, 0) at each variable's deciles, which follow the kinks a
    continuation value has at a coupon or knock-in barrier. As many as the paths allow, at most MAX_TERMS."""
    n, d = z.shape
    room = min(MAX_TERMS, n // MIN_PATHS_PER_TERM)
    for degree in (3 if d == 1 else 2 if d <= 4 else 1, 1, 0):
        powers = _powers(d, degree)
        if len(powers) <= room:
            break
    per = max(0, min(MAX_KNOTS, (room - len(powers)) // d))
    knots = []
    for i in range(d):
        levels = np.unique(np.quantile(z[:, i], np.linspace(0.0, 1.0, per + 2)[1:-1])) if per else []
        lo, hi = z[:, i].min(), z[:, i].max()
        knots += [(i, float(k)) for k in levels if lo < k < hi]  # a knot at an end adds nothing (a binary flag)
    return powers, knots


@dataclass(frozen=True)
class Rule:
    """One call date's rule: the fitted continuation of the state, against the call amount."""

    mean: tuple
    scale: tuple
    powers: tuple  # exponents of each polynomial term
    knots: tuple  # (variable, knot): max(z_i - knot, 0)
    coef: tuple = ()

    def features(self, state: np.ndarray) -> np.ndarray:
        z = (state - np.asarray(self.mean)) / np.asarray(self.scale)
        cols = [np.prod(z ** np.asarray(p), axis=1) for p in self.powers]
        cols += [np.maximum(z[:, i] - k, 0.0) for i, k in self.knots]
        return np.stack(cols, axis=1)

    def continuation(self, state: np.ndarray) -> np.ndarray:
        return self.features(state) @ np.asarray(self.coef)


@dataclass(frozen=True)
class Policy:
    """The issuer's call rule at each call event (by index), fitted on a pilot of `pilot_paths` paths."""

    rules: dict  # event index -> Rule, or None where the pilot reached no path (never called there)
    pilot_paths: int
    in_sample: float = float("nan")  # the pilot's value under its own policy (biased low for the note)

    def decisions(self, out) -> dict:
        ex: Exercise = out.exercise
        dec = {}
        for k, call in ex.calls.items():
            rule = self.rules.get(k)
            if rule is None:
                dec[k] = np.zeros(out.n_paths)
            else:
                dec[k] = (rule.continuation(call.state) > call.amount).astype(float)
        return dec

    def apply(self, out):
        return survival_and_flows(out, self.decisions(out))


def fit(out, discount) -> Policy:
    """Backward regression on the pilot's record: `discount(t)` is the note's discount factor."""
    ex: Exercise = out.exercise
    n = out.n_paths

    def pv(flows: dict, t) -> np.ndarray:
        if t is None or t <= 0:
            return np.zeros(n)
        total = sum((np.broadcast_to(f, (n,)) for f in flows.values()), np.zeros(n))
        return discount(t) * total

    future = np.zeros(n)  # value at 0 of the flows from the next event on, alive at its start
    rules: dict = {}
    for k in range(len(ex.events) - 1, -1, -1):
        ev = ex.events[k]
        call = ex.calls.get(k)
        if call is None:
            future = pv(ev.pre, ev.t_pay) + future
            continue
        after = pv(ev.post, ev.t_pay) + future
        reach = np.broadcast_to(call.mask * call.alive, (n,))
        on = reach > 0
        d = discount(call.t_pay) if call.t_pay > 0 else 1.0
        decide = np.zeros(n)
        if on.sum() == 0:
            rules[k] = None
        else:
            # per unit reached, in money of the call date: the continuation the issuer weighs against the amount
            y = after[on] / (d * reach[on])
            x = call.state[on]
            mean, scale = x.mean(axis=0), x.std(axis=0)
            scale = np.where(scale > 0, scale, 1.0)
            powers, knots = _basis((x - mean) / scale)
            shape = Rule(tuple(float(v) for v in mean), tuple(float(v) for v in scale), tuple(powers), tuple(knots))
            coef = np.linalg.lstsq(shape.features(x), y, rcond=None)[0]
            rule = dataclasses.replace(shape, coef=tuple(float(c) for c in coef))
            rules[k] = rule
            decide = (rule.continuation(call.state) > call.amount).astype(float)
        called = call.mask * decide
        paid = d * called * call.alive * call.amount if call.t_pay > 0 else called * call.alive * call.amount
        future = pv(ev.pre, ev.t_pay) + (1.0 - called) * after + paid
    return Policy(rules, n, float(np.mean(future)))


NEVER = Policy({}, 0)  # the issuer never calls: the uncalled flows, as the script reads without a policy


def pilot_paths(n_paths: int) -> int:
    """The pilot's size: half the pricing paths, between 20,000 and 100,000, even (antithetic pairs)."""
    n = max(20_000, min(n_paths // 2, 100_000))
    return n + n % 2


def pilot_seed(seed) -> np.random.SeedSequence:
    """The pilot's seed, a child of the pricing seed that no pricing batch takes."""
    if isinstance(seed, np.random.SeedSequence):
        return np.random.SeedSequence(seed.entropy, spawn_key=(*seed.spawn_key, PILOT_KEY))
    return np.random.SeedSequence(seed, spawn_key=(PILOT_KEY,))
