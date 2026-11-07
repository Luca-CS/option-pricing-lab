"""End-of-day risk runs and the explained P&L: a cascade of full revaluations from one close to the next, against the Greeks.

EOD run of a date d: the trades dealt on or before d, each with its real closes as fixings, on the lake's market of d (one
surface layer, the reconstructed one by default, as in the replay), in groups (one per single name and payment currency, the
worst-ofs together), each on a seed fixed by its label. The run stores per trade its value, its SE and its Greeks, with the
code version, the parameters, the seeds and the market's origin. A run asked again with the same key (date, trades and their
versions, settings, layer, code) is read back: the backfill is idempotent.

Cascade from d-1 to d, on d-1's seeds and in d-1's model scale (spot 100 at d-1), each step adding one part of d's market:
    time       valuation d, d-1's market frozen (in remaining maturity), the frozen spot as the fixing of the days crossed,
               what they pay counted as cash;
    spot       the spot of d (the fixings of the crossed days become the real closes), d-1's forward shape;
    vol        d-1's surface with d's at-the-money vols per tenor (tenor tents);
    skew       d's whole surface (skew and shape);
    rates      d's discount curve, the forward following the rate;
    dividends  d's forward (dividends, repo, quanto drift);
    corr       d's correlations (worst-ofs);
    fx         the value of a group paying in another currency in the reporting one (none in a one-currency book).
The steps add up to the value of d plus the cash, less the value of d-1: the total P&L, to the cent. New trades (their value
less the client price on the day they are dealt), events (cash, calls, knock-ins) and the release of reserves are separate
lines. The prediction by the Greeks of d-1 (theta, delta and gamma, vega per tenor with vanna and volga, skew vega, rho and
epsilon per tenor, correlation) applied to the observed moves gives each step's explained part; the rest is unexplained.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import time
import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from optlab.core.config import DEFAULT_SEED
from optlab.risk import grid
from optlab.risk.engine import RiskGroup, RiskSettings, run_group
from optlab.risk.greeks import Bumps, GreekOptions, compute
from optlab.risk.shocks import RiskEnv, context_currency
from optlab.risk.store import RiskStore, code_version, run_key
from optlab.risk.surfaces import TentBumpedSurface
from optlab.structured.calendars import CALENDARS
from optlab.structured.multiasset import BasketContext
from optlab.structured.portfolio import Book
from optlab.structured.reserves import remaining

STEPS = ("time", "spot", "vol", "skew", "rates", "dividends", "corr", "fx")
EOD_GREEKS = GreekOptions(kxt="none", curves=True, sticky_strike=False, realistic=False, cross=True, theta=True)


def group_seed(label: str) -> int:
    """A seed fixed by the group's label, so that a group keeps its random numbers from one day to the next."""
    return DEFAULT_SEED + zlib.crc32(label.encode()) % 100_000


# --- the book at a date ---


def _maturity(trade) -> dt.date:
    from optlab.structured.compiler import compile_sheet

    return compile_sheet(trade.term_sheet(), trade.strike_date).maturity


def alive(trades, day) -> list:
    """Trades dealt on or before `day` whose last payment is not before it (term sheets only)."""
    out = []
    for t in trades:
        if t.term_sheet() is None or t.trade_date > day or t.status == "cancelled":
            continue
        if _maturity(t) + dt.timedelta(days=10) < day:
            continue
        out.append(t)
    return sorted(out, key=lambda t: t.trade_id)


def real_fixings(lake, trade, day) -> dict:
    """{name: {date: real close}} of a trade from its strike date to `day` (a day without a close takes the last one)."""
    from optlab.marketdata.replay import filled_fixings

    sheet = trade.term_sheet()
    levels, _ = filled_fixings(lake, sheet.underlyings, trade.strike_date, day, sheet.get("general.calendar"))
    return levels


@dataclass
class BookDay:
    day: dt.date
    groups: list  # RiskGroup
    scales: dict  # {name: real spot / model spot} of the day's markets
    trades: dict  # {trade_id: Trade}
    fixings: dict  # {trade_id: {name: {date: real close}}}
    provenance: dict = field(default_factory=dict)


