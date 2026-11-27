"""The hedge optimizer: the trades that bring a book's Greeks to their targets at the least bid/ask cost, within limits.

Sensitivities. The candidates (futures, vanillas, swaps, dividend swaps, deposits, funding swaps) are priced as positions of
one unit in the same risk run as the book: in the group of their name, on its simulation and its shocks. A vanilla's
hedge ratios are then measured on the paths the notes are measured on (common random numbers); without that the Monte
Carlo noise of each Greek would make the ratios wrong. G is the book's Greek vector, A the candidates' per unit.

Solve. With q = p - m, p, m >= 0 and a slack s >= 0 per target:
    minimise    sum_i c_i (p_i + m_i) + sum_k P_k s_k
    subject to  |G_k + A_k (p - m) - t_k| <= tol_k + s_k,   0 <= p_i, m_i <= limit_i,   p_i, m_i integers for listed lots,
a mixed-integer linear program (HiGHS through `scipy.optimize.milp`): c_i is the half-spread of one unit, t_k and tol_k the
target and its tolerance. The slack's price P_k (`PENALTY` / tol_k) is far above any cost, so a target is missed only when
the universe or the limits cannot reach it, and the result says which.

Control. The proposal is revalued with the book under the spot x vol grid and the historical 1-day moves of the VaR, on the
same simulations: before (the book) and after (the book and the proposal).
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field

import numpy as np

from optlab.risk.engine import RiskGroup, RiskSettings
from optlab.risk.greeks import Bumps, GreekOptions, compute
from optlab.risk.shocks import RiskEnv
from optlab.structured.portfolio import Book, Position

PENALTY = 1e6  # currency per tolerance beyond a target: what missing it costs in the objective
FAMILIES = ("delta", "vega_tenor", "rho_tenor", "dividend_year", "funding_tenor")
OPTIONAL = ("gamma",)
MAX_CANDIDATES = 200
PREFIX = "cand:"


@dataclass
class Candidate:
    """One unit of a hedging instrument the optimizer may trade, with its half-spread and size limit."""

    label: str
    instrument: object  # an Instrument
    unit_cost: float
    limit: float
    integer: bool = False
    group: str = ""  # the label of the group it is priced in

    def to_dict(self) -> dict:
        inst = self.instrument
        return {
            "label": self.label,
            "instrument": inst.to_dict(),  # pyright: ignore[reportAttributeAccessIssue] (an Instrument)
            "unit_cost": self.unit_cost,
            "limit": self.limit,
            "integer": self.integer,
            "group": self.group,
        }


@dataclass
class Target:
    key: str  # a Greek key, "delta|SPX|", "vega_tenor|SPX|1y", ...
    value: float = 0.0
    tolerance: float = 1.0


@dataclass
class Solution:
    quantities: np.ndarray
    cost: float
    residual: dict  # key -> G + A q - t
    missed: list  # the keys beyond their tolerance
    status: str


def with_candidates(groups: list, candidates: list) -> list:
    """The groups with each candidate as a position of one unit named `cand:<i>`, in the group its `group` names."""
    by_label: dict = {}
    for i, c in enumerate(candidates):
        by_label.setdefault(c.group, []).append(Position(f"{PREFIX}{i}", c.instrument, 1.0))
    missing = set(by_label) - {g.label for g in groups}
    if missing:
        raise ValueError(f"candidates in groups the book does not have: {', '.join(sorted(missing))}")
    out = []
    for g in groups:
        extra = by_label.get(g.label, [])
        if not extra:
            out.append(g)
            continue
        book = Book(list(g.book.positions) + extra, g.book.linear)
        out.append(dataclasses.replace(g, book=book))
    return out


def sensitivities(report, n_candidates: int, keys: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(G, A, se of A): the book's Greeks without the candidates, and each candidate's per unit, for `keys`."""
    rows = {p["name"]: p["greeks"] for p in report.positions}
    A = np.zeros((len(keys), n_candidates))
    A_se = np.zeros_like(A)
    for i in range(n_candidates):
        g = rows.get(f"{PREFIX}{i}", {})
        for k, key in enumerate(keys):
            v = g.get(key)
            if v is not None:
                A[k, i], A_se[k, i] = v[0], v[1] or 0.0
    G = np.zeros(len(keys))
    for name, g in rows.items():
        if name.startswith(PREFIX):
            continue
        for k, key in enumerate(keys):
            v = g.get(key)
            if v is not None:
                G[k] += v[0]
    return G, A, A_se


