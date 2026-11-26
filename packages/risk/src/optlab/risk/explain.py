"""The vol part of a day's explained P&L, opened: node by node, by delta bucket, and under each spot-vol rule.

The cascade of `pnl.explain_day` moves the surface in two steps, vol (d's at-the-money vols per tenor) and skew (the rest of
d's surface), after a spot step that keeps d-1's surface in moneyness. This module answers what those steps leave open.

Under each rule (`shocks.RULES`) the spot step reads d-1's surface as that rule says (`pnl.spot_surface`): sticky moneyness
keeps it, sticky strike holds the vols at absolute strikes, realistic adds the at-the-money move the spot-vol betas predict.
The vol and skew steps then carry what the rule did not. The skew step ends on d's surface whatever the rule, so spot, vol and
skew add up to the same amount under the three rules: the rule decides only how that amount is split, and the Greeks of the
rule (its delta) predict its spot step.

Node by node, under one rule, from the surface after the spot step (S) to d's surface (S_d), each step is a full revaluation
on d-1's seeds, so the steps add up to the vol and skew steps exactly. With m_ij the move of the implied vol at the node
(strike i, tenor j) of the risk grid, S_d - S read at the node, the steps of a tenor are:
    level   the at-the-money move m_aj on every strike of the tenor (a tenor tent);
    nodes   at each other strike, the move of its node beyond the level, m_ij - m_aj (a node tent);
from the shortest tenor to the longest, the strikes from the lowest up, and last the residual: from S moved by the tents to
S_d, the part of the move the grid does not hold (between nodes, beyond its ends). A tenor whose tent does not reach the
group's last date moves no value and is skipped (its part is zero, exactly). The order is fixed: a cross effect between two
nodes goes to the later one. With `detail="tenor"` the nodes of a tenor are one step (level, then shape).

Each node is labelled by its forward call delta N(d1) on S, d1 = -k / (s sqrt T) + s sqrt T / 2 at k = ln K / F: the money
reads about 52 delta at one year and 20 % vol. The delta buckets (90, 75, 50, 25 and 10 delta of a call; 90 is the 10-delta
put) take each node's amount, split linearly between the two buckets around its delta; the nodes beyond the ends go to the
end buckets, so the buckets add up to the nodes. Since S is the surface the rule leaves, the deltas and the moves (hence the
buckets) depend on the rule.
"""

from __future__ import annotations

import dataclasses
import time

import numpy as np

from optlab.core import progress
from optlab.marketdata.days import as_day
from optlab.risk import grid
from optlab.risk.engine import RiskGroup, RiskSettings, run_group
from optlab.risk.pnl import (
    _atm_move,
    _maturity,
    _rolled_positions,
    _step_contexts,
    book_at,
    end_context,
    eod,
    explain_day,
    real_fixings,
)
from optlab.risk.shocks import RULES, RiskEnv, Shock
from optlab.risk.store import RiskStorage, code_version, run_key
from optlab.risk.surfaces import TentBumpedSurface
from optlab.structured.multiasset import BasketContext

RULE_STEPS = ("spot", "vol", "skew")
DETAILS = ("node", "tenor")
DELTA_BUCKETS = (0.9, 0.75, 0.5, 0.25, 0.1)  # forward call deltas
DELTA_LABELS = ("10Δ put", "25Δ put", "ATM", "25Δ call", "10Δ call")
ATM = grid.STRIKES.index(1.0)


# --- the nodes ---


node_vols, node_deltas = grid.node_vols, grid.node_deltas


def delta_buckets(values: np.ndarray, deltas: np.ndarray) -> np.ndarray:
    """Amounts per node (n_strikes, n_tenors) into the delta buckets, (n_buckets, n_tenors): each node split linearly
    between the two buckets around its delta, the ends to the end buckets. The buckets add up to the nodes."""
    B = np.asarray(DELTA_BUCKETS)
    out = np.zeros((len(B), values.shape[1]))
    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            d, v = float(deltas[i, j]), float(values[i, j])
            if d >= B[0]:
                out[0, j] += v
            elif d <= B[-1]:
                out[-1, j] += v
            else:
                m = int(np.searchsorted(-B, -d, side="right")) - 1  # B[m] >= d > B[m + 1]
                w = (d - B[m + 1]) / (B[m] - B[m + 1])
                out[m, j] += w * v
                out[m + 1, j] += (1.0 - w) * v
    return out


