"""Spot ladder under three vol rules, and the spot-vol betas the realistic rule reads.

Points: every spot of a group moved together by -30 % ... +10 %, under
- sticky moneyness (the model's own: the surface moves with the forward; a spot move only scales the paths),
- sticky strike (implied vols stay at absolute strikes),
- realistic (sticky moneyness plus an at-the-money move of beta_T ln(1 + h) per tenor, the betas measured on the lake).
At each point: the value, the P&L against the point 0 (paired, with its SE), and the Greeks around the shocked market (delta,
gamma, vega in total and per tenor, vanna, volga, rho, epsilon), per position and for the book. The rule applies to the point
only: the Greeks around it are the model's sticky-moneyness Greeks of the shocked market, so the three ladders compare the
markets the rules lead to. The point 0 is the base itself (same paths, same value to the cent).

Betas: OLS of the daily change of the at-the-money vol per tenor on the daily log return, over a window of business days
(two years by default) ending at the date, on one surface layer; each with its standard error, stored and versioned.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import grid
from .curves import CurveMove
from .engine import Measure, RiskSettings, run_group_safe
from .shocks import RULES, RiskEnv, Shock, VolMove, context_currency

POINTS = (-0.30, -0.20, -0.10, -0.05, 0.0, 0.05, 0.10)


# --- betas ---

def estimate_betas(lake, name: str, day, window: int = 500, layer: str = "reconstructed") -> dict:
    """Spot-vol betas of a name at a date: d(ATM vol at T) = a + beta_T d ln S, per tenor, with standard errors."""
    from .history import closes, vol_factors
    day = pd.Timestamp(day).date()
    start = (pd.Timestamp(day) - pd.tseries.offsets.BDay(window + 10)).date()
    vols = vol_factors(lake, name, start, day, layer)
    spots = closes(lake, [name], start, day)[name]
    df = vols.join(spots.rename("spot"), how="inner").dropna(subset=["spot"]).tail(window + 1)
    if len(df) < 60:
        raise ValueError(f"only {len(df)} days with a surface and a close of {name} up to {day}")
    r = np.log(df["spot"]).diff().to_numpy()[1:]
    betas, ses, r2 = [], [], []
    x = r - r.mean()
    for label in grid.TENOR_LABELS:
        dv = df[f"atm_{label}"].diff().to_numpy()[1:]
        y = dv - dv.mean()
        b = float(x @ y / (x @ x))
        resid = y - b * x
        s2 = float(resid @ resid / (len(x) - 2))
        betas.append(b)
        ses.append(float(np.sqrt(s2 / (x @ x))))
        r2.append(float(1 - (resid @ resid) / (y @ y)) if y @ y > 0 else 0.0)
    origins = sorted(set(df["origin"]))
    return {"name": name, "day": day.isoformat(), "window": window, "n": int(len(x)), "layer": layer, "origin": origins,
            "start": df.index[0].isoformat(), "end": df.index[-1].isoformat(), "tenors": list(grid.TENOR_LABELS),
            "betas": betas, "se": ses, "r2": r2, "unit": "vol (fraction) per unit of daily log return"}


def stored_betas(store, lake, names, day, window: int = 500, layer: str = "reconstructed", refresh: bool = False) -> dict:
    """{name: betas record} for a date: read from the store, estimated and stored when missing (or with `refresh`)."""
    out = {}
    for n in names:
        key = f"{n}@{pd.Timestamp(day).date().isoformat()}#{window}:{layer}"
        got = None if refresh else store.get("betas", key)
        if got is None:
            rec = estimate_betas(lake, n, day, window, layer)
            version = store.put("betas", key, rec)
            got = {"payload": rec, "version": version}
        out[n] = {**got["payload"], "version": got["version"]}
    return out


def env_with_betas(env: RiskEnv | None, betas: dict) -> RiskEnv:
    env = env or RiskEnv()
    return dataclasses.replace(env, betas={**env.betas, **{n: list(b["betas"]) for n, b in betas.items()}})


# --- ladder ---

@dataclass
class LadderOptions:
    points: tuple = POINTS
    rules: tuple = RULES
    h_spot: float = 0.01
    h_vol: float = 0.01
    rate_bp: float = 10.0
    tenors: bool = True  # vega per tenor at every point
    greeks: bool = True  # Greeks at every point (else values and P&L only)

    @classmethod
    def from_dict(cls, d: dict | None) -> "LadderOptions":
        d = dict(d or {})
        known = {f.name for f in dataclasses.fields(cls)}
        out = cls(**{k: (tuple(v) if k in ("points", "rules") else v) for k, v in d.items() if k in known})
        bad = set(out.rules) - set(RULES)
        if bad:
            raise ValueError(f"unknown vol rule(s) {', '.join(sorted(bad))}")
        return out


def _point_key(rule: str, p: float) -> str:
    return "base" if p == 0 else f"{rule}@{p:+.4f}"


def plan(ctx, options: LadderOptions, env: RiskEnv | None = None) -> tuple[dict, list]:
    """(shocks, measures): "value@rule@p", "pnl@rule@p" and the Greeks "delta@rule@p"... (all names moved together)."""
    o = options
    cur = context_currency(ctx)
    S, M = {}, []
    h, v = o.h_spot, o.h_vol
    for rule in o.rules:
        for p in o.points:
            P = Shock() if p == 0 else Shock(spot={"*": p}, rule=rule)
            key = _point_key(rule, p)
            if key != "base":
                S[key] = P
            tag = f"{rule}@{p:+.4f}"
            M.append(Measure(f"value@{tag}", {key: 1.0}))
            M.append(Measure(f"pnl@{tag}", {key: 1.0, "base": -1.0}) if key != "base" else Measure(f"pnl@{tag}", {"base": 0.0}))
            if not o.greeks:
                continue
            if p == 0 and rule != o.rules[0]:  # the point 0 is the same for every rule
                for g in ("delta", "gamma", "vega", "volga", "vanna", "rho", "epsilon") + tuple(
                        f"vega_tenor:{lab}" for lab in (grid.TENOR_LABELS if o.tenors else ())):
                    first = f"{g}@{o.rules[0]}@{p:+.4f}"
                    M.append(Measure(f"{g}@{tag}", {"__alias__": 0.0, first: 0.0}))
                continue

            def add(name, shock):
                k = f"{name}|{tag}"
                S[k] = P + shock
                return k

            up, dn = add("s+", Shock(spot={"*": h})), add("s-", Shock(spot={"*": -h}))
            vu, vd = add("v+", Shock(vol={"*": VolMove(v)})), add("v-", Shock(vol={"*": VolMove(-v)}))
            ru, rd = add("r+", Shock(rates={cur: CurveMove(o.rate_bp)})), add("r-", Shock(rates={cur: CurveMove(-o.rate_bp)}))
            qu, qd = add("q+", Shock(dividend={"*": CurveMove(o.rate_bp)})), add("q-", Shock(dividend={"*": CurveMove(-o.rate_bp)}))
            pp = add("x++", Shock(spot={"*": h}, vol={"*": VolMove(v)}))
            pm = add("x+-", Shock(spot={"*": h}, vol={"*": VolMove(-v)}))
            mp = add("x-+", Shock(spot={"*": -h}, vol={"*": VolMove(v)}))
            mm = add("x--", Shock(spot={"*": -h}, vol={"*": VolMove(-v)}))
            us, uv = 0.01 / h, 0.01 / v
            M += [Measure(f"delta@{tag}", {up: 0.5 * us, dn: -0.5 * us}),
                  Measure(f"gamma@{tag}", {up: us**2, key: -2 * us**2, dn: us**2}),
                  Measure(f"vega@{tag}", {vu: 0.5 * uv, vd: -0.5 * uv}),
                  Measure(f"volga@{tag}", {vu: uv**2, key: -2 * uv**2, vd: uv**2}),
                  Measure(f"vanna@{tag}", {pp: 0.25 * us * uv, pm: -0.25 * us * uv, mp: -0.25 * us * uv, mm: 0.25 * us * uv}),
                  Measure(f"rho@{tag}", {ru: 0.5 / o.rate_bp, rd: -0.5 / o.rate_bp}),
                  Measure(f"epsilon@{tag}", {qu: 0.5 / o.rate_bp, qd: -0.5 / o.rate_bp})]
            if o.tenors:
                for j, lab in enumerate(grid.TENOR_LABELS):
                    k = add(f"t{j}", Shock(vol={"*": VolMove(term={j: v})}))
                    M.append(Measure(f"vega_tenor:{lab}@{tag}", {k: uv, key: -uv}))
    return S, M


@dataclass
class LadderReport:
    points: list
    rules: list
    positions: list  # [{"name", "group", "quantity", "values": {measure: [v, se]}}]
    book: dict  # {measure: [v, se]}
    groups: list
    betas: dict
    options: dict
    settings: dict
    seconds: float
    tenors: list = field(default_factory=lambda: list(grid.TENOR_LABELS))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def table(self, rule: str, what: str = "pnl", position: str | None = None) -> list:
        """[(point, value, se)] of one measure along the ladder of a rule."""
        src = self.book if position is None else next(p for p in self.positions if p["name"] == position)["values"]
        return [(p, *src[f"{what}@{rule}@{p:+.4f}"]) for p in self.points]


def ladder(groups: list, options: LadderOptions | None = None, settings: RiskSettings | None = None, env: RiskEnv | None = None,
           betas: dict | None = None) -> LadderReport:
    t0 = time.perf_counter()
    o = options or LadderOptions()
    settings = settings or RiskSettings(smoothing=0.005)
    env = env_with_betas(env, betas) if betas else (env or RiskEnv())
    positions, book_v, book_var, rows = [], {}, {}, []
    for g in groups:
        shocks, measures = plan(g.ctx, o, env)
        aliases = [m for m in measures if "__alias__" in m.weights]
        real = [m for m in measures if "__alias__" not in m.weights]
        run = run_group_safe(g, shocks, real, settings, env)
        for m in aliases:  # the point 0's Greeks, shared by the rules
            src = next(k for k in m.weights if k != "__alias__")
            run.measures[m.name] = run.measures[src]
        for i, name in enumerate(run.names):
            positions.append({"name": name, "group": g.label, "quantity": run.quantities[i],
                              "values": {k: [v[0][i], v[1][i]] for k, v in run.measures.items()}})
        for k, v in run.measures.items():
            book_v[k] = book_v.get(k, 0.0) + v[2]
            book_var[k] = book_var.get(k, 0.0) + v[3] ** 2
        rows.append({"label": g.label, "method": run.method, "model": run.model, "n_paths": run.n_paths, "seed": run.seed,
                     "simulations": run.simulations, "shocks": len(shocks), "seconds": run.seconds, "warnings": run.warnings})
    book = {k: [v, float(np.sqrt(book_var[k]))] for k, v in book_v.items()}
    return LadderReport(list(o.points), list(o.rules), positions, book, rows, betas or {},
                        {**dataclasses.asdict(o), "points": list(o.points), "rules": list(o.rules)}, settings.to_dict(),
                        time.perf_counter() - t0)