def default_targets(greeks: dict, families=FAMILIES, relative: float = 0.1, floor: float = 1.0) -> list[Target]:
    """Every key of the families the book carries, to zero within `relative` of its size (at least `floor`)."""
    out = []
    for key, value in sorted(greeks.items()):
        family, name, bucket = (key.split("|") + ["", ""])[:3]
        if family not in families or name == "*":
            continue
        if family in ("delta", "gamma") and bucket:
            continue
        out.append(Target(key, 0.0, max(relative * abs(value), floor)))
    return out


def solve(G: np.ndarray, A: np.ndarray, targets: list, candidates: list) -> Solution:
    """The cheapest quantities within the tolerances (or the closest when the limits cannot reach them)."""
    from scipy.optimize import Bounds, LinearConstraint, milp

    n, K = A.shape[1], len(targets)
    t = np.array([x.value for x in targets], dtype=float)
    tol = np.array([max(x.tolerance, 1e-12) for x in targets], dtype=float)
    c = np.array([max(x.unit_cost, 0.0) for x in candidates], dtype=float)
    limit = np.array([max(x.limit, 0.0) for x in candidates], dtype=float)
    obj = np.concatenate([c, c, PENALTY / tol])
    upper = np.hstack([A, -A, -np.eye(K)])
    lower = np.hstack([-A, A, -np.eye(K)])
    rows = np.vstack([upper, lower])
    rhs = np.concatenate([tol + t - G, tol - t + G])
    integer = np.array([1 if x.integer else 0 for x in candidates] * 2 + [0] * K)
    upper_bounds = np.concatenate([limit, limit, np.full(K, np.inf)])
    res = milp(
        obj,
        constraints=LinearConstraint(rows, -np.inf, rhs),  # pyright: ignore[reportArgumentType] (scipy takes arrays; its stubs say float)
        integrality=integer,
        bounds=Bounds(np.zeros(2 * n + K), upper_bounds),  # pyright: ignore[reportArgumentType] (the same)
        options={"time_limit": 60.0},
    )
    if res.x is None:
        raise RuntimeError(f"the hedge optimizer failed: {res.message}")
    x = np.asarray(res.x)
    q = x[:n] - x[n : 2 * n]
    q = np.where(np.array([x.integer for x in candidates]), np.round(q), q)
    q[np.abs(q) < 1e-9] = 0.0
    resid = G + A @ q - t
    missed = [targets[k].key for k in range(K) if abs(resid[k]) > tol[k] * (1 + 1e-6)]
    return Solution(
        q,
        float(c @ np.abs(q)),
        {targets[k].key: float(resid[k]) for k in range(K)},
        missed,
        "optimal" if not missed else "closest: some targets are beyond the universe or the limits",
    )


