"""Greeks of a book as shocks and measures: every Greek is a combination of revaluations on the base's random numbers.

Desk cash conventions, in the payment currency, from the desk's side (the positions' signed quantities):
- delta: value change for +1 % of a spot, central (V(+h) - V(-h)) / 2h x 1 %; its underlying equivalent is delta / (1 % spot)
  units, or delta / (1 % |q| N) of the notional; sticky moneyness (the model's own), sticky strike, and realistic (the
  at-the-money vols follow the measured spot-vol betas) side by side;
- gamma: change of that delta for +1 %, (V(+h) - 2 V + V(-h)) (1 % / h)^2, at h = 1 % and at h = 5 % (gamma_5, same unit);
- vega: per +1 implied vol point, central; per +1 % of the vols (vega_relative); per tenor and per node of the grid
  (one-sided tents, `grid`), net (signed sum), gross (sum of the absolute nodes) and weighted (sum_T vega_T sqrt(1 y / T),
  also revalued directly as one shock, vega_weighted_direct); volga = change of vega per point, vanna = change of delta per
  point; skew vega per +1 point of the 90-110 % rotation at one year;
- theta: one business day of roll, market frozen; split into carry (the spot not drifting to its forward), events (cash of the
  day less its value the day before) and decay (the rest: time value, discounting, and what the day's fixings settle);
- rho (rate: discount and forward), rho_discount, rho_forward, epsilon (dividend yield), repo: per +1 bp, central at
  `rate_bp` and scaled, in total and per tenor (one-sided);
- correlation: per +1 point, every pair (central) and each pair (one-sided); cross gamma: change of the delta of one name for
  +1 % of another; quanto: per +1 point of the name-FX correlation and of the FX vol.

A Greek whose size is under twice its standard error is flagged as within the noise.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field

import numpy as np

from optlab.risk import grid
from optlab.risk.curves import CurveMove
from optlab.risk.engine import Measure, RiskSettings, run_group
from optlab.risk.shocks import RiskEnv, Shock, VolMove, context_currency
from optlab.structured.multiasset import BasketContext
from optlab.structured.units import UNITS as STRUCTURED_UNITS

UNITS = {
    "value": "currency (payment currency), desk side",
    "delta": "currency per +1 % spot",
    "delta_units": "units of the underlying (model scale: spot 100)",
    "delta_notional": "% of the position's notional",
    "gamma": "currency per +1 % spot, per +1 % spot (change of the cash delta)",
    "vega": "currency per +1 implied vol point",
    "vega_relative": "currency per +1 % of every implied vol",
    "vega_weighted": "currency per +1 vol point x sqrt(1 y / T)",
    "volga": "currency per +1 vol point, per +1 vol point",
    "vanna": "currency per +1 % spot, per +1 vol point",
    "skew_vega": "currency per +1 vol point of the 90-110 % skew at one year",
    "theta": "currency per business day (roll, market frozen)",
    "rho": "currency per +1 bp",
    "corr": "currency per +1 correlation point",
    "cross_gamma": "currency per +1 % of the first name, per +1 % of the second (change of its cash delta)",
    "quanto": "currency per +1 correlation point (name-FX) or per +1 FX vol point",
    "stderr": STRUCTURED_UNITS["stderr"],
}


@dataclass
class Bumps:
    spot: float = 0.01
    spot_large: float = 0.05
    vol: float = 0.01
    vol_relative: float = 0.01
    rate_bp: float = 10.0
    corr: float = 0.01
    skew: float = 0.01
    roll_days: int = 1

    @classmethod
    def from_dict(cls, d: dict | None) -> Bumps:
        d = dict(d or {})
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class GreekOptions:
    kxt: str = "single"  # node vegas: "all" groups, "single"-name groups only, or "none" (tenor buckets only)
    curves: bool = True  # rho, epsilon and repo per tenor
    sticky_strike: bool = True
    realistic: bool = True  # needs RiskEnv.betas
    cross: bool = True  # vanna, volga, cross gamma, correlation
    theta: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> GreekOptions:
        d = dict(d or {})
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def _central(name, up, down, scale):
    return Measure(name, {up: 0.5 * scale, down: -0.5 * scale})


def _second(name, up, down, scale):
    return Measure(name, {up: scale, "base": -2.0 * scale, down: scale})


def _one_sided(name, shock, scale):
    return Measure(name, {shock: scale, "base": -scale})


def _cross(name, pp, pm, mp, mm, scale):
    return Measure(name, {pp: 0.25 * scale, pm: -0.25 * scale, mp: -0.25 * scale, mm: 0.25 * scale})


def _merge(name, measures, coef=1.0):
    w: dict = {}
    for m in measures:
        for s, x in m.weights.items():
            w[s] = w.get(s, 0.0) + coef * m.scale * x
    return Measure(name, w)


def plan(
    ctx,
    bumps: Bumps | None = None,
    options: GreekOptions | None = None,
    env: RiskEnv | None = None,
    names=None,
    roll_spots: dict | None = None,
) -> tuple[dict, list]:
    """(shocks, measures) of every Greek of one group's context. Measure names are "greek|name|bucket"."""
    b = bumps or Bumps()
    o = options or GreekOptions()
    env = env or RiskEnv()
    basket = isinstance(ctx, BasketContext)
    names = list(names or (ctx.names if basket else (ctx.underlying,)))
    cur = context_currency(ctx)
    S: dict = {}
    M: list = []
    tenor_name: dict = {}
    kxt = o.kxt == "all" or (o.kxt == "single" and not basket)
    for n in names:
        _plan_spot(S, M, n, b, o, env)
        _plan_vol(S, M, n, b, o, kxt, tenor_name)
        _plan_carry(S, M, n, b, o, env)
    _plan_totals(M, names, tenor_name)
    _plan_rates(S, M, cur, b, o)
    if basket and o.cross and len(names) > 1:
        _plan_correlation(S, M, names, b)
    if o.theta:
        S["roll"] = Shock(roll_days=b.roll_days)
        M.append(_one_sided("theta|*|", "roll", 1.0))
        if roll_spots:
            S["roll@forward"] = Shock(roll_days=b.roll_days, spot=dict(roll_spots))
            M.append(Measure("theta_carry|*|", {"roll": 1.0, "roll@forward": -1.0}))
    return S, M