def book_at(lake, trades, day, layer: str | None = "reconstructed") -> BookDay:
    """The book of a date on the lake's market of that date."""
    from optlab.marketdata.pit import basket_at, context_at

    day = pd.Timestamp(day).date()
    live = alive(trades, day)
    singles, baskets = {}, {}
    for t in live:
        sheet = t.term_sheet()
        pay = sheet.payment_currency
        if len(sheet.underlyings) > 1:
            baskets.setdefault(pay, []).append(t)
        else:
            singles.setdefault((sheet.underlyings[0], pay), []).append(t)
    groups, scales, fixings, prov = [], {}, {}, {}

    def positions(ts, scale_of):
        out = []
        for t in ts:
            real = real_fixings(lake, t, day)
            fixings[t.trade_id] = real
            out.append(t.to_position({n: {d: v / scale_of[n] for d, v in lv.items()} for n, lv in real.items()}))
        return out

    for (name, pay), ts in sorted(singles.items(), key=lambda x: (x[0][0] != "SPX", x[0][0], str(x[0][1]))):
        ctx, snap = context_at(lake, name, day, layer=layer, payment=pay)
        scales[name] = snap.spot_scale
        prov[name] = snap.provenance
        label = name if pay in (None, ctx.payment_currency) else f"{name} ({pay})"
        groups.append(RiskGroup(label, Book(positions(ts, {name: snap.spot_scale})), ctx, group_seed(label)))
    for pay, ts in sorted(baskets.items(), key=lambda x: str(x[0])):
        names = sorted({n for t in ts for n in t.term_sheet().underlyings}, key=lambda n: (n != "SPX", n))
        ctx, snaps, meta = basket_at(lake, names, day, layer=layer, payment=pay)
        sc = {n: s.spot_scale for n, s in snaps.items()}
        for n, s in snaps.items():
            scales.setdefault(n, s.spot_scale)
            prov.setdefault(n, s.provenance)
        label = "worst-of" if len(baskets) == 1 else f"worst-of ({pay})"
        groups.append(RiskGroup(label, Book(positions(ts, sc)), ctx, group_seed(label)))
        prov[label] = {"correlation": meta}
    return BookDay(day, groups, scales, {t.trade_id: t for t in live}, fixings, prov)


# --- EOD run ---


def _trade_key(t) -> dict:
    return {"id": t.trade_id, "version": t.version, "status": t.status, "quantity": t.quantity}


def eod(
    lake,
    trades,
    day,
    store: RiskStore | None = None,
    settings: RiskSettings | None = None,
    layer: str = "reconstructed",
    greeks: bool = True,
    bumps: Bumps | None = None,
    reuse: bool = True,
    env: RiskEnv | None = None,
) -> dict:
    """Value and Greeks of the book at the close of `day`, stored. A stored run with the same key is returned as it is."""
    t0 = time.perf_counter()
    day = pd.Timestamp(day).date()
    settings = settings or RiskSettings(smoothing=0.0)
    bumps = bumps or Bumps()
    live = alive(trades, day)
    code = code_version()
    params = {
        "settings": settings.to_dict(),
        "layer": layer,
        "greeks": greeks,
        "bumps": dataclasses.asdict(bumps),
        "options": dataclasses.asdict(EOD_GREEKS),
    }
    key = run_key(kind="eod", day=day.isoformat(), trades=[_trade_key(t) for t in live], params=params, code=code)
    if store is not None and reuse:
        got = store.find_run(key)
        if got is not None:
            return {**got["payload"], "run_id": got["run_id"], "reused": True}
    bd = book_at(lake, trades, day, layer)
    positions, groups = [], []
    report = None
    if greeks and bd.groups:
        report = compute(
            bd.groups, bumps, EOD_GREEKS, dataclasses.replace(settings, smoothing=settings.smoothing or 0.005), env
        )
    for g in bd.groups:
        r = run_group(g, {}, None, settings, env)
        groups.append(
            {
                "label": g.label,
                "seed": g.seed,
                "method": r.method,
                "model": r.model,
                "n_paths": r.n_paths,
                "currency": r.currency,
                "value": float(sum(r.values["base"])),
                "value_se": r.base_total_se,
                "positions": r.names,
                "warnings": r.warnings,
            }
        )
        for i, name in enumerate(r.names):
            t = bd.trades[name]
            res = remaining(t, day)
            positions.append(
                {
                    "trade_id": name,
                    "group": g.label,
                    "quantity": t.quantity,
                    "value": r.values["base"][i],
                    "value_se": r.base_se[i],
                    "notional": r.notionals[i],
                    "reserve": res["amount"] if res else None,
                    "greeks": next((p["greeks"] for p in report.positions if p["name"] == name), None)
                    if report
                    else None,
                }
            )
    payload = {
        "day": day.isoformat(),
        "code": code,
        "params": params,
        "positions": positions,
        "groups": groups,
        "value": float(sum(p["value"] for p in positions)),
        "value_se": float(np.sqrt(sum(g["value_se"] ** 2 for g in groups))),
        "greeks": report.book if report else None,
        "provenance": _origins(bd.provenance),
        "trades": [_trade_key(t) for t in live],
        "seconds": time.perf_counter() - t0,
    }
    if store is not None:
        payload["run_id"] = store.save_run("eod", day, key, params, payload, payload["seconds"], code)
    return payload


