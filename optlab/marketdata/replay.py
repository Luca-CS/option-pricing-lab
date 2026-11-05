"""Replay a structured-products book over its life on real fixings: a daily loop of events, value, Greeks, cash and
explained P&L.

Each valuation date prices on `market_at` that date, so only what was known then enters the value. The lifecycle
events come from the product's own script on the real closes. P&L over a step is split between delta, gamma, vega and
time, computed the step before; the residual is published, not hidden. Amounts are in currency for the trade's notional.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import time
from dataclasses import dataclass, field

import pandas as pd

from ..config import DEFAULT_SEED
from ..log import get_logger
from ..structured.calendars import CALENDARS
from ..structured.fastmodels import TabulatedLocalVol
from ..structured.greeks import ShiftedSurface
from ..structured.lifecycle import events as lifecycle_events
from ..structured.multiasset import BasketContext
from ..structured.portfolio import Book
from ..structured.quote import quote
from ..structured.schema import TermSheet
from ..structured.trade import Trade
from .lake import Lake, sql_str
from .pit import basket_at, context_at, real_fixings
from .snapshot import to_model_scale
from .universe import PLACE_CALENDAR, instrument

LOG = get_logger(__name__)


@dataclass(frozen=True)
class ReplayConfig:
    model: str = "lv"  # lv or slv; a daily barrier or a call needs a path model
    n_paths: int = 20_000
    batch: int = 10_000
    seed: int = DEFAULT_SEED
    reval_every: int = 1  # business days between valuations; event and payment days are always valued
    leverage_every: int = 1  # slv: valuations between leverage recalibrations; weekly was off by 0.23 on average (reports/replay.md)
    n_particles: int = 100_000
    greeks: bool = True
    h_spot: float = 0.01
    h_vol: float = 0.01
    fast: bool = True
    as_of: object = None
    surface_layer: str | None = "reconstructed"  # one layer over a life: a switch of layer is not market P&L


def place_calendar(names) -> str:
    places = {PLACE_CALENDAR.get(instrument(n).place, "weekend") for n in names}
    return places.pop() if len(places) == 1 else "weekend"


def struck_sheet(sheet: TermSheet, strike: dt.date) -> TermSheet:
    """The sheet struck on a past date, on the calendar of its underlying."""
    return sheet.replace(**{"general.strike_date": strike, "general.calendar": place_calendar(sheet.underlyings)})


def market_for(lake: Lake, names, day, cfg: ReplayConfig, previous=None, T_max: float = 2.0, recalibrate: bool = True):
    """(context, {name: spot scale}, provenance by name). Under SLV the leverage comes from the last recalibration."""
    if len(names) > 1:
        ctx, snaps, _ = basket_at(lake, list(names), day, cfg.as_of, layer=cfg.surface_layer)
        return ctx, {n: s.spot_scale for n, s in snaps.items()}, {n: s.provenance for n, s in snaps.items()}
    name = names[0]
    if cfg.model == "slv" and recalibrate:
        ctx, snap = context_at(lake, name, day, cfg.as_of, leverage="calibrate", T_max=T_max, n_particles=cfg.n_particles,
                               seed=cfg.seed, layer=cfg.surface_layer)
    else:
        ctx, snap = context_at(lake, name, day, cfg.as_of, layer=cfg.surface_layer)
        if cfg.model == "slv" and previous is not None:
            ctx = dataclasses.replace(ctx, slv=previous.slv, heston=previous.heston)
    return ctx, {name: snap.spot_scale}, {name: snap.provenance}


def strike_trade(lake: Lake, trade_id: str, sheet: TermSheet, strike, quantity: float = 1.0, cfg: ReplayConfig = ReplayConfig(),
                 coupon: float | None = None) -> tuple[Trade, dict]:
    """A trade dealt on a past date: the coupon solved on that date's market unless given, then fixed."""
    strike = pd.Timestamp(strike).date()
    sheet = struck_sheet(sheet, strike)
    t0 = time.perf_counter()
    names = sheet.underlyings
    ctx, scales, prov = market_for(lake, names, strike, cfg, T_max=max(sheet.get("general.maturity_months") / 12, 0.5))
    info = {"strike": strike.isoformat(), "provenance": prov}
    has_coupon = sheet.get("coupon.type") != "none"  # an airbag or a capital-protected note has nothing to solve
    if coupon is None and has_coupon:
        q = quote(sheet, ctx, model=cfg.model, control=None, n_paths=max(cfg.n_paths, 20_000), batch=cfg.batch, seed=cfg.seed)
        coupon, info["coupon_se"], info["policy"] = q.coupon_rate, q.coupon_rate_se, q.decision.label
    info.update(coupon=coupon, seconds=time.perf_counter() - t0)
    fixed = sheet.replace(**{"wrapper.solve": "upfront", **({"coupon.rate": coupon} if has_coupon else {})})
    return Trade.from_term_sheet(trade_id, fixed, quantity, strike, strike), info