def _plan_spot(S: dict, M: list, n: str, b: Bumps, o: GreekOptions, env: RiskEnv) -> None:
    """Delta and gammas of a name, and the deltas under the other vol rules."""
    h, H = b.spot, b.spot_large
    unit_s = 0.01 / h
    S[f"s+|{n}"], S[f"s-|{n}"] = Shock(spot={n: h}), Shock(spot={n: -h})
    S[f"S+|{n}"], S[f"S-|{n}"] = Shock(spot={n: H}), Shock(spot={n: -H})
    M += [
        _central(f"delta|{n}|", f"s+|{n}", f"s-|{n}", unit_s),
        _second(f"gamma|{n}|", f"s+|{n}", f"s-|{n}", unit_s**2),
        _second(f"gamma_5|{n}|", f"S+|{n}", f"S-|{n}", (0.01 / H) ** 2),
    ]
    if o.sticky_strike:
        S[f"ss+|{n}"] = Shock(spot={n: h}, rule="sticky_strike")
        S[f"ss-|{n}"] = Shock(spot={n: -h}, rule="sticky_strike")
        M.append(_central(f"delta_sticky_strike|{n}|", f"ss+|{n}", f"ss-|{n}", unit_s))
    if o.realistic and n in env.betas:
        S[f"rl+|{n}"] = Shock(spot={n: h}, rule="realistic")
        S[f"rl-|{n}"] = Shock(spot={n: -h}, rule="realistic")
        M.append(_central(f"delta_realistic|{n}|", f"rl+|{n}", f"rl-|{n}", unit_s))


