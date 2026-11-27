"""The hedged replay: a trade's life on real closes with the desk's hedges, against the same life naked.

Each name's delta is held flat by its index future (the next quarterly expiry, rolled five days before it) or by cash
(units of the index, funded at the government rate plus the issuer's spread and earning the dividends the forward
carries), rebalanced every `delta_every` business days when the residual exceeds `threshold` of the trade's delta. The
vega, with `vega`, is held flat every `vega_every` days by an at-the-money call of `vega_tenor` years valued on the day's
surface (replication), whose delta joins the delta hedge. Every trade pays its half-spread from the cost table.

A step's hedged P&L is the trade's P&L plus the hedges', explained into
    delta      what the spot move does to the trade and the hedges together, at the deltas of the day before;
    gamma      the trade's and the option's gamma terms;
    vega       the trade's and the option's vega terms on the at-the-money move;
    time       the trade's time value (the option's is in the slippage);
    carry      the cash hedge's funding and dividends;
    costs      the half-spreads paid;
    slippage   the rest: the hedge between rebalances, and the higher orders.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from optlab.structured.dates import year_fraction
from optlab.structured.hedges import LinearEnv
from optlab.structured.hedges import costs as hedge_costs
from optlab.structured.hedges.contracts import INDEX_FUTURES, quarterly_expiries
from optlab.structured.hedges.instruments import Future, Vanilla
from optlab.structured.multiasset import BasketContext
from optlab.structured.portfolio import Book, Position

ROLL_DAYS = 5  # a future is rolled this many calendar days before its expiry


@dataclass(frozen=True)
class HedgeConfig:
    instrument: str = "future"  # future or cash
    delta_every: int = 1
    threshold: float = 0.0  # a share of the trade's |delta|: a smaller residual waits
    vega: bool = False
    vega_every: int = 5
    vega_tenor: float = 0.25
    rows: dict = field(default_factory=dict)  # the cost table's hedging rows (structured/hedges/costs)

    def __post_init__(self):
        if self.instrument not in ("future", "cash"):
            raise ValueError("the delta hedge is a future or cash")


def _market(ctx, name):
    return ctx.markets[name] if isinstance(ctx, BasketContext) else ctx.market


class Hedger:
    """The hedges' state along a replay: units per name, the futures held, the option, and what each step made."""

    def __init__(self, cfg: HedgeConfig, names, lake):
        self.cfg, self.names, self.lake = cfg, list(names), lake
        self.rows = hedge_costs.merged(cfg.rows)
        self.units = {n: 0.0 for n in names}
        self.future: dict = {n: None for n in names}
        self.option: Vanilla | None = None
        self.option_q = 0.0
        self.k = 0
        self.prev: dict | None = None

    # --- per-unit levels and sensitivities on a day's market ---
    def _level(self, n, ctx, scale, day) -> float:
        m = _market(ctx, n)
        if self.cfg.instrument == "cash" or n not in INDEX_FUTURES:
            return scale * float(m.spot)
        fut = self.future[n]
        T = max(year_fraction(day, fut.expiry), 0.0) if fut is not None else 0.0
        return scale * float(m.F(T))

    def _unit(self, n) -> float:
        """Currency per index point of one unit: a future's multiplier, or 1 for cash."""
        if self.cfg.instrument == "cash" or n not in INDEX_FUTURES:
            return 1.0
        return INDEX_FUTURES[n].multiplier

    def _option_numbers(self, ctx, scale, day) -> dict:
        """The option's value, delta (per +1 %), gamma and vega (per point) per unit, by replication on the day."""
        if self.option is None or isinstance(ctx, BasketContext):
            return {"value": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "iv": 0.0}
        env = LinearEnv({self.names[0]: scale})
        book = Book([Position("opt", self.option, 1.0)], env)
        h = 0.01
        m = ctx.market
        base = book.price(ctx).value
        up = book.price(ctx, market=m.scaled(m.spot * (1 + h))).value
        down = book.price(ctx, market=m.scaled(m.spot * (1 - h))).value
        g = book.greeks(ctx)
        T = max(year_fraction(day, self.option.expiry), 1 / 365)
        return {
            "value": base,
            "delta": (up - down) / 2,
            "gamma": up - 2 * base + down,
            "vega": float(g["vega"]["total"]) if g.get("vega") else 0.0,
            "iv": float(ctx.surface.iv(0.0, T)),
        }

    def _cost(self, n, d_units, level) -> float:
        if not d_units:
            return 0.0
        if self.cfg.instrument == "cash" or n not in INDEX_FUTURES:
            return abs(d_units) * self.rows["cash_bp"] * 1e-4 * level
        spec = INDEX_FUTURES[n]
        return abs(d_units) * self.rows["future_ticks"] * spec.tick * spec.multiplier

    def _funding(self, day) -> float:
        try:
            from optlab.marketdata.hedges import funding_curve

            return float(funding_curve(self.lake, day).spread(1 / 12))
        except KeyError, ValueError:
            return 0.0

    # --- one step ---
    def step(self, day: dt.date, ctx, scales: dict, greeks: dict) -> dict:
        """The hedges' P&L since the last step (on today's market), then today's rebalance and its cost."""
        out = {"pnl": 0.0, "costs": 0.0, "carry": 0.0, "option_pnl": 0.0, "hedge_delta": {}, "option_q": 0.0}
        levels = {n: self._level(n, ctx, scales[n], day) for n in self.names}
        opt = self._option_numbers(ctx, scales[self.names[0]], day)
        if self.prev is not None:
            dt_years = year_fraction(self.prev["day"], day)
            for n in self.names:
                move = levels[n] - self.prev["levels"][n]
                out["pnl"] += self.units[n] * self._unit(n) * move
                if self.cfg.instrument == "cash" or n not in INDEX_FUTURES:
                    m = _market(self.prev["ctx"], n)
                    carry = float(m.carry_rate(1 / 12)) + self.prev["spread"]  # r - q, plus the funding spread
                    out["carry"] -= self.units[n] * self.prev["levels"][n] * carry * dt_years
            out["option_pnl"] = self.option_q * (opt["value"] - self.prev["option"]["value"])
            out["pnl"] += out["carry"] + out["option_pnl"]
        # roll a future that comes to its expiry
        if self.cfg.instrument == "future":
            for n in self.names:
                fut = self.future[n]
                if n in INDEX_FUTURES and (fut is None or (fut.expiry - day).days <= ROLL_DAYS):
                    old = self.units[n]
                    nxt = quarterly_expiries(day + dt.timedelta(days=ROLL_DAYS), 1)[0]
                    self.future[n] = Future(n, nxt, 1.0)
                    out["costs"] += 2 * self._cost(n, old, levels[n])  # close and reopen
                    levels[n] = self._level(n, ctx, scales[n], day)
        # the vega hedge first: its delta joins the delta hedge
        if self.cfg.vega and not isinstance(ctx, BasketContext) and self.k % self.cfg.vega_every == 0:
            n = self.names[0]
            vega = sum(float((greeks.get(x) or {}).get("vega", 0.0)) for x in self.names)
            if self.option is None or (self.option.expiry - day).days <= ROLL_DAYS:
                expiry = day + dt.timedelta(days=round(365 * self.cfg.vega_tenor))
                strike = scales[n] * float(ctx.market.F(self.cfg.vega_tenor))
                if self.option is not None:  # unwind the old option at its value, paying its spread
                    out["costs"] += abs(self.option_q) * self.rows["otc_vol_points"] * abs(opt["vega"])
                self.option, self.option_q = Vanilla(n, expiry, round(strike, 2), "call"), 0.0
                opt = self._option_numbers(ctx, scales[n], day)
            target = -vega / opt["vega"] if opt["vega"] else 0.0
            out["costs"] += abs(target - self.option_q) * self.rows["otc_vol_points"] * abs(opt["vega"])
            self.option_q = target
        # the delta hedge
        if self.k % self.cfg.delta_every == 0:
            for n in self.names:
                trade_delta = float((greeks.get(n) or {}).get("delta", 0.0))
                total = trade_delta + (self.option_q * opt["delta"] if n == self.names[0] else 0.0)
                per_unit = self._unit(n) * levels[n] * 0.01
                current = self.units[n] * per_unit
                if abs(total + current) > self.cfg.threshold * abs(trade_delta):
                    target = -total / per_unit if per_unit else 0.0
                    out["costs"] += self._cost(n, target - self.units[n], levels[n])
                    self.units[n] = target
        out["pnl"] -= out["costs"]
        for n in self.names:
            out["hedge_delta"][n] = self.units[n] * self._unit(n) * levels[n] * 0.01
        out["option_q"] = self.option_q
        out["option"] = {k: opt[k] for k in ("delta", "gamma", "vega")}
        out["units"] = dict(self.units)
        self.prev = {"day": day, "levels": levels, "option": opt, "ctx": ctx, "spread": self._funding(day)}
        self.k += 1
        return out