def filled_fixings(lake: Lake, names, start, end, calendar, as_of=None) -> tuple[dict, list]:
    """Real closes per name on the business days of the product. A day without a close takes the last one, reported:
    Yahoo misses some real trading days of the Euro Stoxx 50."""
    out, filled = {}, []
    days = CALENDARS[calendar].business_days(start, end)
    for n in names:
        real = real_fixings(lake, n, start, end, as_of)
        levels, last = {}, None
        for d in days:
            key = d.isoformat()
            if key in real:
                last = real[key]
            elif last is not None:
                filled.append((n, key))
            if last is not None:
                levels[key] = last
        out[n] = levels
    return out, filled


def _price(trade: Trade, ctx, cfg: ReplayConfig, scales: dict, fixings: dict, factors=None, surface=None, ctx_override=None):
    model_fix = {n: to_model_scale({n: fixings[n]}, scales[n])[n] for n in fixings}
    book = Book([trade.to_position(model_fix)])
    use = ctx_override or ctx
    kw = {}
    if not isinstance(use, BasketContext):
        if cfg.model == "lv" and cfg.fast:
            kw["sim_model"] = TabulatedLocalVol(surface or use.surface)
        if surface is not None:
            kw["surface"] = surface
    if factors is not None:
        kw["spot_factors"] = factors
    return book.price(use, model=cfg.model, n_paths=cfg.n_paths, seed=cfg.seed, batch=cfg.batch, **kw)


def _greeks(trade, ctx, cfg, scales, fixings, names):
    """Base value and, per name, delta and gamma (per +1 % spot) on one simulation, and vega (per +1 vol point)."""
    h, out = cfg.h_spot, {}
    if not cfg.greeks:
        base = _price(trade, ctx, cfg, scales, fixings)
        return base, out
    if isinstance(ctx, BasketContext):
        factors = [{n: 1.0 for n in names}]
        for n in names:
            factors += [{**factors[0], n: 1 + h}, {**factors[0], n: 1 - h}]
    else:
        factors = [1.0, 1 + h, 1 - h]
    runs = _price(trade, ctx, cfg, scales, fixings, factors)
    base = runs[0]
    for i, n in enumerate(names):
        up, down = runs[1 + 2 * i].value, runs[2 + 2 * i].value
        scale = 0.01 / h
        out[n] = {"delta": (up - down) / 2 * scale, "gamma": (up - 2 * base.value + down) * scale * scale}
        if cfg.model == "lv":
            if isinstance(ctx, BasketContext):
                vu = _price(trade, ctx, cfg, scales, fixings, ctx_override=ctx.bumped(n, vol_shift=cfg.h_vol)).value
                vd = _price(trade, ctx, cfg, scales, fixings, ctx_override=ctx.bumped(n, vol_shift=-cfg.h_vol)).value
            else:
                vu = _price(trade, ctx, cfg, scales, fixings, surface=ShiftedSurface(ctx.surface, cfg.h_vol)).value
                vd = _price(trade, ctx, cfg, scales, fixings, surface=ShiftedSurface(ctx.surface, -cfg.h_vol)).value
            out[n]["vega"] = (vu - vd) / 2 * (0.01 / cfg.h_vol)
    return base, out