def _plan_vol(S: dict, M: list, n: str, b: Bumps, o: GreekOptions, kxt: bool, tenor_name: dict) -> None:
    """Vegas of a name: parallel, relative, volga and vanna, skew, by node or by tenor, net and weighted."""
    h, v, rv = b.spot, b.vol, b.vol_relative
    unit_s, unit_v = 0.01 / h, 0.01 / v
    S[f"v+|{n}"], S[f"v-|{n}"] = Shock(vol={n: VolMove(v)}), Shock(vol={n: VolMove(-v)})
    S[f"vr+|{n}"], S[f"vr-|{n}"] = Shock(vol={n: VolMove(relative=rv)}), Shock(vol={n: VolMove(relative=-rv)})
    M += [
        _central(f"vega|{n}|", f"v+|{n}", f"v-|{n}", unit_v),
        _central(f"vega_relative|{n}|", f"vr+|{n}", f"vr-|{n}", 0.01 / rv),
    ]
    if o.cross:
        M.append(_second(f"volga|{n}|", f"v+|{n}", f"v-|{n}", unit_v**2))
        for a, sa in (("+", h), ("-", -h)):
            for c, sv in (("+", v), ("-", -v)):
                S[f"x{a}{c}|{n}"] = Shock(spot={n: sa}, vol={n: VolMove(sv)})
        M.append(_cross(f"vanna|{n}|", f"x++|{n}", f"x+-|{n}", f"x-+|{n}", f"x--|{n}", unit_s * unit_v))
    S[f"k+|{n}"], S[f"k-|{n}"] = Shock(vol={n: VolMove(skew=b.skew)}), Shock(vol={n: VolMove(skew=-b.skew)})
    M.append(_central(f"skew_vega|{n}|", f"k+|{n}", f"k-|{n}", 0.01 / b.skew))
    tenors = []
    for j, label in enumerate(grid.TENOR_LABELS):
        if kxt:
            nodes = []
            for i, klabel in enumerate(grid.STRIKE_LABELS):
                key = f"n{i},{j}|{n}"
                S[key] = Shock(vol={n: VolMove(nodes={(i, j): v})})
                m = _one_sided(f"vega_node|{n}|{klabel}@{label}", key, unit_v)
                M.append(m)
                nodes.append(m)
            tm = _merge(f"vega_tenor|{n}|{label}", nodes)
        else:
            key = f"t{j}|{n}"
            S[key] = Shock(vol={n: VolMove(term={j: v})})
            tm = _one_sided(f"vega_tenor|{n}|{label}", key, unit_v)
        M.append(tm)
        tenors.append(tm)
        tenor_name.setdefault(label, []).append(tm)
    M.append(_merge(f"vega_net|{n}|", tenors))
    wm = Measure(f"vega_weighted|{n}|", {})
    for j, tm in enumerate(tenors):
        for s_, x in tm.weights.items():
            wm.weights[s_] = wm.weights.get(s_, 0.0) + x * tm.scale * float(grid.weighted_factor(grid.TENORS[j]))
    M.append(wm)
    S[f"w|{n}"] = Shock(
        vol={n: VolMove(term={j: v * float(grid.weighted_factor(T)) for j, T in enumerate(grid.TENORS)})}
    )
    M.append(_one_sided(f"vega_weighted_direct|{n}|", f"w|{n}", unit_v))


def _plan_carry(S: dict, M: list, n: str, b: Bumps, o: GreekOptions, env: RiskEnv) -> None:
    """Dividends and repo of a name (by tenor with `curves`), and its quanto correlation and FX vega."""
    unit_v = 0.01 / b.vol
    S[f"q+|{n}"], S[f"q-|{n}"] = (
        Shock(dividend={n: CurveMove(b.rate_bp)}),
        Shock(dividend={n: CurveMove(-b.rate_bp)}),
    )
    S[f"p+|{n}"], S[f"p-|{n}"] = Shock(repo={n: CurveMove(b.rate_bp)}), Shock(repo={n: CurveMove(-b.rate_bp)})
    M += [
        _central(f"epsilon|{n}|", f"q+|{n}", f"q-|{n}", 1 / b.rate_bp),
        _central(f"repo|{n}|", f"p+|{n}", f"p-|{n}", 1 / b.rate_bp),
    ]
    if o.curves:
        for j, label in enumerate(grid.TENOR_LABELS):
            S[f"qt{j}|{n}"] = Shock(dividend={n: CurveMove(tenors={j: b.rate_bp})})
            M.append(_one_sided(f"epsilon_tenor|{n}|{label}", f"qt{j}|{n}", 1 / b.rate_bp))
    if n in env.quanto:
        S[f"qc+|{n}"], S[f"qc-|{n}"] = Shock(quanto_corr={n: b.corr}), Shock(quanto_corr={n: -b.corr})
        S[f"fv+|{n}"], S[f"fv-|{n}"] = Shock(fx_vol={n: b.vol}), Shock(fx_vol={n: -b.vol})
        M += [
            _central(f"quanto_corr|{n}|", f"qc+|{n}", f"qc-|{n}", 0.01 / b.corr),
            _central(f"fx_vega|{n}|", f"fv+|{n}", f"fv-|{n}", unit_v),
        ]


def _plan_totals(M: list, names: list, tenor_name: dict) -> None:
    """Every name together, for a group of several names."""
    for g in (
        "delta",
        "gamma",
        "gamma_5",
        "vega",
        "vega_relative",
        "skew_vega",
        "vega_net",
        "vega_weighted",
        "epsilon",
    ):
        parts = [m for m in M if m.name.split("|")[0] == g and m.name.split("|")[1] in names]
        if len(names) > 1 and parts:
            M.append(_merge(f"{g}|*|", parts))
    for label, parts in tenor_name.items():
        if len(names) > 1:
            M.append(_merge(f"vega_tenor|*|{label}", parts))