def _origins(prov: dict) -> dict:
    """What the market of each name was made of (origins and layers only, no level)."""
    out = {}
    for n, p in prov.items():
        if not isinstance(p, dict):
            continue
        out[n] = {
            k: {kk: vv for kk, vv in v.items() if kk in ("origin", "layer", "method", "mode", "curve", "date")}
            for k, v in p.items()
            if isinstance(v, dict)
        }
    return out


def backfill(
    lake,
    trades,
    start,
    end,
    store: RiskStore,
    settings: RiskSettings | None = None,
    calendar: str = "nyse",
    layer: str = "reconstructed",
    greeks: bool = True,
    explain: bool = True,
    log=None,
) -> list:
    """EOD runs over every business day of [start, end], then the cascade of each pair of days; existing identical runs are
    reused."""
    days = CALENDARS[calendar].business_days(pd.Timestamp(start).date(), pd.Timestamp(end).date())
    out = []
    for d in days:
        r = eod(lake, trades, d, store, settings, layer, greeks)
        out.append(
            {"day": d.isoformat(), "run_id": r.get("run_id"), "reused": r.get("reused", False), "value": r["value"]}
        )
        if log:
            log(f"EOD {d}: {'reused' if r.get('reused') else 'computed'} {r.get('run_id')}")
    if explain:
        for a, b in zip(days, days[1:]):
            p = explain_day(lake, trades, a, b, store, settings, layer)
            if log:
                log(f"P&L {a} -> {b}: {'reused' if p.get('reused') else 'computed'} total {p['total']:.2f}")
    return out


# --- the cascade ---


def _rolled_positions(book: Book, fixings_real: dict, scale: dict, a: dt.date, b: dt.date, frozen: bool) -> Book:
    """The positions of d-1 with the fixings of the days in (a, b]: the frozen spot (100 in a's scale) or the real closes."""
    days = [a + dt.timedelta(days=i) for i in range(1, (b - a).days + 1)]
    out = []
    for pos in book.positions:
        fix = {n: dict(v) for n, v in (pos.fixings or {}).items()}
        real = fixings_real.get(pos.name, {})
        for n in fix:
            last = fix[n][max(fix[n])] if fix[n] else 100.0
            for d in days:
                key = d.isoformat()
                if d.weekday() >= 5 and key not in real.get(n, {}):
                    continue
                if frozen:
                    fix[n][key] = float(last if key not in fix[n] else fix[n][key])
                elif key in real.get(n, {}):
                    fix[n][key] = real[n][key] / scale[n]
        out.append(dataclasses.replace(pos, fixings=fix))
    return Book(out)