def _live_tenors(t_max: float) -> list[int]:
    """Tenors whose tent reaches [0, t_max]: the first, and every one whose left neighbour is before t_max."""
    return [j for j in range(len(grid.TENORS)) if j == 0 or grid.TENORS[j - 1] < t_max]


def node_steps(moves: np.ndarray, tenors: list[int], detail: str = "node") -> list[tuple[str, int, int | None, dict]]:
    """The cascade's steps from the node moves: (kind, tenor, strike or None, move) with the move as {"term": {j: x}} or
    {"nodes": {(i, j): x}}; zero moves are left out."""
    out: list[tuple[str, int, int | None, dict]] = []
    for j in tenors:
        level = float(moves[ATM, j])
        if level:
            out.append(("level", j, None, {"term": {j: level}}))
        shape = {(i, j): float(moves[i, j] - level) for i in range(len(grid.STRIKES)) if i != ATM}
        shape = {k: v for k, v in shape.items() if v}
        if not shape:
            continue
        if detail == "tenor":
            out.append(("shape", j, None, {"nodes": shape}))
        else:
            out += [("node", j, i, {"nodes": {(i, j): v}}) for (i, _), v in shape.items()]
    return out


# --- revaluations ---


def _diff(x, y) -> tuple[float, float]:
    d = np.asarray(x, dtype=float) - np.asarray(y, dtype=float)
    if not np.ndim(d):
        return float(d), 0.0
    return float(np.mean(d)), float(d.std(ddof=1) / np.sqrt(len(d)))


def _with_surfaces(ctx, surfaces: dict):
    """The context with the named surfaces replaced (nothing else moves)."""
    if isinstance(ctx, BasketContext):
        vols = {**ctx.vols, **surfaces}
        contexts = {
            n: dataclasses.replace(c, surface=vols[n], slv_cache={}) if n in surfaces else c
            for n, c in ctx.contexts.items()
        }
        return BasketContext(ctx.names, ctx.markets, vols, ctx.corr, ctx.valuation, contexts, {}, ctx.payment_currency)
    (name,) = surfaces
    return dataclasses.replace(ctx, surface=surfaces[name], slv_cache={})


def _surfaces(ctx) -> dict:
    return dict(ctx.vols) if isinstance(ctx, BasketContext) else {ctx.underlying: ctx.surface}


class _Valuer:
    """Values of one group's rolled positions on a context, per pair of paths, summed over the positions."""

    def __init__(self, ga: RiskGroup, books: dict, settings: RiskSettings, since: float):
        self.ga, self.books, self.settings, self.since = ga, books, settings, since
        self.revaluations = 0

    def __call__(self, ctx, book: str = "rolled"):
        r = run_group(
            RiskGroup(self.ga.label, self.books[book], ctx, self.ga.seed),
            {},
            None,
            self.settings,
            None,
            cash_since=self.since,
            keep_pairs=True,
        )
        self.revaluations += 1
        return np.sum([np.asarray(p, dtype=float) for p in (r.pairs or [])], axis=0)


def _rule_deltas(ga: RiskGroup, names, settings: RiskSettings, env: RiskEnv, rules) -> dict:
    """{rule: {name: cash delta per +1 % of the group}} under the rules other than sticky moneyness (whose delta is the
    EOD run's), on d-1's group and seed, at the Greeks' smoothing."""
    shocks, out = {}, {}
    for r in rules:
        if r == "sticky_moneyness":
            continue
        for n in names:
            shocks[f"{r}+|{n}"] = Shock(spot={n: 0.01}, rule=r)
            shocks[f"{r}-|{n}"] = Shock(spot={n: -0.01}, rule=r)
    if not shocks:
        return out
    greek_settings = dataclasses.replace(settings, smoothing=settings.smoothing or 0.005)
    run = run_group(ga, shocks, None, greek_settings, env)
    for r in rules:
        if r == "sticky_moneyness":
            continue
        out[r] = {n: 0.5 * (run.pnl[f"{r}+|{n}"][2] - run.pnl[f"{r}-|{n}"][2]) for n in names}
    return out