def _plan_rates(S: dict, M: list, cur: str, b: Bumps, o: GreekOptions) -> None:
    """The rates of the payment currency: rho, its discount and forward parts, by tenor with `curves`."""
    rb = b.rate_bp
    for kind, field_ in (("rho", "rates"), ("rho_discount", "discount"), ("rho_forward", "forward_rate")):
        S[f"{kind}+"], S[f"{kind}-"] = Shock(**{field_: {cur: CurveMove(rb)}}), Shock(**{field_: {cur: CurveMove(-rb)}})
        M.append(_central(f"{kind}|{cur}|", f"{kind}+", f"{kind}-", 1 / rb))
    if o.curves:
        for j, label in enumerate(grid.TENOR_LABELS):
            S[f"rt{j}"] = Shock(rates={cur: CurveMove(tenors={j: rb})})
            M.append(_one_sided(f"rho_tenor|{cur}|{label}", f"rt{j}", 1 / rb))


def _plan_correlation(S: dict, M: list, names: list, b: Bumps) -> None:
    """The correlation of a basket: parallel, by pair, and the cross gammas."""
    h = b.spot
    unit_s = 0.01 / h
    S["c+"], S["c-"] = Shock(corr=b.corr), Shock(corr=-b.corr)
    M.append(_central("corr|*|", "c+", "c-", 0.01 / b.corr))
    for a_i, a in enumerate(names):
        for bb in names[a_i + 1 :]:
            pair = f"{a}|{bb}"
            S[f"c|{pair}"] = Shock(corr_pairs={pair: b.corr})
            M.append(_one_sided(f"corr|{a}~{bb}|", f"c|{pair}", 0.01 / b.corr))
            for sa, xa in (("+", h), ("-", -h)):
                for sb, xb in (("+", h), ("-", -h)):
                    S[f"g{sa}{sb}|{pair}"] = Shock(spot={a: xa, bb: xb})
            M.append(
                _cross(f"cross_gamma|{a}~{bb}|", f"g++|{pair}", f"g+-|{pair}", f"g-+|{pair}", f"g--|{pair}", unit_s**2)
            )


def _forward_drift(ctx, names, days: int) -> dict:
    """{name: F(dt) / S - 1}: where the spot goes over the roll if it follows its forward."""
    from optlab.risk.shocks import roll_date
    from optlab.structured.dates import year_fraction

    dt_ = year_fraction(ctx.valuation, roll_date(ctx.valuation, days))
    markets = ctx.markets if isinstance(ctx, BasketContext) else {names[0]: ctx.market}
    return {n: float(markets[n].F(dt_) / markets[n].spot - 1.0) for n in names}


@dataclass
class RiskReport:
    """Greeks per position, per name and for the book, with standard errors, as JSON-ready data."""

    positions: list
    book: dict
    gross: dict
    groups: list
    bumps: dict
    options: dict
    settings: dict
    seconds: float
    units: dict = field(default_factory=lambda: dict(UNITS))
    grid: dict = field(default_factory=grid.grid_json)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def get(self, key: str, position: str | None = None):
        """(value, se) of a measure ("delta|SPX|") for the book or one position."""
        if position is None:
            return tuple(self.book[key])
        row = next(p for p in self.positions if p["name"] == position)
        return tuple(row["greeks"][key])


def _noise(v, se) -> bool:
    return se is not None and se > 0 and abs(v) < 2 * se