def hedged_explain(row: dict, prev: dict, hedge: dict, prev_hedge: dict, names) -> dict:
    """A step's hedged P&L split: the trade's explain with the hedges' deltas, gammas and vegas, the carry, the costs."""
    ex = dict(row.get("explain") or {})
    out = {"delta": 0.0, "gamma": ex.get("gamma", 0.0), "vega": ex.get("vega", 0.0), "time": ex.get("time", 0.0)}
    for n in names:
        ret = row["spot"][n] / prev["spot"][n] - 1.0
        trade_delta = float((prev.get("greeks", {}).get(n) or {}).get("delta", 0.0))
        out["delta"] += (trade_delta + prev_hedge["hedge_delta"].get(n, 0.0)) * ret / 0.01
        if n == names[0] and prev_hedge.get("option_q"):
            q, o = prev_hedge["option_q"], prev_hedge["option"]
            out["delta"] += q * o["delta"] * ret / 0.01
            out["gamma"] += 0.5 * q * o["gamma"] * (ret / 0.01) ** 2
    out["reserve"] = ex.get("reserve", 0.0)
    out["carry"] = hedge["carry"]
    out["costs"] = -hedge["costs"]
    return out


def summary(daily: list) -> dict:
    """Totals and the spread of the daily P&L, naked and hedged, and the hedged explain summed."""
    steps = [d for d in daily if d.get("pnl") is not None and d.get("hedged_pnl") is not None]
    naked = np.array([d["pnl"] for d in steps], dtype=float)
    hedged = np.array([d["hedged_pnl"] for d in steps], dtype=float)
    parts: dict = {}
    for d in steps:
        for k, v in (d.get("hedged_explain") or {}).items():
            parts[k] = parts.get(k, 0.0) + float(v)
    return {
        "steps": len(steps),
        "naked_total": float(naked.sum()) if len(naked) else 0.0,
        "hedged_total": float(hedged.sum()) if len(hedged) else 0.0,
        "naked_std": float(naked.std(ddof=1)) if len(naked) > 1 else 0.0,
        "hedged_std": float(hedged.std(ddof=1)) if len(hedged) > 1 else 0.0,
        "costs": float(sum(d["hedge"]["costs"] for d in steps)),
        "carry": float(sum(d["hedge"]["carry"] for d in steps)),
        "explain": parts,
        "slippage": float(sum(d.get("hedged_slippage", 0.0) for d in steps)),
    }