def _rates_market(ma, mb_in_a, spot):
    """d's discounts with d-1's forward shape moved by the change of the rate: F / S = (F_a / S_a) D_a / D_b."""
    from optlab.core.market import Market
    from optlab.risk.curves import _pillars

    T = np.unique(np.concatenate([_pillars(ma), _pillars(mb_in_a)]))
    Da, Db = np.asarray(ma.D(T)), np.asarray(mb_in_a.D(T))
    return Market(spot, T, Db, spot * np.asarray(ma.F(T)) / ma.spot * Da / Db)


def _atm_move(sa, sb) -> list:
    if np.isscalar(sa) or np.isscalar(sb):
        return (
            [float(sb) - float(sa)] * len(grid.TENORS)
            if np.isscalar(sa) and np.isscalar(sb)
            else [0.0] * len(grid.TENORS)
        )
    T = np.asarray(grid.TENORS)
    return list(np.asarray(sb.iv(np.zeros(len(T)), T)) - np.asarray(sa.iv(np.zeros(len(T)), T)))


def _step_contexts(ga: RiskGroup, gb_ctx, ratio: dict, a: dt.date, b: dt.date) -> dict:
    """{step: context} of one group from d-1's context to d's, in d-1's scale."""
    out = {}
    ctx = ga.ctx
    basket = isinstance(ctx, BasketContext)
    names = list(ctx.names) if basket else [ctx.underlying]
    ma = dict(ctx.markets) if basket else {names[0]: ctx.market}
    sa = dict(ctx.vols) if basket else {names[0]: ctx.surface}
    mb = {n: (gb_ctx.markets[n] if basket else gb_ctx.market) for n in names}
    sb = {n: (gb_ctx.vols[n] if basket else gb_ctx.surface) for n in names}
    mb_a = {n: mb[n].scaled(ma[n].spot * ratio[n]) for n in names}
    corr_a = ctx.corr if basket else None
    corr_b = gb_ctx.corr if basket else None
    stages = {
        "time": (ma, sa, corr_a),
        "spot": ({n: ma[n].scaled(ma[n].spot * ratio[n]) for n in names}, sa, corr_a),
    }
    vol = {n: (sa[n] if np.isscalar(sa[n]) else TentBumpedSurface(sa[n], term=_atm_move(sa[n], sb[n]))) for n in names}
    stages["vol"] = (stages["spot"][0], vol, corr_a)
    stages["skew"] = (stages["spot"][0], sb, corr_a)
    stages["rates"] = ({n: _rates_market(ma[n], mb_a[n], ma[n].spot * ratio[n]) for n in names}, sb, corr_a)
    stages["dividends"] = (mb_a, sb, corr_a)
    stages["corr"] = (mb_a, sb, corr_b)
    for step, (m, s, c) in stages.items():
        if basket:
            contexts = {
                n: dataclasses.replace(ctx.contexts[n], market=m[n], surface=s[n], valuation=b, slv_cache={})
                for n in names
                if n in ctx.contexts
            }
            out[step] = BasketContext(ctx.names, m, s, np.asarray(c), b, contexts, {}, ctx.payment_currency)
        else:
            out[step] = dataclasses.replace(ctx, market=m[names[0]], surface=s[names[0]], valuation=b, slv_cache={})
    return out


def _moves(ga, gb_ctx, ratio, cur) -> dict:
    """Observed factor moves of a group's names, as `var.taylor_pnl` reads them (row of changes)."""
    ctx = ga.ctx
    basket = isinstance(ctx, BasketContext)
    names = list(ctx.names) if basket else [ctx.underlying]
    row = {}
    T = np.asarray(grid.TENORS)
    for n in names:
        row[f"spot:{n}"] = float(np.log(ratio[n]))
        sa = ctx.vols[n] if basket else ctx.surface
        sb = gb_ctx.vols[n] if basket else gb_ctx.surface
        for lab, dv in zip(grid.TENOR_LABELS, _atm_move(sa, sb)):
            row[f"atm:{n}:{lab}"] = float(dv)
        if not np.isscalar(sa) and not np.isscalar(sb):
            k = np.log([0.9, 1.1])
            row[f"skew:{n}"] = float(np.subtract(*sb.iv(k, np.ones(2))) - np.subtract(*sa.iv(k, np.ones(2))))
        ma = ctx.markets[n] if basket else ctx.market
        mb = gb_ctx.markets[n] if basket else gb_ctx.market
        za, zb = ma.zero_rate(T), mb.zero_rate(T)
        ca, cb = ma.carry_rate(T), mb.carry_rate(T)
        for j, lab in enumerate(grid.TENOR_LABELS):
            row[f"rate:{cur}:{lab}"] = float(zb[j] - za[j])
            row[f"div:{n}:{lab}"] = float((zb[j] - cb[j]) - (za[j] - ca[j]))
    if basket:
        for i, a_ in enumerate(names):
            for j in range(i + 1, len(names)):
                row[f"corr:{'|'.join(sorted((a_, names[j])))}"] = float(gb_ctx.corr[i][j] - ctx.corr[i][j])
    return row