def compute(
    groups: list,
    bumps: Bumps | None = None,
    options: GreekOptions | None = None,
    settings: RiskSettings | None = None,
    env: RiskEnv | None = None,
) -> RiskReport:
    """Every Greek of every group, aggregated per position, per name and for the book."""
    from optlab.risk.shocks import roll_date
    from optlab.structured.dates import year_fraction

    t0 = time.perf_counter()
    bumps = bumps or Bumps()
    options = options or GreekOptions()
    settings = settings or RiskSettings(smoothing=0.005)
    env = env or RiskEnv()
    positions, book_v, book_var, gross_nodes, group_rows = [], {}, {}, {}, []
    for g in groups:
        ctx = g.ctx
        items = g.book._prepare(ctx)
        used = sorted({n for it in items for n in it["underlyings"]})
        names = [n for n in ctx.names if n in used] if isinstance(ctx, BasketContext) else used
        roll_spots = _forward_drift(ctx, names, bumps.roll_days) if options.theta else None
        shocks, measures = plan(ctx, bumps, options, env, names, roll_spots)
        run = run_group(g, shocks, measures, settings, env)
        spots = {n: float((ctx.markets[n] if isinstance(ctx, BasketContext) else ctx.market).spot) for n in names}
        dt_roll = year_fraction(ctx.valuation, roll_date(ctx.valuation, bumps.roll_days))
        D = ctx.market
        for i, pname in enumerate(run.names):
            greeks = {k: [v[0][i], v[1][i]] for k, v in run.measures.items()}
            q, N = run.quantities[i], run.notionals[i]
            for n in names:
                d = greeks.get(f"delta|{n}|")
                if d:
                    greeks[f"delta_units|{n}|"] = [d[0] / (0.01 * spots[n]), d[1] / (0.01 * spots[n])]
                    size = abs(q) * N
                    greeks[f"delta_notional|{n}|"] = (
                        [100 * d[0] / (0.01 * size), 100 * d[1] / (0.01 * size)] if size else [0.0, 0.0]
                    )
            theta = None
            if "roll" in run.pnl:
                cash_roll = run.cash.get("roll", [{}] * len(run.names))[i]
                cash_base = run.cash.get("base", [{}] * len(run.names))[i]
                C = sum(a for t, a in cash_roll.items() if -dt_roll - 1e-9 < t <= 1e-12)
                W = sum(float(D.D(t)) * a for t, a in cash_base.items() if 1e-12 < t <= dt_roll + 1e-9)
                th, th_se = run.pnl["roll"][0][i], run.pnl["roll"][1][i]
                carry = greeks.get("theta_carry|*|", [0.0, 0.0])
                theta = {
                    "theta": th,
                    "theta_se": th_se,
                    "carry": carry[0],
                    "carry_se": carry[1],
                    "events": C - W,
                    "events_cash": C,
                    "events_expected": W,
                    "decay": th - carry[0] - (C - W),
                }
            noisy = sorted(k for k, (v, se) in greeks.items() if _noise(v, se))
            positions.append(
                {
                    "name": pname,
                    "group": g.label,
                    "quantity": q,
                    "notional": N,
                    "value": run.values["base"][i],
                    "value_se": run.base_se[i],
                    "greeks": greeks,
                    "theta": theta,
                    "noise": noisy,
                    "smoothing_bias": None
                    if run.smoothing_bias is None
                    else [run.smoothing_bias[0][i], run.smoothing_bias[1][i]],
                }
            )
        for k, v in run.measures.items():
            book_v[k] = book_v.get(k, 0.0) + v[2]
            book_var[k] = book_var.get(k, 0.0) + v[3] ** 2
        for n in names:
            if f"delta|{n}|" in run.measures:
                for k, unit in (("delta_units", 0.01 * spots[n]),):
                    key = f"{k}|{n}|"
                    book_v[key] = book_v.get(key, 0.0) + run.measures[f"delta|{n}|"][2] / unit
                    book_var[key] = book_var.get(key, 0.0) + (run.measures[f"delta|{n}|"][3] / unit) ** 2
        book_v["value||"] = book_v.get("value||", 0.0) + float(sum(run.values["base"]))
        book_var["value||"] = book_var.get("value||", 0.0) + run.base_total_se**2
        for k, v in run.measures.items():
            if k.startswith("vega_node|") or (k.startswith("vega_tenor|") and not k.startswith("vega_tenor|*")):
                gross_nodes[k] = gross_nodes.get(k, 0.0) + v[2]
        group_rows.append(
            {
                "label": g.label,
                "method": run.method,
                "model": run.model,
                "currency": run.currency,
                "n_paths": run.n_paths,
                "seed": run.seed,
                "simulations": run.simulations,
                "seconds": run.seconds,
                "shocks": len(shocks),
                "warnings": run.warnings,
                "names": names,
                "smoothing_bias": None if run.smoothing_bias is None else run.smoothing_bias[2:],
            }
        )
    book = {k: [v, float(np.sqrt(book_var[k]))] for k, v in book_v.items()}
    gross = {}
    for k, v in gross_nodes.items():
        kind, name, _ = k.split("|")
        key = f"{kind}_gross|{name}|"
        gross[key] = gross.get(key, 0.0) + abs(v)
    return RiskReport(
        positions,
        book,
        gross,
        group_rows,
        dataclasses.asdict(bumps),
        dataclasses.asdict(options),
        settings.to_dict(),
        time.perf_counter() - t0,
    )