def _book_greek(greeks_a: dict, positions, key: str) -> float:
    return float(sum(((greeks_a.get(p) or {}).get(key) or [0.0])[0] for p in positions))


def _skew_of(surface) -> float:
    if np.isscalar(surface):
        return 0.0
    k = np.log([0.9, 1.1])
    return float(np.subtract(*surface.iv(k, np.ones(2))))


class _Acc:
    """What the groups of a day add up: steps per rule (value, variance), predictions, node rows per name, the residual."""

    def __init__(self, rules, greeks_a: dict):
        self.rules, self.greeks_a = rules, greeks_a
        self.steps = {r: {s: [0.0, 0.0] for s in RULE_STEPS} for r in rules}
        self.pred = {r: {s: 0.0 for s in RULE_STEPS} for r in rules}
        self.per_name: dict = {}
        self.residual = [0.0, 0.0]

    def greek(self, positions, key: str) -> float:
        return _book_greek(self.greeks_a, positions, key)


def _group_rules(acc: _Acc, value, contexts, names, positions, ratio, sb, deltas_rule, v_skew) -> dict:
    """Spot, vol and skew of one group under each rule, and their prediction; {rule: values after its spot step}."""
    v_time = value(contexts["sticky_moneyness"]["time"], "time")
    v_spot = {}
    for r in acc.rules:
        vs, vv = value(contexts[r]["spot"]), value(contexts[r]["vol"])
        v_spot[r] = vs
        for step, (x, y) in (("spot", (vs, v_time)), ("vol", (vv, vs)), ("skew", (v_skew, vv))):
            m, se = _diff(x, y)
            acc.steps[r][step][0] += m
            acc.steps[r][step][1] += se**2
        ss = _surfaces(contexts[r]["spot"])
        for n in names:
            x = float(ratio[n] - 1.0) / 0.01
            delta = acc.greek(positions, f"delta|{n}|") if r == "sticky_moneyness" else deltas_rule[r][n]
            acc.pred[r]["spot"] += delta * x + 0.5 * acc.greek(positions, f"gamma|{n}|") * x * x
            acc.pred[r]["vol"] += sum(
                acc.greek(positions, f"vega_tenor|{n}|{lab}") * dv / 0.01
                for lab, dv in zip(grid.TENOR_LABELS, _atm_move(ss[n], sb[n]))
            )
            acc.pred[r]["skew"] += acc.greek(positions, f"skew_vega|{n}|") * (_skew_of(sb[n]) - _skew_of(ss[n])) / 0.01
    return v_spot


def _group_nodes(acc: _Acc, value, start, sb, names, positions, tenors, detail, prev, label):
    """The node cascade of one group from `start` (the context after the spot step) towards d's surfaces `sb`, name after
    name; returns the values after its last step (the residual step to d's surface is the caller's)."""
    ss = _surfaces(start)
    current = dict(ss)
    shape = (len(grid.STRIKES), len(grid.TENORS))
    for n in names:
        row = acc.per_name.setdefault(n, _name_row())
        if np.isscalar(ss[n]) or np.isscalar(sb[n]):
            continue  # a flat vol: its move is in the residual
        moves = node_vols(sb[n]) - node_vols(ss[n])
        H, term = np.zeros(shape), np.zeros(shape[1])
        for kind, j, i, mv in node_steps(moves, tenors, detail):
            for (ii, jj), x in mv.get("nodes", {}).items():
                H[ii, jj] += x
            for jj, x in mv.get("term", {}).items():
                term[jj] += x
            current[n] = TentBumpedSurface(ss[n], nodes=H.copy(), term=term.copy())
            v = value(_with_surfaces(start, current))
            m, se = _diff(v, prev)
            prev = v
            _book_step(row, kind, j, i, m, se)
        if not row["groups"]:  # a name in several groups (alone and in a basket) has one surface per day
            row["moves"], row["deltas"] = moves.tolist(), node_deltas(ss[n]).tolist()
        row["predicted_level"] = [
            row["predicted_level"][j] + acc.greek(positions, f"vega_tenor|{n}|{lab}") * float(moves[ATM, j]) / 0.01
            for j, lab in enumerate(grid.TENOR_LABELS)
        ]
        row["skipped"] = [j for j in range(len(grid.TENORS)) if j not in tenors]
        row["groups"].append(label)
    return prev