def _predict(greeks: dict, row: dict, names, cur, days: int) -> dict:
    """The P&L the Greeks of d-1 predict for each step, from the observed moves."""
    v = lambda k: (greeks.get(k) or [0.0])[0]  # noqa: E731
    out = {s: 0.0 for s in STEPS}
    out["time"] = v("theta|*|") * days
    for n in names:
        x = float(np.expm1(row.get(f"spot:{n}", 0.0))) / 0.01
        out["spot"] += v(f"delta|{n}|") * x + 0.5 * v(f"gamma|{n}|") * x * x
        par = 0.0
        for lab in grid.TENOR_LABELS:
            dv = row.get(f"atm:{n}:{lab}", 0.0) / 0.01
            out["vol"] += v(f"vega_tenor|{n}|{lab}") * dv
            par += dv / len(grid.TENOR_LABELS)
        out["vol"] += v(f"vanna|{n}|") * x * par + 0.5 * v(f"volga|{n}|") * par * par
        out["skew"] += v(f"skew_vega|{n}|") * row.get(f"skew:{n}", 0.0) / 0.01
        for lab in grid.TENOR_LABELS:
            out["dividends"] += v(f"epsilon_tenor|{n}|{lab}") * row.get(f"div:{n}:{lab}", 0.0) / 1e-4
    for lab in grid.TENOR_LABELS:
        out["rates"] += v(f"rho_tenor|{cur}|{lab}") * row.get(f"rate:{cur}:{lab}", 0.0) / 1e-4
    for k, x in row.items():
        if k.startswith("corr:"):
            a_, b_ = k[5:].split("|")
            out["corr"] += (v(f"corr|{a_}~{b_}|") or v(f"corr|{b_}~{a_}|")) * x / 0.01
    return out