@dataclass
class HedgeRun:
    targets: list
    proposal: list
    cost: float
    status: str
    missed: list
    greeks: dict
    grid: dict | None = None
    var: dict | None = None
    seconds: float = 0.0
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def optimise(
    groups: list,
    candidates: list,
    targets: list | None = None,
    families=FAMILIES,
    settings: RiskSettings | None = None,
    env: RiskEnv | None = None,
    bumps: Bumps | None = None,
    grid: bool = True,
    var_shocks: dict | None = None,
    relative: float = 0.1,
    floor: float = 1.0,
) -> HedgeRun:
    """Sensitivities of the book and the candidates in one risk run, the solve, and the control on the same simulations
    (`var_shocks`: the history's 1-day moves, {key: Shock}, for the VaR before and after). Without `targets`, every
    key of `families` goes to zero within `relative` of its size, at least `floor`."""
    from optlab.risk import scenarios as sc
    from optlab.risk.var import revalue_many, var_es

    t0 = time.perf_counter()
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"at most {MAX_CANDIDATES} candidates")
    settings = settings or RiskSettings(smoothing=0.005)
    env = env or RiskEnv()
    options = GreekOptions(kxt="none", curves=True, sticky_strike=False, realistic=False, cross=False, theta=False)
    report = compute(with_candidates(groups, candidates), bumps, options, settings, env)
    book_only = {
        k: v[0] - sum(p["greeks"].get(k, [0.0])[0] for p in report.positions if str(p["name"]).startswith(PREFIX))
        for k, v in report.book.items()
    }
    targets = targets if targets is not None else default_targets(book_only, families, relative, floor)
    keys = [x.key for x in targets]
    G, A, A_se = sensitivities(report, len(candidates), keys)
    sol = solve(G, A, targets, candidates)
    proposal = []
    for i, (cand, q) in enumerate(zip(candidates, sol.quantities)):
        if q:
            proposal.append(
                {**cand.to_dict(), "index": i, "quantity": float(q), "cost": abs(float(q)) * cand.unit_cost}
            )
    after = G + A @ sol.quantities
    rows = [
        {
            "key": x.key,
            "target": x.value,
            "tolerance": x.tolerance,
            "before": float(G[k]),
            "after": float(after[k]),
            "after_se": float(np.sqrt(np.sum((A_se[k] * sol.quantities) ** 2))),
            "missed": x.key in sol.missed,
        }
        for k, x in enumerate(targets)
    ]
    out = HedgeRun(rows, proposal, sol.cost, sol.status, sol.missed, {"keys": keys})
    if proposal and (grid or var_shocks):
        sized = _sized(groups, candidates, sol.quantities)
        names = {f"{PREFIX}{i}" for i, q in enumerate(sol.quantities) if q}
        if grid:
            rep = sc.run(sized, [], settings=dataclasses.replace(settings, smoothing=0.0), env=env)
            if rep.grid is not None:
                out.grid = _grid_before_after(rep, names)
        if var_shocks:
            reval = revalue_many(sized, var_shocks, dataclasses.replace(settings, smoothing=0.0), env)
            after_pnl = np.array([reval["pnl"][k][0] for k in var_shocks])
            before_pnl = np.array(
                [sum(v for p, (v, _) in reval["positions"][k].items() if p not in names) for k in var_shocks]
            )
            out.var = {"days": len(var_shocks), "before": var_es(before_pnl), "after": var_es(after_pnl)}
    out.warnings = sorted({w for g in report.groups for w in g["warnings"]})
    out.seconds = time.perf_counter() - t0
    return out


def _sized(groups: list, candidates: list, quantities) -> list:
    """The groups with each traded candidate at its solved quantity."""
    by_label: dict = {}
    for i, (c, q) in enumerate(zip(candidates, quantities)):
        if q:
            by_label.setdefault(c.group, []).append(Position(f"{PREFIX}{i}", c.instrument, float(q)))
    out = []
    for g in groups:
        extra = by_label.get(g.label, [])
        out.append(g if not extra else dataclasses.replace(g, book=Book(list(g.book.positions) + extra, g.book.linear)))
    return out


def _grid_before_after(rep, names: set) -> dict:
    """The spot x vol grid of the book alone and with the proposal, from one run's per-position P&L."""
    g = rep.grid
    cells_after = [[cell[0] for cell in row] for row in g["pnl"]]
    before = [[0.0 for _ in row] for row in g["pnl"]]
    for p in rep.positions:
        if p["name"] in names:
            continue
        for i, a in enumerate(g["spots"]):
            for j, b in enumerate(g["vols"]):
                if a or b:
                    before[i][j] += p["pnl"]["grid"][f"{a:+.2f}:{b:+.2f}"][0]
    return {"spots": g["spots"], "vols": g["vols"], "before": before, "after": cells_after, "rule": g["rule"]}


__all__ = ["Candidate", "HedgeRun", "RiskGroup", "Solution", "Target", "default_targets", "optimise", "solve"]