def _time_value(trade, ctx, cfg, scales, fixings, names, new_day):
    """Yesterday's market moved to `new_day` with spot and term structure unchanged: yesterday's model spots as fixings."""
    extended = {n: dict(fixings[n]) for n in names}
    prev = max(max(f) for f in fixings.values())
    calendar = CALENDARS[trade.term_sheet().get("general.calendar")]
    for d in calendar.business_days(dt.date.fromisoformat(prev) + dt.timedelta(days=1), new_day):
        for n in names:
            extended[n][d.isoformat()] = fixings[n][max(fixings[n])]
    if isinstance(ctx, BasketContext):
        moved = BasketContext(ctx.names, ctx.markets, ctx.vols, ctx.corr, new_day)
    else:
        moved = dataclasses.replace(ctx, valuation=new_day)
    return _price(trade, moved, cfg, scales, extended, ctx_override=moved).value


@dataclass
class ReplayResult:
    trade: Trade
    daily: pd.DataFrame
    events: list
    cash: list
    reconciliation: dict
    explain: dict
    filled: list
    seconds: dict
    provenance: dict = field(default_factory=dict)
    inception: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"trade_id": self.trade.trade_id, "events": [{"date": e.date.isoformat(), "kind": e.kind, "amount": e.amount,
                                                             "payment": e.payment.isoformat() if e.payment else None}
                                                            for e in self.events],
                "reconciliation": self.reconciliation, "explain": self.explain, "seconds": self.seconds,
                "n_valuations": len(self.daily), "filled_fixings": len(self.filled), "inception": self.inception,
                "provenance": self.provenance}


def result_json(r: ReplayResult) -> dict:
    """Events, flows and P&L as JSON: performances of the fixings rather than levels, which are personal-use data."""
    out = r.summary()
    names = r.trade.term_sheet().underlyings
    d = r.daily
    first = {n: d["spot"].iloc[0][n] for n in names}

    def number(x):
        return None if x is None or x != x else float(x)

    out["underlyings"] = list(names)
    out["sheet"] = r.trade.term_sheet().to_dict(json_safe=True)
    out["daily"] = [{"date": x["date"].isoformat(), "value": float(x["value"]), "se": float(x["value_se"]), "cash": float(x["cash"]),
                     "perf": {n: x["spot"][n] / first[n] for n in names}, "pnl": number(x.get("pnl")),
                     "explain": x.get("explain") if isinstance(x.get("explain"), dict) else None,
                     "residual": number(x.get("residual")), "greeks": x["greeks"], "layer": x["layer"]} for _, x in d.iterrows()]
    return out