def explain_day(
    lake,
    trades,
    a,
    b,
    store: RiskStore | None = None,
    settings: RiskSettings | None = None,
    layer: str = "reconstructed",
    reuse: bool = True,
) -> dict:
    """The explained P&L from the close of `a` to the close of `b` (stored as a "pnl" run)."""
    from optlab.structured.dates import year_fraction
    from optlab.structured.lifecycle import events as lifecycle_events

    t0 = time.perf_counter()
    a, b = pd.Timestamp(a).date(), pd.Timestamp(b).date()
    settings = settings or RiskSettings(smoothing=0.0)
    code = code_version()
    ea = eod(lake, trades, a, store, settings, layer)
    eb = eod(lake, trades, b, store, settings, layer)
    key = run_key(
        kind="pnl",
        a=ea.get("run_id") or a.isoformat(),
        b=eb.get("run_id") or b.isoformat(),
        code=code,
        settings=settings.to_dict(),
        layer=layer,
    )
    if store is not None and reuse:
        got = store.find_run(key)
        if got is not None:
            return {**got["payload"], "run_id": got["run_id"], "reused": True}
    bda, bdb = book_at(lake, trades, a, layer), book_at(lake, trades, b, layer)
    since = -year_fraction(a, b)
    n_days = len(CALENDARS["nyse"].business_days(a + dt.timedelta(days=1), b))
    steps_book = {s: [0.0, 0.0] for s in STEPS}
    pred_book = {s: 0.0 for s in STEPS}
    per_trade: dict = {}
    groups_out = []
    greeks_a = {p["trade_id"]: p["greeks"] for p in ea["positions"]}
    for ga in bda.groups:
        cur = context_currency(ga.ctx)
        gb = next((g for g in bdb.groups if g.label == ga.label), None)
        basket = isinstance(ga.ctx, BasketContext)
        names = list(ga.ctx.names) if basket else [ga.ctx.underlying]
        if gb is None:  # every trade of the group ended: d's market of its names from the lake
            from optlab.marketdata.pit import basket_at, context_at

            if basket:
                gb_ctx, snaps, _ = basket_at(lake, names, b, layer=layer, payment=ga.ctx.payment_currency)
                scale_b = {n: s.spot_scale for n, s in snaps.items()}
            else:
                gb_ctx, snap = context_at(lake, names[0], b, layer=layer, payment=ga.ctx.payment_currency)
                scale_b = {names[0]: snap.spot_scale}
        else:
            gb_ctx = gb.ctx
            scale_b = {n: bdb.scales[n] for n in names}
        ratio = {n: scale_b[n] / bda.scales[n] for n in names}  # real spot of b over real spot of a
        contexts = _step_contexts(ga, gb_ctx, ratio, a, b)
        trades_a = {p.name: bda.fixings[p.name] for p in ga.book.positions}
        real_b = {tid: real_fixings(lake, bda.trades[tid], b) for tid in trades_a}
        scale_a = {n: bda.scales[n] for n in names}
        base = run_group(ga, {}, None, settings, None, keep_pairs=True)
        values = {"base": base.pairs}
        cash = {}
        for step, sctx in contexts.items():
            book = _rolled_positions(ga.book, real_b, scale_a, a, b, frozen=(step == "time"))
            r = run_group(
                RiskGroup(ga.label, book, sctx, ga.seed), {}, None, settings, None, cash_since=since, keep_pairs=True
            )
            values[step] = r.pairs
            cash = {
                nm: sum(x for t_, x in c.items() if since - 1e-9 < t_ <= 1e-12)
                for nm, c in zip(r.names, r.cash.get("base", []))
            }
        values["fx"] = values["corr"]
        names_pos = [p.name for p in ga.book.positions]
        prev = "base"
        for step in STEPS:
            diffs = [np.asarray(x) - np.asarray(y) for x, y in zip(values[step], values[prev])]
            tot = np.sum(diffs, axis=0)
            se = float(tot.std(ddof=1) / np.sqrt(len(tot))) if np.ndim(tot) else 0.0
            steps_book[step][0] += float(np.mean(tot))
            steps_book[step][1] += se**2
            for p, d_ in zip(names_pos, diffs):
                per_trade.setdefault(p, {"steps": {}, "group": ga.label})["steps"][step] = float(np.mean(d_))
            prev = step
        row = _moves(ga, gb_ctx, ratio, cur)
        for p in names_pos:
            g = greeks_a.get(p) or {}
            pr = _predict(g, row, names, cur, n_days)
            per_trade[p]["predicted"] = pr
            for s in STEPS:
                pred_book[s] += pr[s]
            v_a = float(np.mean(values["base"][names_pos.index(p)]))
            v_b = float(np.mean(values["fx"][names_pos.index(p)]))
            t = bda.trades[p]
            ra, rb = remaining(t, a), remaining(t, b)
            ev = []
            try:
                fx_b = {n: {d: v / scale_a[n] for d, v in lv.items()} for n, lv in real_b[p].items()}
                ev = [e for e in lifecycle_events(t, fx_b, b) if a < e.date <= b]
            except KeyError, ValueError:
                pass
            per_trade[p].update(
                {
                    "value_a": v_a,
                    "value_b_and_cash": v_b,
                    "cash": cash.get(p, 0.0),
                    "total": v_b - v_a,
                    "release": (ra["amount"] - rb["amount"]) if ra and rb else None,
                    "events": [
                        {
                            "date": e.date.isoformat(),
                            "kind": e.kind,
                            "amount": None if e.amount is None else t.quantity * e.amount,
                        }
                        for e in ev
                    ],
                }
            )
        stored_b = sum(x["value"] for x in eb["positions"] if x["trade_id"] in names_pos)
        value_b = float(np.sum([np.mean(x) for x in values["fx"]]))
        cash_b = float(sum(cash.get(p, 0.0) for p in names_pos))
        groups_out.append(
            {
                "label": ga.label,
                "seed": ga.seed,
                "value_a": float(np.sum([np.mean(x) for x in values["base"]])),
                "value_b_and_cash": value_b,
                "cash": cash_b,
                "eod_value_b": stored_b,
                "reconciliation_gap": value_b - cash_b - stored_b,
                "moves": {k: v for k, v in row.items()},
            }
        )
    new = []
    for tid, t in bdb.trades.items():
        if tid in bda.trades:
            continue
        pos = next(x for x in eb["positions"] if x["trade_id"] == tid)
        client = (t.reserves or {}).get("client_price")
        # the desk holds q units worth `value` and received q x the client price for them (q < 0: it sold them)
        booking = None if client is None else pos["value"] - t.quantity * client / 100.0 * pos["notional"]
        new.append(
            {
                "trade_id": tid,
                "value": pos["value"],
                "client_price": client,
                "pnl": booking,
                "trade_date": t.trade_date.isoformat(),
            }
        )
    total = float(sum(v[0] for v in steps_book.values()))
    releases = [x["release"] for x in per_trade.values() if x.get("release") is not None]
    payload = {
        "from": a.isoformat(),
        "to": b.isoformat(),
        "steps": {s: [v[0], float(np.sqrt(v[1]))] for s, v in steps_book.items()},
        "predicted": pred_book,
        "unexplained": {s: steps_book[s][0] - pred_book[s] for s in STEPS},
        "total": total,
        "predicted_total": float(sum(pred_book.values())),
        "trades": per_trade,
        "groups": groups_out,
        "new_trades": new,
        "release": float(sum(releases)) if releases else 0.0,
        "eod": [ea.get("run_id"), eb.get("run_id")],
        "order": list(STEPS),
        "business_days": n_days,
        "seconds": time.perf_counter() - t0,
        "code": code,
    }
    if store is not None:
        payload["run_id"] = store.save_run(
            "pnl",
            b,
            key,
            {"from": a.isoformat(), "settings": settings.to_dict(), "layer": layer},
            payload,
            payload["seconds"],
            code,
        )
    return payload