def _book_step(row: dict, kind: str, j: int, i, m: float, se: float) -> None:
    if kind == "level":
        row["level"][j] += m
        row["level_var"][j] += se**2
        row["nodes"][ATM][j] += m
        row["nodes_var"][ATM][j] += se**2
    elif kind == "shape":
        row["shape"][j] += m
        row["shape_var"][j] += se**2
    else:
        row["shape"][j] += m
        row["nodes"][i][j] += m
        row["nodes_var"][i][j] += se**2


# --- the day ---


def explain_detail(
    lake,
    trades,
    a,
    b,
    store: RiskStorage | None = None,
    settings: RiskSettings | None = None,
    layer: str = "reconstructed",
    rule: str = "sticky_moneyness",
    detail: str = "node",
    betas: dict | None = None,
    reuse: bool = True,
) -> dict:
    """The vol part of the explained P&L from the close of `a` to that of `b`: the cascade's spot, vol and skew steps under
    each rule, and under `rule` the vol move node by node (or tenor by tenor) with the delta buckets. `betas` {name: per
    tenor} enables the realistic rule. Stored as a "pnl_detail" run."""
    from optlab.structured.dates import year_fraction

    if rule not in RULES:
        raise ValueError(f"unknown vol rule {rule!r}, expected one of {', '.join(RULES)}")
    if detail not in DETAILS:
        raise ValueError(f"unknown detail {detail!r}, expected one of {', '.join(DETAILS)}")
    if rule == "realistic" and not betas:
        raise ValueError("the realistic rule needs the spot-vol betas, and the lake has none for these names")
    t0 = time.perf_counter()
    a, b = as_day(a), as_day(b)
    settings = settings or RiskSettings(smoothing=0.0)
    code = code_version()
    day = explain_day(lake, trades, a, b, store, settings, layer)
    betas = {n: [float(x) for x in v] for n, v in (betas or {}).items()}
    key = run_key(
        kind="pnl_detail",
        pnl=day.get("run_id") or f"{a.isoformat()}:{b.isoformat()}",
        rule=rule,
        detail=detail,
        betas=betas,
        code=code,
        settings=settings.to_dict(),
        layer=layer,
    )
    if store is not None and reuse:
        got = store.find_run(key)
        if got is not None:
            return {**got["payload"], "run_id": got["run_id"], "reused": True}
    rules = [r for r in RULES if r != "realistic" or betas]
    ea = eod(lake, trades, a, store, settings, layer)
    acc = _Acc(rules, {p["trade_id"]: p["greeks"] or {} for p in ea["positions"]})
    bda, bdb = book_at(lake, trades, a, layer), book_at(lake, trades, b, layer)
    since = -year_fraction(a, b)
    env = RiskEnv(betas=betas)
    revaluations = 0
    for g_i, ga in enumerate(bda.groups):
        progress.step(
            f"{ga.label}: the spot, vol and skew steps under each rule, then the vol move by {detail}",
            g_i,
            len(bda.groups),
        )
        gb_ctx, ratio = end_context(lake, ga, bda, bdb, b, layer)
        names = list(ga.ctx.names) if isinstance(ga.ctx, BasketContext) else [ga.ctx.underlying]
        positions = [p.name for p in ga.book.positions]
        real_b = {tid: real_fixings(lake, bda.trades[tid], b) for tid in positions}
        scale_a = {n: bda.scales[n] for n in names}
        value = _Valuer(
            ga,
            {
                "time": _rolled_positions(ga.book, real_b, scale_a, a, b, frozen=True),
                "rolled": _rolled_positions(ga.book, real_b, scale_a, a, b, frozen=False),
            },
            settings,
            since,
        )
        contexts = {r: _step_contexts(ga, gb_ctx, ratio, a, b, r, betas) for r in rules}
        v_skew = value(contexts["sticky_moneyness"]["skew"])
        sb = _surfaces(gb_ctx)
        deltas_rule = _rule_deltas(ga, names, settings, env, rules)
        v_spot = _group_rules(acc, value, contexts, names, positions, ratio, sb, deltas_rule, v_skew)
        # the vol move under `rule`, node by node, from the surface after its spot step to d's
        t_max = max((year_fraction(b, _maturity(bda.trades[p])) for p in positions), default=0.0)
        last = _group_nodes(
            acc,
            value,
            contexts[rule]["spot"],
            sb,
            names,
            positions,
            _live_tenors(t_max),
            detail,
            v_spot[rule],
            ga.label,
        )
        m, se = _diff(v_skew, last)
        acc.residual[0] += m
        acc.residual[1] += se**2
        revaluations += value.revaluations
    steps = {r: {s: v[0] for s, v in acc.steps[r].items()} for r in rules}
    names_out = {n: _finish(row, detail) for n, row in acc.per_name.items()}
    vol_total = steps[rule]["vol"] + steps[rule]["skew"]
    payload = {
        "from": a.isoformat(),
        "to": b.isoformat(),
        "rule": rule,
        "detail": detail,
        "rules": {
            r: {
                "steps": steps[r],
                "steps_se": {s: float(np.sqrt(v[1])) for s, v in acc.steps[r].items()},
                "predicted": acc.pred[r],
                "unexplained": {s: steps[r][s] - acc.pred[r][s] for s in RULE_STEPS},
            }
            for r in rules
        },
        "unavailable": {} if "realistic" in rules else {"realistic": "no spot-vol betas for these names"},
        "names": names_out,
        "residual": acc.residual[0],
        "residual_se": float(np.sqrt(acc.residual[1])),
        "vol_total": vol_total,
        "check": vol_total - acc.residual[0] - sum(sum(x["level"]) + sum(x["shape"]) for x in names_out.values()),
        "grid": {
            "strikes": list(grid.STRIKE_LABELS),
            "tenors": list(grid.TENOR_LABELS),
            "buckets": list(DELTA_LABELS),
            "bucket_deltas": list(DELTA_BUCKETS),
        },
        "pnl_run": day.get("run_id"),
        "revaluations": revaluations,
        "seconds": time.perf_counter() - t0,
        "code": code,
    }
    if store is not None:
        payload["run_id"] = store.save_run(
            "pnl_detail",
            b,
            key,
            {"from": a.isoformat(), "rule": rule, "detail": detail, "settings": settings.to_dict(), "layer": layer},
            payload,
            payload["seconds"],
            code,
        )
    return payload