def replay(lake: Lake, trade: Trade, end=None, cfg: ReplayConfig = ReplayConfig(), inception: dict | None = None) -> ReplayResult:
    """The loop over a trade's life, up to `end` or the last close in the lake."""
    t_all = time.perf_counter()
    sheet = trade.term_sheet()
    names = sheet.underlyings
    calendar = sheet.get("general.calendar")
    strike = trade.strike_date
    last_data = pd.Timestamp(lake.read("prices_eod", where=f"instrument = {sql_str(names[0])}", columns="max(obs_date) AS d")["d"].iloc[0]).date()
    end = min(pd.Timestamp(end).date() if end is not None else last_data, last_data)
    fixings_all, filled = filled_fixings(lake, names, strike, end, calendar, cfg.as_of)
    known = {n: {d: v for d, v in fixings_all[n].items() if d <= end.isoformat()} for n in names}
    events = lifecycle_events(trade, known, end)
    paid_by_date: dict = {}
    for e in events:
        if e.amount and e.payment:
            paid_by_date[e.payment] = paid_by_date.get(e.payment, 0.0) + trade.quantity * e.amount
    terminal = [e for e in events if e.kind in ("called", "matured")]
    last_day = min(end, max((e.payment or e.date) for e in terminal)) if terminal else end
    all_days = CALENDARS[calendar].business_days(strike, last_day)
    must = {e.date for e in events} | set(paid_by_date) | {strike, last_day}
    schedule = [d for i, d in enumerate(all_days) if i % cfg.reval_every == 0 or d in must]
    seconds = {"market": 0.0, "value": 0.0, "greeks": 0.0, "leverage": 0.0}
    rows = []
    previous = prev_ctx = prev_scales = prev_greeks = prev_value = prev_day = None
    since_leverage = cfg.leverage_every
    cum_cash = 0.0
    T_rem_years = sheet.get("general.maturity_months") / 12
    provenance = {}
    for day in schedule:
        fix = {n: {d: v for d, v in known[n].items() if d <= day.isoformat()} for n in names}
        t0 = time.perf_counter()
        recalibrate = since_leverage >= cfg.leverage_every
        ctx, scales, prov = market_for(lake, names, day, cfg, previous, T_max=max(T_rem_years, 0.5), recalibrate=recalibrate)
        if cfg.model == "slv":
            seconds["leverage" if recalibrate else "market"] += time.perf_counter() - t0
            since_leverage = 1 if recalibrate else since_leverage + 1
        else:
            seconds["market"] += time.perf_counter() - t0
        provenance = prov
        t1 = time.perf_counter()
        base, greeks = _greeks(trade, ctx, cfg, scales, fix, names)
        seconds["greeks"] += time.perf_counter() - t1
        cash = sum(v for d, v in paid_by_date.items() if (prev_day or strike) < d <= day)
        cum_cash += cash
        row = {"date": day, "value": base.value, "value_se": base.stderr, "cash": cash, "cum_cash": cum_cash,
               "spot": {n: fix[n][max(fix[n])] for n in names}, "greeks": greeks, "layer": {n: prov[n]["surface"]["layer"] for n in names}}
        if prev_day is not None:
            pnl = base.value + cash - prev_value
            row["pnl"] = pnl
            t2 = time.perf_counter()
            explain = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "time": 0.0}
            if cfg.greeks and prev_greeks:
                remaining = max((sheet_maturity(sheet) - prev_day).days / 365.0, 1 / 52)
                for n in names:
                    g = prev_greeks[n]
                    move = rows[-1]["spot"][n]
                    ret = row["spot"][n] / move - 1.0
                    explain["delta"] += g["delta"] * ret / 0.01
                    explain["gamma"] += 0.5 * g["gamma"] * (ret / 0.01) ** 2
                    if "vega" in g:
                        new_v = surface_of(ctx, n).iv(0.0, remaining)
                        old_v = surface_of(prev_ctx, n).iv(0.0, remaining)
                        explain["vega"] += g["vega"] * float(new_v - old_v) * 100
                old_fix = {n: {d: v for d, v in known[n].items() if d <= prev_day.isoformat()} for n in names}
                explain["time"] = _time_value(trade, prev_ctx, cfg, prev_scales, old_fix, names, day) + cash - prev_value
                seconds["greeks"] += time.perf_counter() - t2
            row["explain"] = explain
            row["residual"] = pnl - sum(explain.values())
        rows.append(row)
        previous, prev_ctx, prev_scales, prev_greeks, prev_value, prev_day = ctx, ctx, scales, greeks, base.value, day
    daily = pd.DataFrame(rows)
    seconds["total"] = time.perf_counter() - t_all
    seconds["per_valuation"] = seconds["total"] / max(len(rows), 1)
    return ReplayResult(trade, daily, events, sorted(paid_by_date.items()), _reconcile(daily, trade, events, paid_by_date, last_day),
                        _explained(daily), filled, seconds, provenance, inception or {})


def surface_of(ctx, name):
    return ctx.vols[name] if isinstance(ctx, BasketContext) else ctx.surface


def sheet_maturity(sheet: TermSheet) -> dt.date:
    from ..structured.compiler import compile_sheet
    return compile_sheet(sheet, sheet.get("general.strike_date")).maturity


