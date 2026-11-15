"""Hypothetical stress scenarios: named, coherent market moves kept as versioned, signed data.

A scenario is a name, a description and a `Shock` (spots, vols with their rule, skew, dividends, correlation, rates...). The
set lives in the risk store: every save is a new version with who signed it and why; the defaults below are starting values,
labelled as such, for the desk to correct. Running a set revalues the book under each scenario on the base's random numbers:
P&L per position and for the book, with paired standard errors. Each shocked surface is checked for static arbitrage and a
scenario that breaks it is flagged.

A spot x vol grid (spot -30 % to +10 % against a parallel vol move of -5 to +15 points) comes with it: the spots ride on one
simulation per vol level.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass

import numpy as np

from optlab.risk.curves import CurveMove
from optlab.risk.engine import RiskSettings, run_group_safe
from optlab.risk.shocks import RiskEnv, Shock, VolMove, apply
from optlab.risk.surfaces import arbitrage
from optlab.structured.multiasset import BasketContext

STARTING = "starting value: to be corrected by the desk"
KEY = "hypothetical"


@dataclass
class Scenario:
    name: str
    description: str
    shock: Shock
    label: str = STARTING

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "shock": self.shock.to_dict(), "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> Scenario:
        if not d.get("name"):
            raise ValueError("a scenario needs a name")
        return cls(
            str(d["name"]),
            str(d.get("description", "")),
            Shock.from_dict(d.get("shock") or {}),
            str(d.get("label", "")),
        )


def defaults() -> list[Scenario]:
    return [
        Scenario(
            "Equity crash",
            "Spots -20 %, vols by the measured spot-vol betas, skew +2 points, dividends -30 %, "
            "correlation +15 points, rates -50 bp",
            Shock(
                spot={"*": -0.20},
                rule="realistic",
                vol={"*": VolMove(skew=0.02)},
                dividend_relative={"*": -0.30},
                corr=0.15,
                rates={"*": CurveMove(-50.0)},
            ),
        ),
        Scenario("Grind down", "Spots -10 %, vols +3 points", Shock(spot={"*": -0.10}, vol={"*": VolMove(0.03)})),
        Scenario("Vol spike", "Spots -5 %, vols +10 points", Shock(spot={"*": -0.05}, vol={"*": VolMove(0.10)})),
        Scenario("Rally", "Spots +10 %, vols -3 points", Shock(spot={"*": 0.10}, vol={"*": VolMove(-0.03)})),
        Scenario("Dividend cut", "Dividends -50 %", Shock(dividend_relative={"*": -0.50})),
        Scenario("Rates up", "Rates +100 bp", Shock(rates={"*": CurveMove(100.0)})),
    ]


def load(store) -> dict:
    """{"scenarios": [Scenario], "version", "signed_by", "note", "created_at", "starting"}; the defaults when nothing is stored."""
    got = store.get("scenarios", KEY)
    if got is None:
        return {
            "scenarios": defaults(),
            "version": None,
            "signed_by": None,
            "note": "starting values, not saved yet",
            "created_at": None,
            "starting": True,
        }
    return {
        "scenarios": [Scenario.from_dict(d) for d in got["payload"]],
        "version": got["version"],
        "signed_by": got["meta"].get("signed_by"),
        "note": got["meta"].get("note"),
        "created_at": got["created_at"],
        "starting": False,
    }


def save(store, scenarios: list, signed_by: str, note: str = "") -> int:
    """A new version of the set, signed. Names must be unique; every shock must parse."""
    if not signed_by or not str(signed_by).strip():
        raise ValueError("a scenario set is signed: give who saves it")
    items = [s if isinstance(s, Scenario) else Scenario.from_dict(s) for s in scenarios]
    names = [s.name for s in items]
    if len(set(names)) != len(names):
        raise ValueError("scenario names must be unique")
    return store.put(
        "scenarios", KEY, [s.to_dict() for s in items], {"signed_by": str(signed_by).strip(), "note": note}
    )


def history(store) -> list[dict]:
    return store.versions("scenarios", KEY)


def _arbitrage_flags(ctx, shock: Shock, env: RiskEnv, horizon: float = 3.0) -> dict:
    """{name: {"butterfly_free", "calendar_free", "min_g", "min_wT"}} of the shocked surfaces that differ from the base."""
    try:
        moved = apply(ctx, shock, env)
    except KeyError as exc:
        return {"error": str(exc)}
    out = {}
    pairs = (
        [(n, ctx.vols[n], moved.vols[n]) for n in ctx.names]
        if isinstance(ctx, BasketContext)
        else [(ctx.underlying, ctx.surface, moved.surface)]
    )
    for n, before, after in pairs:
        if after is before or np.isscalar(after):
            continue
        out[n] = arbitrage(after, horizon)
    return out


@dataclass
class ScenarioReport:
    scenarios: list  # [{"name", "description", "label", "book": [pnl, se], "flags": {...}}]
    positions: list  # [{"name", "group", "pnl": {scenario: [v, se]}}]
    grid: dict | None
    groups: list
    version: int | None
    settings: dict
    seconds: float

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def run(
    groups: list,
    scenarios: list,
    settings: RiskSettings | None = None,
    env: RiskEnv | None = None,
    spot_vol_grid: bool = True,
    spots=(-0.30, -0.20, -0.10, -0.05, 0.0, 0.05, 0.10),
    vols=(-0.05, 0.0, 0.05, 0.10, 0.15),
    version: int | None = None,
) -> ScenarioReport:
    t0 = time.perf_counter()
    settings = settings or RiskSettings()
    env = env or RiskEnv()
    shocks = {f"sc:{s.name}": s.shock for s in scenarios}
    if spot_vol_grid:
        for a in spots:
            for b in vols:
                if a or b:
                    shocks[f"grid:{a:+.2f}:{b:+.2f}"] = Shock(
                        spot={"*": a} if a else {}, vol={"*": VolMove(b)} if b else {}
                    )
    book_v, book_var, positions, rows, flags = {}, {}, [], [], {s.name: {} for s in scenarios}
    for g in groups:
        for s in scenarios:
            flags[s.name].update({f"{g.label}:{k}": v for k, v in _arbitrage_flags(g.ctx, s.shock, env).items()})
        r = run_group_safe(g, shocks, None, settings, env)
        for i, name in enumerate(r.names):
            positions.append(
                {
                    "name": name,
                    "group": g.label,
                    "value": r.values["base"][i],
                    "pnl": {k: [r.pnl[k][0][i], r.pnl[k][1][i]] for k in shocks},
                }
            )
        for k in shocks:
            book_v[k] = book_v.get(k, 0.0) + r.pnl[k][2]
            book_var[k] = book_var.get(k, 0.0) + r.pnl[k][3] ** 2
        rows.append(
            {
                "label": g.label,
                "method": r.method,
                "model": r.model,
                "n_paths": r.n_paths,
                "simulations": r.simulations,
                "seconds": r.seconds,
                "warnings": r.warnings,
            }
        )
    out = []
    for s in scenarios:
        k = f"sc:{s.name}"
        bad = [
            n
            for n, f in flags[s.name].items()
            if isinstance(f, dict) and not (f.get("butterfly_free", True) and f.get("calendar_free", True))
        ]
        out.append(
            {
                "name": s.name,
                "description": s.description,
                "label": s.label,
                "shock": s.shock.to_dict(),
                "book": [book_v[k], float(np.sqrt(book_var[k]))],
                "flags": flags[s.name],
                "arbitrage": bad,
            }
        )
    grid = None
    if spot_vol_grid:
        cells = [
            [
                [0.0, 0.0]
                if not (a or b)
                else [book_v[f"grid:{a:+.2f}:{b:+.2f}"], float(np.sqrt(book_var[f"grid:{a:+.2f}:{b:+.2f}"]))]
                for b in vols
            ]
            for a in spots
        ]
        grid = {"spots": list(spots), "vols": list(vols), "pnl": cells, "rule": "sticky_moneyness"}
    for p in positions:
        p["pnl"] = {k[3:]: v for k, v in p["pnl"].items() if k.startswith("sc:")} | (
            {"grid": {k[5:]: v for k, v in p["pnl"].items() if k.startswith("grid:")}} if spot_vol_grid else {}
        )
    return ScenarioReport(out, positions, grid, rows, version, settings.to_dict(), time.perf_counter() - t0)


__all__ = ["Scenario", "defaults", "load", "save", "history", "run", "ScenarioReport", "STARTING"]