def _name_row() -> dict:
    shape = (len(grid.STRIKES), len(grid.TENORS))
    return {
        "level": [0.0] * shape[1],
        "level_var": [0.0] * shape[1],
        "shape": [0.0] * shape[1],
        "shape_var": [0.0] * shape[1],
        "nodes": np.zeros(shape).tolist(),
        "nodes_var": np.zeros(shape).tolist(),
        "moves": np.zeros(shape).tolist(),
        "deltas": np.zeros(shape).tolist(),
        "predicted_level": [0.0] * shape[1],
        "skipped": [],
        "groups": [],
    }


def _finish(row: dict, detail: str) -> dict:
    nodes = np.asarray(row["nodes"])
    out = {
        "level": row["level"],
        "level_se": np.sqrt(np.asarray(row["level_var"])).tolist(),
        "shape": row["shape"],
        "by_tenor": [x + y for x, y in zip(row["level"], row["shape"])],
        "predicted_level": row["predicted_level"],
        "moves": row["moves"],
        "deltas": row["deltas"],
        "skipped": row["skipped"],
        "groups": row["groups"],
    }
    if detail == "node":
        out["nodes"] = nodes.tolist()
        out["nodes_se"] = np.sqrt(np.asarray(row["nodes_var"])).tolist()
        out["buckets"] = delta_buckets(nodes, np.asarray(row["deltas"])).tolist()
    else:
        out["shape_se"] = np.sqrt(np.asarray(row["shape_var"])).tolist()
    return out