def _reconcile(daily, trade, events, paid_by_date, last_day) -> dict:
    """Cash booked in the loop against the lifecycle's amounts, and total P&L against value and cash."""
    from_events = sum(trade.quantity * e.amount for e in events if e.amount and e.payment and e.payment <= last_day)
    booked = float(daily["cash"].sum())
    pnl = float(daily["pnl"].fillna(0.0).sum()) if "pnl" in daily else 0.0
    final, initial = float(daily["value"].iloc[-1]), float(daily["value"].iloc[0])
    return {"cash_booked": booked, "cash_from_events": from_events, "cash_gap": booked - from_events,
            "total_pnl": pnl, "value_start": initial, "value_end": final,
            "pnl_gap": pnl - (final + booked - initial - float(daily["cash"].iloc[0]))}


def _explained(daily: pd.DataFrame) -> dict:
    if "explain" not in daily or daily["explain"].dropna().empty:
        return {}
    steps = daily.dropna(subset=["pnl"])
    parts = pd.DataFrame(list(steps["explain"]))
    residual = steps["residual"].astype(float)
    pnl = steps["pnl"].astype(float)
    return {"steps": len(steps), "sum": {c: float(parts[c].sum()) for c in parts}, "sum_pnl": float(pnl.sum()),
            "sum_residual": float(residual.sum()), "mean_abs_pnl": float(pnl.abs().mean()),
            "mean_abs_residual": float(residual.abs().mean()),
            "residual_share": float(residual.abs().sum() / max(pnl.abs().sum(), 1e-12)),
            "mean_value_se": float(steps["value_se"].astype(float).mean())}


def replay_book(lake: Lake, trades, end=None, cfg: ReplayConfig = ReplayConfig()) -> dict:
    """Each trade through its life, and the book's value, cash and P&L by date (a trade adds nothing before its strike or
    after its last payment). Trades are valued apart, so errors add as if independent."""
    results = [replay(lake, t, end, cfg) for t in trades]
    frames = []
    for r in results:
        d = r.daily[["date", "value", "value_se", "cash"]].copy()
        d["pnl"] = r.daily["pnl"].astype(float).fillna(0.0) if "pnl" in r.daily else 0.0
        frames.append(d.set_index("date"))
    days = sorted(set().union(*(f.index for f in frames)))
    value = sum(f["value"].reindex(days).ffill().where(pd.Series(days, index=days) <= f.index.max(), 0.0).fillna(0.0) for f in frames)
    cash = sum(f["cash"].reindex(days).fillna(0.0) for f in frames)
    pnl = sum(f["pnl"].reindex(days).fillna(0.0) for f in frames)
    return {"results": results, "book": pd.DataFrame({"value": value, "cash": cash, "pnl": pnl})}


def cost_table(lake: Lake, name: str, day, trade: Trade, n_paths=(20_000, 100_000), particles=(50_000, 100_000, 200_000)) -> dict:
    """What a valuation costs, measured, to choose the frequencies of revaluation and of leverage recalibration."""
    sheet = trade.term_sheet()
    T = max(sheet.get("general.maturity_months") / 12, 0.5)
    day = pd.Timestamp(day).date()
    fix = {n: real_fixings(lake, n, trade.strike_date, day) for n in sheet.underlyings}
    out = {"valuation": {}, "leverage": {}}
    t = time.perf_counter()
    context_at(lake, name, day)
    out["market_seconds"] = time.perf_counter() - t
    for n in n_paths:
        for model in ("lv", "slv"):
            cfg = ReplayConfig(model=model, n_paths=n, greeks=False)
            ctx, scales, _ = market_for(lake, sheet.underlyings, day, cfg, T_max=T, recalibrate=model == "slv")
            t = time.perf_counter()
            r = _price(trade, ctx, cfg, scales, fix)
            out["valuation"][f"{model}_{n}"] = {"seconds": time.perf_counter() - t, "value": r.value, "se": r.stderr}
    for n in particles:
        t = time.perf_counter()
        context_at(lake, name, day, leverage="calibrate", T_max=T, n_particles=n, seed=DEFAULT_SEED)
        out["leverage"][str(n)] = {"seconds": time.perf_counter() - t}
    return out