def cumulative(store: RiskStore, start, end) -> dict:
    """Daily and cumulated explained P&L between two dates from the stored runs: per step, per trade, and the curve."""
    runs = store.runs("pnl", start, end)
    days, curve, steps, trades, pred = [], [], {s: 0.0 for s in STEPS}, {}, {s: 0.0 for s in STEPS}
    acc = 0.0
    daily = []
    for r in runs:
        p = store.run(r["run_id"])["payload"]
        if p["from"] < str(start):
            continue
        acc += p["total"]
        days.append(p["to"])
        curve.append(acc)
        daily.append(
            {
                "from": p["from"],
                "to": p["to"],
                "total": p["total"],
                "steps": p["steps"],
                "predicted": p["predicted"],
                "unexplained": p["unexplained"],
                "release": p.get("release", 0.0),
                "new_trades": p["new_trades"],
                "events": {k: v["events"] for k, v in p["trades"].items() if v.get("events")},
            }
        )
        for s in STEPS:
            steps[s] += p["steps"][s][0]
            pred[s] += p["predicted"][s]
        for k, v in p["trades"].items():
            row = trades.setdefault(k, {"total": 0.0, "steps": {s: 0.0 for s in STEPS}, "group": v["group"]})
            row["total"] += v["total"]
            for s in STEPS:
                row["steps"][s] += v["steps"].get(s, 0.0)
    return {
        "from": str(start),
        "to": str(end),
        "days": days,
        "curve": curve,
        "daily": daily,
        "steps": steps,
        "predicted": pred,
        "unexplained": {s: steps[s] - pred[s] for s in STEPS},
        "trades": trades,
        "total": acc,
        "order": list(STEPS),
    }
